"""The events an endpoint can subscribe to, and what each one carries.

Every payload field is named here on purpose. Serialising "the model" would
send whatever a later migration adds, and some columns must never leave the
server: an invoice's `public_token` is a bearer link to the invoice, for
instance. A field reaches a subscriber only by being listed below.
"""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass

from django.core.serializers.json import DjangoJSONEncoder
from django.db.models import Model

from common import object_registry
from common.modules import available_modules, model_module

PING = "ping"


@dataclass(frozen=True)
class WebhookSpec:
    """A source of events: their prefix, the label both settings screens draw
    its group under, the event names it offers, and its payload builder."""

    prefix: str
    label: str
    events: tuple[str, ...]
    build: Callable[[Model], dict]


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


def interaction_data(interaction):
    return {
        "id": interaction.id,
        "kind": interaction.kind,
        "direction": interaction.direction,
        "occurred_at": interaction.occurred_at,
        "duration_seconds": interaction.duration_seconds,
        "duration_estimated": interaction.duration_estimated,
        "outcome": interaction.outcome,
        "subject": interaction.subject,
        "body": interaction.body,
        "logged_by": interaction.logged_by_id,
        "participants": interaction.participants,
        "home": interaction.home,
        "lead_id": interaction.lead_id,
        "contact_id": interaction.contact_id,
        "account_id": interaction.account_id,
        "opportunity_id": interaction.opportunity_id,
        "ticket_id": interaction.case_id,
        "origin": interaction.origin,
        "external_id": interaction.external_id,
        **_stamps(interaction),
    }


# Named by the registry's entries (`ObjectEntry.webhook`).
LEAD_WEBHOOK = WebhookSpec(
    "lead", "Leads", ("lead.created", "lead.updated", "lead.deleted"), lead_data
)
CONTACT_WEBHOOK = WebhookSpec(
    "contact",
    "Contacts",
    ("contact.created", "contact.updated", "contact.deleted"),
    contact_data,
)
ACCOUNT_WEBHOOK = WebhookSpec(
    "account",
    "Accounts",
    ("account.created", "account.updated", "account.deleted"),
    account_data,
)
DEAL_WEBHOOK = WebhookSpec(
    "deal",
    "Deals",
    ("deal.created", "deal.updated", "deal.deleted", "deal.won", "deal.lost"),
    deal_data,
)
TICKET_WEBHOOK = WebhookSpec(
    "ticket",
    "Tickets",
    ("ticket.created", "ticket.updated", "ticket.comment_added"),
    ticket_data,
)
INVOICE_WEBHOOK = WebhookSpec(
    "invoice",
    "Invoices",
    ("invoice.created", "invoice.updated", "invoice.paid"),
    invoice_data,
)
TASK_WEBHOOK = WebhookSpec(
    "task", "Tasks", ("task.created", "task.updated", "task.deleted"), task_data
)

# Webhook sources that are not record objects, so the registry does not list
# them: (model label, spec). A ticket reply offers no group of its own; its
# event is in TICKET_WEBHOOK's group, whose module it follows.
NON_OBJECT_WEBHOOKS = (
    (
        "interactions.Interaction",
        WebhookSpec(
            "interaction", "Interactions", ("interaction.created",), interaction_data
        ),
    ),
    ("common.Comment", WebhookSpec("ticket", "Tickets", (), comment_data)),
)


def built_in_sources():
    """(model label, spec) of every built-in source, whatever its module: the
    registry's entries, then NON_OBJECT_WEBHOOKS."""
    return [
        *(
            (e.model_label, spec)
            for e in object_registry.BUILT_IN
            if (spec := e.webhook_spec()) is not None
        ),
        *NON_OBJECT_WEBHOOKS,
    ]


def _groups(org):
    """The specs `org` is offered, in catalogue order: the registry's built-in
    entries, NON_OBJECT_WEBHOOKS, then hook entries. With org None, every
    built-in group and no hook's."""
    built_in = {e.key for e in object_registry.BUILT_IN}
    entries = object_registry.all_objects(org)
    shown = None if org is None else {None, *available_modules(org)}
    specs = [
        *(e.webhook_spec() for e in entries if e.key in built_in),
        *(
            spec
            for label, spec in NON_OBJECT_WEBHOOKS
            if shown is None or model_module(label) in shown
        ),
        *(e.webhook_spec() for e in entries if e.key not in built_in),
    ]
    return [spec for spec in specs if spec is not None and spec.events]


def catalogue_json(org=None):
    return [
        {"module": spec.prefix, "label": spec.label, "events": list(spec.events)}
        for spec in _groups(org)
    ]


def event_names(org=None) -> tuple[str, ...]:
    """Every event `org` may subscribe to, in catalogue order."""
    return tuple(name for spec in _groups(org) for name in spec.events)


def _catalogue():
    return tuple(
        (spec.prefix, spec.label, label, spec.events)
        for label, spec in built_in_sources()
        if spec.events
    )


def _event_modules():
    """Event prefix to the module it belongs to; None for a core one."""
    return {prefix: model_module(label) for prefix, _, label, _ in _catalogue()}


def prefix_module(prefix):
    """The module an event prefix belongs to: None for a core prefix, and for
    one no built-in group has."""
    return _event_modules().get(prefix)


def event_module(event):
    return prefix_module(event.partition(".")[0])


def __getattr__(name):
    # The built-in groups as `(prefix, label, model label, events)` and each
    # prefix's module, under their names from before the registry, which only
    # `tests/test_module_off.py` still reads. Read on use: the registry names
    # this module's specs, so it cannot load with it.
    if name == "CATALOGUE":
        return _catalogue()
    if name == "EVENT_MODULES":
        return _event_modules()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def to_json(data):
    """Plain JSON types only: UUIDs, Decimals and dates become strings."""
    return json.loads(json.dumps(data, cls=DjangoJSONEncoder))


def _escape_slack(text):
    # Slack reads <...> as a link or a mention, so a lead named "<!channel>"
    # submitted through a public web form would ping the whole channel.
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


_TEAMS_MARKUP = re.compile(r"([\\*_\[\]`<>])")


def _escape_teams(text):
    # An Adaptive Card's TextBlock reads markdown, so a lead named
    # "[Pay now](https://...)" would be a link in the channel. One line, so
    # nothing in a name can start a list.
    return _TEAMS_MARKUP.sub(r"\\\1", " ".join(str(text).split()))


def _summary(payload, escape):
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
    return f"BottleCRM: {what}: {escape(subject)}"


def slack_text(payload):
    """One line for a Slack incoming webhook, from a delivery payload."""
    return _summary(payload, _escape_slack)


def teams_text(payload):
    """The same line for a Microsoft Teams Workflows webhook."""
    return _summary(payload, _escape_teams)


def teams_card(payload):
    """A Teams Workflows "post to a channel" message: one Adaptive Card."""
    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": [
                        {"type": "TextBlock", "text": teams_text(payload), "wrap": True}
                    ],
                },
            }
        ],
    }
