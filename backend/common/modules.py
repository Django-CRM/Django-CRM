"""The module catalogue: ten parts of the CRM an org admin can switch off.

State lives in three ``Org`` fields (``modules_off``, ``module_order``,
``modules_on_at``). Everything here reads them defensively: unknown or
malformed stored values are ignored, never raised on.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

import crum
from django.http import JsonResponse
from rest_framework.response import Response


@dataclass(frozen=True)
class Module:
    key: str
    label: str
    group: str
    covers: str
    stops: str
    term: str | None = None
    needs: str | None = None


GROUPS = ("sell", "serve", "bill")

MODULES = (
    Module(
        key="deals",
        label="Pipeline",
        term="opportunity.plural",
        group="sell",
        covers="Deals, deal pipelines, line items, the deal board and stage aging.",
        stops=(
            "Everything in {label} leaves every menu, search and Today, and stale-deal emails stop. "
            "Converting a lead no longer creates a deal, and Goals switches off too."
        ),
    ),
    Module(
        key="leads",
        label="Leads",
        term="lead.plural",
        group="sell",
        covers="Leads, lead pipelines, and lead import and export.",
        stops="{label} leave every menu, search and Today.",
    ),
    Module(
        key="goals",
        label="Goals",
        group="sell",
        needs="deals",
        covers="Sales goals, the leaderboard and goal history.",
        stops="{label} leave every menu, and goal milestone emails stop.",
    ),
    Module(
        key="tasks",
        label="Tasks",
        group="serve",
        covers="Tasks, boards, the task calendar and its calendar feed.",
        stops=(
            "{label} leave every menu and Today, calendar feeds stop updating, "
            "and automation rules skip their Create a task steps."
        ),
    ),
    Module(
        key="tickets",
        label="Tickets",
        group="serve",
        covers=(
            "Tickets, approvals, ticket analytics, routing, escalation, the reopen "
            "policy, business hours, saved replies, inbound email, CSAT and portal "
            "tickets."
        ),
        stops=(
            "{label} leave every menu and Today, SLA escalation stops, CSAT survey "
            "links stop opening, and customers stop seeing their tickets in the "
            "portal."
        ),
    ),
    Module(
        key="knowledge_base",
        label="Knowledge base",
        group="serve",
        covers="Articles, the public help center and portal articles.",
        stops=(
            "Articles leave every menu, the public help center goes offline, and "
            "the portal stops showing articles."
        ),
    ),
    Module(
        key="documents",
        label="Documents",
        group="serve",
        covers="Documents and their downloads.",
        stops="{label} leave every menu, and their downloads stop opening.",
    ),
    Module(
        key="projects",
        label="Projects",
        group="serve",
        covers=(
            "Projects, their people, rates and budgets, tasks and time on them, "
            "and billing a project's time."
        ),
        stops=(
            "{label} leave every menu, time can no longer be logged on them, and "
            "budget notifications stop."
        ),
    ),
    Module(
        key="invoices",
        label="Invoices",
        term="invoice.plural",
        group="bill",
        covers=(
            "Invoices, estimates, recurring invoices, invoice templates, invoice "
            "reports, account statements, and public invoice and estimate links."
        ),
        stops=(
            "Recurring invoices stop being sent, and links you have already sent to "
            "customers stop opening. Recurring invoices that fall due while {label} "
            "is off are not sent later. Payment reminders that fall due while "
            "{label} is off are not sent later."
        ),
    ),
    Module(
        key="timesheet",
        label="Timesheet",
        group="bill",
        covers="Time entries, the timesheet and the time report.",
        stops=(
            "Time entries leave every menu, ticket and project, and running timers "
            "are no longer stopped automatically."
        ),
    ),
)

KEYS = tuple(m.key for m in MODULES)
_BY_KEY = {m.key: m for m in MODULES}

# LeadSource is left unplaced: deals and contacts carry its keys too.
_LEADS = (
    "Lead",
    "LeadPipeline",
    "LeadStage",
    "LeadIntake",
    "LeadIntakeEvent",
)
_DEALS = ("DealPipeline", "DealStage", "Opportunity", "OpportunityLineItem")
_TASKS = (
    "Board",
    "BoardMember",
    "BoardColumn",
    "BoardTask",
    "TaskPipeline",
    "TaskStage",
    "Task",
)
_CASES = (
    "Case", "CaseWatcher", "CsatSurvey", "CasePipeline", "CaseStage", "ReopenPolicy",
    "EscalationPolicy", "InboundMailbox", "EmailMessage", "RoutingRule",
    "RoutingRuleState", "ApprovalRule", "Approval",
)  # fmt: skip
_INVOICES = (
    "InvoiceTemplate", "Invoice", "InvoiceLineItem", "Payment", "Estimate",
    "EstimateLineItem", "RecurringInvoice", "RecurringInvoiceLineItem",
    "InvoiceHistory",
)  # fmt: skip

# invoices.Product is absent on purpose: it follows deals or invoices.
MODEL_MODULES: dict[str, str] = {
    **{f"leads.{n}": "leads" for n in _LEADS},
    **{f"opportunity.{n}": "deals" for n in _DEALS},
    "opportunity.SalesGoal": "goals",
    **{f"tasks.{n}": "tasks" for n in _TASKS},
    **{f"cases.{n}": "tickets" for n in _CASES},
    "cases.Solution": "knowledge_base",
    "cases.TimeEntry": "timesheet",
    "business_hours.BusinessCalendar": "tickets",
    "business_hours.BusinessHoliday": "tickets",
    "macros.Macro": "tickets",
    **{f"invoices.{n}": "invoices" for n in _INVOICES},
    "common.Document": "documents",
    "projects.Project": "projects",
    "projects.ProjectRate": "projects",
}


def module(key):
    """The catalogue entry for ``key``; ``KeyError`` when unknown."""
    return _BY_KEY[key]


def model_module(label: str) -> str | None:
    return MODEL_MODULES.get(label)


def enabled_modules(org) -> frozenset[str]:
    off = org.modules_off
    stored = {k for k in off if isinstance(k, str)} if isinstance(off, list) else set()
    return frozenset(KEYS) - stored


def available_modules(org) -> frozenset[str]:
    enabled = enabled_modules(org)
    return frozenset(
        k for k in enabled if _BY_KEY[k].needs is None or _BY_KEY[k].needs in enabled
    )


def module_is_available(org, key: str) -> bool:
    return key in available_modules(org)


def module_label(org, key) -> str:
    m = _BY_KEY[key]
    terms = org.terminology
    if m.term and isinstance(terms, dict):
        value = terms.get(m.term)
        if isinstance(value, str) and value:
            return value
    return m.label


def module_off_message(org, key) -> str:
    return f"{module_label(org, key)} is switched off for this organization."


def module_off_body(org, key) -> dict:
    return {
        "detail": module_off_message(org, key),
        "code": "module_off",
        "module": key,
    }


def module_off_response(org, key) -> Response:
    return Response(module_off_body(org, key), status=404)


def ordered_modules(org) -> list[Module]:
    stored = org.module_order if isinstance(org.module_order, list) else []
    result = []
    for group in GROUPS:
        members = [m for m in MODULES if m.group == group]
        first = [
            _BY_KEY[k]
            for k in dict.fromkeys(k for k in stored if isinstance(k, str))
            if k in _BY_KEY and _BY_KEY[k].group == group
        ]
        result += first + [m for m in members if m not in first]
    return result


def module_on_since(org, key) -> datetime | None:
    stamps = org.modules_on_at
    raw = stamps.get(key) if isinstance(stamps, dict) else None
    if not isinstance(raw, str):
        return None
    try:
        when = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=UTC)


def only_available(org, key, queryset):
    return queryset if module_is_available(org, key) else queryset.none()


def org_of(record):
    """The request's org when it is the record's, so a serializer does not
    query once per row."""
    request = crum.get_current_request()
    org = getattr(getattr(request, "profile", None), "org", None)
    if org is not None and org.id == record.org_id:
        return org
    return record.org


@dataclass(frozen=True)
class Requires:
    """The modules a route needs. ``all_of`` lists the needed module first, so
    the refusal names the module that is actually off."""

    all_of: tuple[str, ...] = ()
    any_of: tuple[str, ...] = ()

    def blocking(self, available: frozenset[str]) -> str | None:
        for key in self.all_of:
            if key not in available:
                return key
        if self.any_of and not available.intersection(self.any_of):
            return self.any_of[0]
        return None


CORE = Requires()

NAMESPACE_MODULES: dict[str, Requires] = {
    "api_leads": Requires(("leads",)),
    "api_opportunities": Requires(("deals",)),
    "api_tasks": Requires(("tasks",)),
    "api_boards": Requires(("tasks",)),
    "api_cases": Requires(("tickets",)),
    "api_business_hours": Requires(("tickets",)),
    "api_macros": Requires(("tickets",)),
    # Time sits on tickets or projects, so the entry routes need either.
    "api_time_entries": Requires(("timesheet",), any_of=("tickets", "projects")),
    "api_projects": Requires(("projects",)),
    "api_invoices": Requires(("invoices",)),
    "public_help": Requires(("knowledge_base",)),
    "public_invoices": Requires(("invoices",)),
    "public_leads": Requires(("leads",)),
    "api_accounts": CORE,
    "api_contacts": CORE,
    "api_webforms": CORE,
    "api_webhooks": CORE,
    "api_automation": CORE,
    # Each request is checked against its record's module in the view.
    "api_interactions": CORE,
    "public_webforms": CORE,
}

# Keyed by ``ResolverMatch.route`` prefix; the longest match wins over the
# namespace. ``common_urls``, ``api_common`` and ``portal`` mix core and module
# routes, so they have no namespace entry: each of their roots is listed here,
# and a new root there stays unclassified.
ROUTE_MODULES: dict[str, Requires] = {
    "api/opportunities/goals/": Requires(("deals", "goals")),
    "api/cases/solutions/": Requires(("knowledge_base",)),
    "api/cases/<uid:pk>/solutions/": Requires(("tickets", "knowledge_base")),
    "api/cases/<uid:pk>/solution-suggestions/": Requires(("tickets", "knowledge_base")),
    "api/cases/<uid:pk>/time-entries/": Requires(("tickets", "timesheet")),
    "api/cases/<uid:pk>/time-summary/": Requires(("tickets", "timesheet")),
    # A mailbox makes tickets or leads; each webhook checks its own in the view.
    "api/cases/mailboxes/": Requires(any_of=("tickets", "leads")),
    "api/cases/inbound/": Requires(any_of=("tickets", "leads")),
    "api/invoices/products/": Requires(any_of=("invoices", "deals")),
    "api/invoices/from-opportunity/": Requires(("invoices", "deals")),
    # The view also needs Tickets to bill an account, Projects to bill a project.
    "api/invoices/from-time-entries/": Requires(
        ("invoices", "timesheet"), any_of=("tickets", "projects")
    ),
    "api/projects/<uid:pk>/time-entries/": Requires(("projects", "timesheet")),
    "api/projects/<uid:pk>/unbilled/": Requires(("projects", "invoices", "timesheet")),
    "api/projects/<uid:pk>/invoice-amount/": Requires(("projects", "invoices")),
    "api/projects/<uid:pk>/retainer/": Requires(("projects", "invoices")),
    "api/projects/from-deal/": Requires(("projects", "deals")),
    "api/leads/sources/": CORE,
    "api/documents/": Requires(("documents",)),
    "api/profile/calendar-feed/": Requires(("tasks",)),
    "api/public/calendar/": Requires(("tasks",)),
    "api/public/csat/": Requires(("tickets",)),
    "api/org/help-center/": Requires(("knowledge_base",)),
    "api/portal/cases/": Requires(("tickets",)),
    "api/portal/articles/": Requires(("knowledge_base",)),
    "api/portal/login/": CORE,
    "api/schema/": CORE,
    "api/activities/": CORE,
    "api/api-settings/": CORE,
    "api/apps/": CORE,
    "api/attachments/": CORE,
    "api/auth/": CORE,
    "api/custom-fields/": CORE,
    "api/dashboard/": CORE,
    "api/devices/": CORE,
    "api/notifications/": CORE,
    "api/org/": CORE,
    "api/packs/": CORE,
    "api/profile/": CORE,
    "api/saved-views/": CORE,
    "api/search/": CORE,
    "api/tags/": CORE,
    "api/teams/": CORE,
    "api/user/": CORE,
    "api/users/": CORE,
}


def requirement_for(route: str, namespaces: Sequence[str]) -> Requires | None:
    """What ``route`` needs: the longest ``ROUTE_MODULES`` prefix it starts
    with, else the entry for its innermost namespace, else None (unclassified).

    Outer namespaces are never consulted, so an app mounted under a new
    namespace inside ``common_urls`` stays unclassified instead of turning core.
    """
    prefixes = [p for p in ROUTE_MODULES if route.startswith(p)]
    if prefixes:
        return ROUTE_MODULES[max(prefixes, key=len)]
    return NAMESPACE_MODULES.get(namespaces[-1]) if namespaces else None


# Module routes ``route_refusal`` never sees (exempt paths, the portal, exempt
# views); each checks its module in its view.
VIEW_CHECKED_ROUTES: frozenset[str] = frozenset(
    {
        "api/public/csat/<str:token>/",
        "api/public/help/<str:slug>/",
        "api/public/help/<str:slug>/articles/<uid:pk>/",
        "api/public/invoice/<str:token>/",
        "api/public/invoice/<str:token>/pdf/",
        "api/public/estimate/<str:token>/",
        "api/public/estimate/<str:token>/pdf/",
        "api/public/estimate/<str:token>/accept/",
        "api/public/estimate/<str:token>/decline/",
        "api/public/calendar/<str:token>.ics",
        "api/org/help-center/",
        "api/portal/articles/",
        "api/portal/articles/suggest/",
        "api/portal/articles/<uid:pk>/",
        "api/portal/cases/",
        "api/portal/cases/<uid:pk>/",
        "api/portal/cases/<uid:pk>/comment/",
        "api/cases/inbound/<uid:mailbox_id>/",
        "api/cases/inbound/email/",
        "api/public/leads/<uid:intake_id>/",
    }
)


def route_refusal(request, match) -> JsonResponse | None:
    """The 404 for a signed-in request (session, token or org key) to a route
    whose module is off. The portal carries an org but no profile, so it and
    anonymous callers pass here and are checked in their views."""
    org = getattr(request, "org", None)
    if org is None or getattr(request, "profile", None) is None:
        return None
    requires = requirement_for(match.route, match.namespaces)
    key = requires.blocking(available_modules(org)) if requires else None
    if key is None:
        return None
    return JsonResponse(module_off_body(org, key), status=404)
