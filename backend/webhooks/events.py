"""The events an endpoint can subscribe to, and what each one carries.

Every payload field is named here on purpose. Serialising "the model" would
send whatever a later migration adds, and some columns must never leave the
server: an invoice's `public_token` is a bearer link to the invoice, for
instance. A field reaches a subscriber only by being listed below.
"""

import json

from django.core.serializers.json import DjangoJSONEncoder

PING = "ping"

# Grouped the way both settings screens draw their checkboxes.
CATALOGUE = (
    ("lead", "Leads", ("lead.created", "lead.updated", "lead.deleted")),
    ("contact", "Contacts", ("contact.created", "contact.updated", "contact.deleted")),
    ("account", "Accounts", ("account.created", "account.updated", "account.deleted")),
    (
        "deal",
        "Deals",
        ("deal.created", "deal.updated", "deal.deleted", "deal.won", "deal.lost"),
    ),
    ("ticket", "Tickets", ("ticket.created", "ticket.updated", "ticket.comment_added")),
    ("invoice", "Invoices", ("invoice.created", "invoice.updated", "invoice.paid")),
    ("task", "Tasks", ("task.created", "task.updated", "task.deleted")),
)

EVENT_NAMES = tuple(name for _, _, names in CATALOGUE for name in names)


def catalogue_json():
    return [
        {"module": module, "label": label, "events": list(names)}
        for module, label, names in CATALOGUE
    ]


def _assigned(instance):
    # Filtered to the record's own org. The M2M does not enforce that, so a
    # profile id from elsewhere is never passed on even if one got attached.
    return [
        str(pk)
        for pk in instance.assigned_to.filter(org_id=instance.org_id).values_list(
            "id", flat=True
        )
    ]


def _stamps(instance):
    return {"created_at": instance.created_at, "updated_at": instance.updated_at}


def lead_data(lead):
    return {
        "id": lead.id,
        "title": lead.title,
        "first_name": lead.first_name,
        "last_name": lead.last_name,
        "email": lead.email,
        "phone": lead.phone,
        "company_name": lead.company_name,
        "status": lead.status,
        "source": lead.source,
        "stage_id": lead.stage_id,
        "opportunity_amount": lead.opportunity_amount,
        "currency": lead.currency,
        "close_date": lead.close_date,
        "assigned_to": _assigned(lead),
        **_stamps(lead),
    }


def contact_data(contact):
    return {
        "id": contact.id,
        "first_name": contact.first_name,
        "last_name": contact.last_name,
        "email": contact.email,
        "phone": contact.phone,
        "organization": contact.organization,
        "title": contact.title,
        "account_id": contact.account_id,
        "assigned_to": _assigned(contact),
        **_stamps(contact),
    }


def account_data(account):
    return {
        "id": account.id,
        "name": account.name,
        "email": account.email,
        "phone": account.phone,
        "website": account.website,
        "industry": account.industry,
        "annual_revenue": account.annual_revenue,
        "currency": account.currency,
        "assigned_to": _assigned(account),
        **_stamps(account),
    }


def deal_data(deal):
    return {
        "id": deal.id,
        "name": deal.name,
        "pipeline_id": deal.pipeline_id,
        "stage": deal.stage,
        "amount": deal.amount,
        "currency": deal.currency,
        "probability": deal.probability,
        "closed_on": deal.closed_on,
        "account_id": deal.account_id,
        "assigned_to": _assigned(deal),
        **_stamps(deal),
    }


def ticket_data(case):
    return {
        "id": case.id,
        "name": case.name,
        "status": case.status,
        "priority": case.priority,
        "case_type": case.case_type,
        "account_id": case.account_id,
        "closed_on": case.closed_on,
        "assigned_to": _assigned(case),
        **_stamps(case),
    }


def comment_data(comment):
    return {
        "id": comment.id,
        "ticket_id": comment.object_id,
        "comment": comment.comment,
        "commented_by": comment.commented_by_id,
        "commented_by_contact": comment.commented_by_contact_id,
        "created_at": comment.created_at,
    }


def invoice_data(invoice):
    return {
        "id": invoice.id,
        "invoice_number": invoice.invoice_number,
        "invoice_title": invoice.invoice_title,
        "status": invoice.status,
        "total_amount": invoice.total_amount,
        "amount_paid": invoice.amount_paid,
        "amount_due": invoice.amount_due,
        "currency": invoice.currency,
        "issue_date": invoice.issue_date,
        "due_date": invoice.due_date,
        "paid_at": invoice.paid_at,
        "account_id": invoice.account_id,
        "contact_id": invoice.contact_id,
        "assigned_to": _assigned(invoice),
        **_stamps(invoice),
    }


def task_data(task):
    return {
        "id": task.id,
        "title": task.title,
        "status": task.status,
        "priority": task.priority,
        "due_date": task.due_date,
        "account_id": task.account_id,
        "opportunity_id": task.opportunity_id,
        "ticket_id": task.case_id,
        "lead_id": task.lead_id,
        "assigned_to": _assigned(task),
        **_stamps(task),
    }


def to_json(data):
    """Plain JSON types only: UUIDs, Decimals and dates become strings."""
    return json.loads(json.dumps(data, cls=DjangoJSONEncoder))


def _escape_slack(text):
    # Slack reads <...> as a link or a mention, so a lead named "<!channel>"
    # submitted through a public web form would ping the whole channel.
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def slack_text(payload):
    """One line for a Slack incoming webhook, from a delivery payload."""
    event = payload["event"]
    if event == PING:
        return "BottleCRM test message: this webhook is connected."
    data = payload.get("data") or {}
    subject = (
        data.get("name")
        or data.get("title")
        or data.get("invoice_number")
        or " ".join(
            part for part in (data.get("first_name"), data.get("last_name")) if part
        )
        or (data.get("comment") or "")[:200]
        or data.get("id")
        or ""
    )
    module, _, action = event.partition(".")
    what = f"{module} {action.replace('_', ' ')}".capitalize()
    return f"BottleCRM: {what}: {_escape_slack(subject)}"
