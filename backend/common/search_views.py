"""Org-scoped global search behind ``GET /api/search/?q=``, the ⌘K palette.

ONE endpoint, deliberately. The org comes from the JWT (`request.profile.org`)
exactly once and every queryset is filtered by it; a per-model fan-out from the
browser would be as many chances to read another tenant's rows.

Each type also honours the SAME read rule as its own detail view, by calling
that module's visibility helper rather than restating it, so search can never
surface a record the caller could not open, or hide one they could:

* leads ``visible_leads_qs``, deals ``visible_deals_qs``, accounts
  ``visible_accounts_qs``, invoices ``visible_invoices_qs``: admins and Django
  superusers see the whole org, everyone else what they created or were
  assigned.
* contacts ``visible_contacts_qs``: the same, plus contacts at an account the
  caller is assigned to.
* tickets ``visible_cases_qs``: admins, creator, assignees and watchers. No
  superuser clause, because the ticket detail view has none.
* knowledge-base articles. Org-wide, every member reads them.

Matching stays on the server; the whole record set never reaches the browser.
"""

from django.db.models import Q
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.access import visible_accounts_qs
from cases.access import visible_cases_qs
from cases.models import Solution
from common.permissions import HasOrgContext
from contacts.access import visible_contacts_qs
from invoices.permissions import visible_invoices_qs
from leads.access import visible_leads_qs
from opportunity.access import visible_deals_qs
from opportunity.stages import stage_index

# Rows per type. Small on purpose. The palette shows a handful per group and
# the point is the fastest match, not an exhaustive report.
PER_TYPE = 6
# One-character queries match almost everything; wait for a second character.
MIN_QUERY = 2


class GlobalSearchView(APIView):
    """``GET /api/search/?q=<query>``. A handful of matches per record type."""

    permission_classes = (IsAuthenticated, HasOrgContext)

    def get(self, request):
        q = (request.query_params.get("q") or "").strip()
        if len(q) < MIN_QUERY:
            return Response({"query": q, "results": []})

        profile = request.profile
        org = profile.org
        user = request.user
        results = []

        # Leads
        leads = visible_leads_qs(profile, user).filter(
            Q(title__icontains=q)
            | Q(first_name__icontains=q)
            | Q(last_name__icontains=q)
            | Q(email__icontains=q)
            | Q(company_name__icontains=q)
        )[:PER_TYPE]
        for lead in leads:
            name = lead.title or f"{lead.first_name} {lead.last_name}".strip()
            results.append(
                {
                    "type": "lead",
                    "id": str(lead.id),
                    "title": name or lead.email or "Untitled lead",
                    "subtitle": lead.company_name or lead.email or "",
                }
            )

        # Deals (Opportunity)
        deals = (
            visible_deals_qs(profile, user)
            .select_related("account")
            .filter(
                Q(name__icontains=q)
                | Q(description__icontains=q)
                | Q(account__name__icontains=q)
            )[:PER_TYPE]
        )
        # The label, not the code: stages are configurable per pipeline. One
        # read of the org's stages serves every row.
        deals = list(deals)
        stages = stage_index(profile.org_id) if deals else {}
        for deal in deals:
            stage = deal.current_stage(stages)
            results.append(
                {
                    "type": "deal",
                    "id": str(deal.id),
                    "title": deal.name,
                    "subtitle": (deal.account.name if deal.account_id else "")
                    or (stage.label if stage else deal.stage)
                    or "",
                }
            )

        # Accounts
        accounts = visible_accounts_qs(profile, user).filter(
            Q(name__icontains=q)
            | Q(email__icontains=q)
            | Q(website__icontains=q)
            | Q(industry__icontains=q)
        )[:PER_TYPE]
        for account in accounts:
            results.append(
                {
                    "type": "account",
                    "id": str(account.id),
                    "title": account.name,
                    "subtitle": account.email or account.industry or "",
                }
            )

        # Contacts
        contacts = visible_contacts_qs(profile).filter(
            Q(first_name__icontains=q)
            | Q(last_name__icontains=q)
            | Q(email__icontains=q)
            | Q(organization__icontains=q)
        )[:PER_TYPE]
        for contact in contacts:
            name = f"{contact.first_name} {contact.last_name}".strip()
            results.append(
                {
                    "type": "contact",
                    "id": str(contact.id),
                    "title": name or contact.email or "Unnamed contact",
                    "subtitle": contact.organization or contact.email or "",
                }
            )

        # Tickets (Case): the module's own read-visibility helper
        cases = (
            visible_cases_qs(profile)
            .select_related("account")
            .filter(
                Q(name__icontains=q)
                | Q(description__icontains=q)
                | Q(account__name__icontains=q)
            )[:PER_TYPE]
        )
        for case in cases:
            results.append(
                {
                    "type": "ticket",
                    "id": str(case.id),
                    "title": case.name,
                    "subtitle": (case.account.name if case.account_id else "")
                    or case.status
                    or "",
                }
            )

        # Invoices
        invoices = (
            visible_invoices_qs(profile, user)
            .select_related("account")
            .filter(
                Q(invoice_number__icontains=q)
                | Q(invoice_title__icontains=q)
                | Q(client_name__icontains=q)
                | Q(account__name__icontains=q)
            )[:PER_TYPE]
        )
        for invoice in invoices:
            results.append(
                {
                    "type": "invoice",
                    "id": str(invoice.id),
                    "title": invoice.invoice_number
                    or invoice.invoice_title
                    or "Invoice",
                    "subtitle": invoice.client_name
                    or (invoice.account.name if invoice.account_id else "")
                    or invoice.status
                    or "",
                }
            )

        # Knowledge base (Solution): org-wide, every member reads
        solutions = Solution.objects.filter(org=org).filter(
            Q(title__icontains=q) | Q(description__icontains=q)
        )[:PER_TYPE]
        for solution in solutions:
            results.append(
                {
                    "type": "solution",
                    "id": str(solution.id),
                    "title": solution.title,
                    "subtitle": solution.status or "",
                }
            )

        return Response({"query": q, "results": results})
