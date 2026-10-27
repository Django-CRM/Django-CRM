"""Webhooks follow modules: the catalogue, what an endpoint may subscribe to,
and what fires."""

from unittest import mock

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from cases.models import Case
from common.testing import set_modules_off
from leads.models import Lead
from webhooks import events
from webhooks.emit import SPECS
from webhooks.models import WebhookDelivery, WebhookEndpoint

URL = "/api/webhooks/"
TICKETS_OFF = "Tickets is switched off for this organization."


@pytest.fixture(autouse=True)
def no_broker():
    with mock.patch("webhooks.emit.deliver_webhook.delay") as delay:
        yield delay


@pytest.fixture(autouse=True)
def _public(public_dns):
    return public_dns


def _groups(client):
    return {g["module"] for g in client.get(URL).json()["event_catalogue"]}


def test_the_catalogue_leaves_out_off_modules(admin_client, org_a):
    assert {"lead", "ticket", "invoice", "deal", "task"} <= _groups(admin_client)
    set_modules_off(org_a, "tickets", "invoices")
    groups = _groups(admin_client)
    assert not {"ticket", "invoice"} & groups
    assert {"lead", "deal", "task", "contact", "account"} <= groups


def test_each_event_group_names_the_model_that_emits_it():
    """A group follows the module of the model it names, so that must be the
    model whose saves emit the group. A comment emits its ticket's group."""
    emitted = {
        prefix: label
        for label, (prefix, _) in SPECS.items()
        if label != "common.comment"
    }
    named = {prefix: model.lower() for prefix, _, model, _ in events.CATALOGUE}
    assert named == emitted
    assert events.EVENT_MODULES == {
        "lead": "leads",
        "contact": None,
        "account": None,
        "deal": "deals",
        "ticket": "tickets",
        "invoice": "invoices",
        "task": "tasks",
        "interaction": None,
    }


def test_editing_an_endpoint_keeps_its_off_module_events(
    admin_client, org_a, admin_profile
):
    hook = WebhookEndpoint.objects.create(
        org=org_a,
        url="https://hooks.example.com/in",
        events=["lead.created", "ticket.created"],
        created_by=admin_profile.user,
    )
    set_modules_off(org_a, "tickets")
    response = admin_client.patch(
        f"{URL}{hook.id}/", {"events": ["lead.updated"]}, format="json"
    )
    assert response.status_code == 200, response.content
    hook.refresh_from_db()
    assert hook.events == ["lead.updated", "ticket.created"]


def test_subscribing_to_an_off_module_event_is_refused(admin_client, org_a):
    set_modules_off(org_a, "tickets")
    response = admin_client.post(
        URL,
        {"url": "https://hooks.example.com/in", "events": ["ticket.created"]},
        format="json",
    )
    assert response.status_code == 400
    assert response.json()["events"] == [TICKETS_OFF]
    ok = admin_client.post(
        URL,
        {"url": "https://hooks.example.com/in", "events": ["lead.created"]},
        format="json",
    )
    assert ok.status_code == 201


def test_nothing_fires_for_an_off_module(org_a, django_capture_on_commit_callbacks):
    hook = WebhookEndpoint.objects.create(
        org=org_a,
        url="https://hooks.example.com/in",
        events=["lead.created", "ticket.created"],
    )
    set_modules_off(org_a, "tickets")
    with django_capture_on_commit_callbacks(execute=True):
        Case.objects.create(name="T", status="New", priority="Normal", org=org_a)
    assert not WebhookDelivery.objects.filter(endpoint=hook).exists()
    with django_capture_on_commit_callbacks(execute=True):
        Lead.objects.create(org=org_a, title="Hi", first_name="Ada")
    assert list(
        WebhookDelivery.objects.filter(endpoint=hook).values_list("event", flat=True)
    ) == ["lead.created"]


def test_sending_reads_modules_without_the_old_names(
    monkeypatch, org_a, django_capture_on_commit_callbacks
):
    """`EVENT_MODULES` is kept for the test above that names it; sending reads
    `prefix_module`."""

    def no_old_names(name):
        raise AttributeError(name)

    monkeypatch.setattr(events, "__getattr__", no_old_names)
    assert events.prefix_module("ticket") == "tickets"
    assert events.prefix_module("contact") is None
    assert events.prefix_module("obj:site") is None
    hook = WebhookEndpoint.objects.create(
        org=org_a,
        url="https://hooks.example.com/in",
        events=["lead.created", "ticket.created"],
    )
    set_modules_off(org_a, "tickets")
    with django_capture_on_commit_callbacks(execute=True):
        Case.objects.create(name="T", status="New", priority="Normal", org=org_a)
        Lead.objects.create(org=org_a, title="Hi", first_name="Ada")
    assert list(
        WebhookDelivery.objects.filter(endpoint=hook).values_list("event", flat=True)
    ) == ["lead.created"]


def test_an_org_with_no_endpoints_pays_no_module_lookup(org_a):
    from webhooks.emit import _publish_entry

    entry = mock.Mock(org_id=org_a.id, model=Lead)
    with CaptureQueriesContext(connection) as queries:
        _publish_entry(entry, {}, {})
    assert len(queries) == 1
    assert "webhook" in queries[0]["sql"].lower()
