"""The webhook catalogue, endpoint validation and the emitter read the object
registry, so a hook entry's events join them with no list kept here."""

import dataclasses
import types
from unittest import mock

import pytest

from common import object_registry
from common.models import Document
from webhooks import emit, events, signals
from webhooks.events import WebhookSpec
from webhooks.models import WebhookDelivery, WebhookEndpoint

URL = "/api/webhooks/"
HOOK_URL = "https://hooks.example.com/in"
SITE_EVENTS = ("obj:site.created", "obj:site.updated", "obj:site.deleted")


def _site_data(document):
    return {"id": document.id, "object": "site", "name": document.title}


SITE = WebhookSpec(
    prefix="obj:site", label="Sites", events=SITE_EVENTS, build=_site_data
)


@dataclasses.dataclass(frozen=True)
class SiteEntry:
    key: str = "obj:site"
    custom_field_target: str = "obj:site"
    label: str = "Site"
    label_plural: str = "Sites"

    def webhook_spec(self):
        return SITE


@pytest.fixture(autouse=True)
def _dns_and_broker(public_dns):
    with mock.patch("webhooks.emit.deliver_webhook.delay"):
        yield


@pytest.fixture
def site_object(monkeypatch, org_a):
    """Org A's registry holds the site object while `state["active"]` is set."""
    state = {"active": True}

    def hook(org):
        if state["active"] and org is not None and org.pk == org_a.pk:
            return (SiteEntry(),)
        return ()

    monkeypatch.setattr(object_registry, "_HOOKS", [hook])
    return state


def _modules(client):
    return [g["module"] for g in client.get(URL).json()["event_catalogue"]]


def _deliveries(endpoint):
    return list(
        WebhookDelivery.objects.filter(endpoint=endpoint)
        .order_by("created_at")
        .values_list("event", "payload__data")
    )


def test_catalogue_comes_from_the_registry(monkeypatch, admin_client, org_a):
    with_spec = [e.webhook_spec() for e in object_registry.BUILT_IN if e.webhook]
    assert _modules(admin_client) == [s.prefix for s in with_spec] + ["interaction"]
    assert events.event_names(org_a) == events.event_names()

    monkeypatch.setattr(
        object_registry,
        "BUILT_IN",
        tuple(
            dataclasses.replace(e, webhook=None) if e.key == "task" else e
            for e in object_registry.BUILT_IN
        ),
    )
    assert "task" not in _modules(admin_client)
    assert "task.created" not in events.event_names(org_a)
    assert "task.created" not in events.event_names()
    refused = admin_client.post(
        URL, {"url": HOOK_URL, "events": ["task.created"]}, format="json"
    )
    assert refused.status_code == 400
    assert refused.json()["events"] == ["Unknown event: task.created."]


def test_receivers_follow_the_registry(monkeypatch):
    assert set(signals.saved_labels()) == {
        "leads.Lead",
        "contacts.Contact",
        "accounts.Account",
        "opportunity.Opportunity",
        "cases.Case",
        "invoices.Invoice",
        "tasks.Task",
        "interactions.Interaction",
    }
    assert set(signals.deleted_labels()) == {
        "leads.Lead",
        "contacts.Contact",
        "accounts.Account",
        "opportunity.Opportunity",
        "tasks.Task",
    }

    monkeypatch.setattr(
        object_registry,
        "BUILT_IN",
        tuple(
            dataclasses.replace(e, webhook=None) if e.key == "task" else e
            for e in object_registry.BUILT_IN
        ),
    )
    assert "tasks.Task" not in signals.saved_labels()
    assert "tasks.Task" not in signals.deleted_labels()


def test_hook_entry_spec_appears_in_catalogue(site_object, admin_client, org_a, org_b):
    catalogue = admin_client.get(URL).json()["event_catalogue"]
    assert catalogue[-1] == {
        "module": "obj:site",
        "label": "Sites",
        "events": list(SITE_EVENTS),
    }
    assert catalogue[-2]["module"] == "interaction"
    assert events.event_names(org_a)[-3:] == SITE_EVENTS
    assert not set(SITE_EVENTS) & set(events.event_names(org_b))
    assert not set(SITE_EVENTS) & set(events.event_names())

    created = admin_client.post(
        URL,
        {"url": HOOK_URL, "events": ["obj:site.updated", "lead.created"]},
        format="json",
    )
    assert created.status_code == 201, created.content
    assert created.json()["events"] == ["lead.created", "obj:site.updated"]


def test_stored_unoffered_event_survives_save(
    site_object, admin_client, org_a, admin_profile
):
    endpoint = WebhookEndpoint.objects.create(
        org=org_a,
        url=HOOK_URL,
        events=["lead.created", "obj:site.created"],
        created_by=admin_profile.user,
    )
    site_object["active"] = False
    assert "obj:site" not in _modules(admin_client)

    saved = admin_client.patch(
        f"{URL}{endpoint.id}/", {"events": ["lead.updated"]}, format="json"
    )
    assert saved.status_code == 200, saved.content
    endpoint.refresh_from_db()
    assert endpoint.events == ["lead.updated", "obj:site.created"]

    sent_back = admin_client.patch(
        f"{URL}{endpoint.id}/",
        {"events": ["obj:site.created", "lead.deleted"]},
        format="json",
    )
    assert sent_back.status_code == 200, sent_back.content
    assert sent_back.json()["events"] == ["lead.deleted", "obj:site.created"]

    new_one = admin_client.patch(
        f"{URL}{endpoint.id}/",
        {"events": ["lead.deleted", "obj:site.updated"]},
        format="json",
    )
    assert new_one.status_code == 400
    assert new_one.json()["events"] == ["Unknown event: obj:site.updated."]

    site_object["active"] = True
    assert _modules(admin_client)[-1] == "obj:site"


def test_record_with_explicit_spec_publishes_under_its_prefix(
    org_a, admin_profile, django_capture_on_commit_callbacks
):
    endpoint = WebhookEndpoint.objects.create(
        org=org_a,
        url=HOOK_URL,
        events=list(SITE_EVENTS),
        created_by=admin_profile.user,
    )
    document = Document.objects.create(title="Harbour view", org=org_a)
    row = {"id": str(document.id), "object": "site", "name": "Harbour view"}

    with django_capture_on_commit_callbacks(execute=True):
        emit.record(document, emit.CREATED, spec=SITE)
    # A Document names no spec in the registry, so without one nothing is sent.
    with django_capture_on_commit_callbacks(execute=True):
        emit.record(document, emit.UPDATED)
    assert _deliveries(endpoint) == [("obj:site.created", row)]

    request = types.SimpleNamespace()
    with mock.patch("webhooks.emit.get_current_request", return_value=request):
        emit.record(document, emit.UPDATED, spec=SITE)
        emit.record(document, emit.UPDATED)
    emit.flush(request)
    assert _deliveries(endpoint)[1:] == [("obj:site.updated", row)]

    with django_capture_on_commit_callbacks(execute=True):
        emit.record(document, emit.DELETED, _site_data(document), spec=SITE)
        document.delete()
    assert _deliveries(endpoint)[2:] == [("obj:site.deleted", row)]
