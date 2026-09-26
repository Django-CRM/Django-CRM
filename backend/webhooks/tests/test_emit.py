"""Which saves become which webhook events, and for which org's endpoints."""

import types
from unittest import mock

import pytest
from django.contrib.contenttypes.models import ContentType
from django.db import transaction

from accounts.models import Account
from cases.models import Case
from common.models import Comment
from contacts.models import Contact
from invoices.models import Invoice
from leads.models import Lead
from opportunity.models import Opportunity
from tasks.models import Task
from webhooks import emit
from webhooks.models import WebhookDelivery, WebhookEndpoint


@pytest.fixture(autouse=True)
def no_broker():
    with mock.patch("webhooks.emit.deliver_webhook.delay") as delay:
        yield delay


def _endpoint(org, *event_names, **fields):
    return WebhookEndpoint.objects.create(
        org=org, url="https://hooks.example.com/in", events=list(event_names), **fields
    )


def _events(endpoint):
    return list(
        WebhookDelivery.objects.filter(endpoint=endpoint)
        .order_by("created_at")
        .values_list("event", flat=True)
    )


class TestOutsideARequest:
    def test_create_update_delete_reach_a_subscribed_endpoint(
        self, org_a, django_capture_on_commit_callbacks, no_broker
    ):
        hook = _endpoint(org_a, "lead.created", "lead.updated", "lead.deleted")
        with django_capture_on_commit_callbacks(execute=True):
            lead = Lead.objects.create(org=org_a, title="Hi", first_name="Ada")
        with django_capture_on_commit_callbacks(execute=True):
            lead.status = "assigned"
            lead.save()
        with django_capture_on_commit_callbacks(execute=True):
            lead.delete()
        assert _events(hook) == ["lead.created", "lead.updated", "lead.deleted"]
        created = WebhookDelivery.objects.filter(endpoint=hook).earliest("created_at")
        payload = created.payload
        assert set(payload) == {"id", "event", "created_at", "org_id", "data"}
        assert payload["org_id"] == str(org_a.id)
        assert payload["data"]["first_name"] == "Ada"
        assert str(created.event_id) == payload["id"]
        deleted = WebhookDelivery.objects.get(endpoint=hook, event="lead.deleted")
        assert deleted.payload["data"]["title"] == "Hi"
        # One Celery send per delivery, queued at commit.
        assert no_broker.call_count == 3

    def test_unsubscribed_and_inactive_endpoints_get_nothing(
        self, org_a, django_capture_on_commit_callbacks
    ):
        other_event = _endpoint(org_a, "contact.created")
        switched_off = _endpoint(org_a, "lead.created", is_active=False)
        with django_capture_on_commit_callbacks(execute=True):
            Lead.objects.create(org=org_a, title="Hi")
        assert (
            WebhookDelivery.objects.filter(
                endpoint__in=[other_event, switched_off]
            ).count()
            == 0
        )

    def test_another_orgs_record_never_reaches_this_orgs_endpoint(
        self, org_a, org_b, django_capture_on_commit_callbacks
    ):
        mine = _endpoint(org_a, "lead.created", "contact.created", "task.created")
        theirs = _endpoint(org_b, "lead.created")
        with django_capture_on_commit_callbacks(execute=True):
            Lead.objects.create(org=org_b, title="Theirs")
            Contact.objects.create(org=org_b, first_name="B", last_name="C")
            Task.objects.create(org=org_b, title="T", status="New", priority="Low")
        assert _events(mine) == []
        assert _events(theirs) == ["lead.created"]
        delivery = WebhookDelivery.objects.get(endpoint=theirs)
        assert delivery.org_id == org_b.id
        assert delivery.payload["org_id"] == str(org_b.id)

    def test_a_rolled_back_save_emits_nothing(
        self, org_a, django_capture_on_commit_callbacks
    ):
        hook = _endpoint(org_a, "lead.created")
        with django_capture_on_commit_callbacks(execute=True):
            with pytest.raises(RuntimeError):
                with transaction.atomic():
                    Lead.objects.create(org=org_a, title="Never")
                    raise RuntimeError
        assert _events(hook) == []

    def test_each_module_emits(self, org_a, django_capture_on_commit_callbacks):
        hook = _endpoint(
            org_a,
            "account.created",
            "contact.created",
            "ticket.created",
            "task.created",
            "invoice.created",
            "deal.created",
        )
        with django_capture_on_commit_callbacks(execute=True):
            Account.objects.create(org=org_a, name="Acme")
            Contact.objects.create(org=org_a, first_name="A", last_name="B")
            Case.objects.create(org=org_a, name="Broken", priority="High", status="New")
            Task.objects.create(org=org_a, title="T", status="New", priority="Low")
            Invoice.objects.create(org=org_a, invoice_title="I", invoice_number="1")
            Opportunity.objects.create(org=org_a, name="Deal")
        assert sorted(_events(hook)) == sorted(hook.events)

    def test_invoice_payload_never_carries_the_public_link_token(
        self, org_a, django_capture_on_commit_callbacks
    ):
        hook = _endpoint(org_a, "invoice.created")
        with django_capture_on_commit_callbacks(execute=True):
            invoice = Invoice.objects.create(
                org=org_a, invoice_title="I", invoice_number="7"
            )
        data = WebhookDelivery.objects.get(endpoint=hook).payload["data"]
        assert "public_token" not in data
        if invoice.public_token:
            assert invoice.public_token not in str(data)
        assert data["invoice_number"] == "7"


class TestTransitions:
    def test_deal_won_fires_once_on_entering_a_won_stage(
        self, org_a, django_capture_on_commit_callbacks
    ):
        hook = _endpoint(org_a, "deal.won", "deal.lost")
        with django_capture_on_commit_callbacks(execute=True):
            deal = Opportunity.objects.create(org=org_a, name="Deal", amount=100)
        with django_capture_on_commit_callbacks(execute=True):
            deal.stage = "CLOSED_WON"
            deal.closed_on = "2026-09-01"
            deal.save()
        with django_capture_on_commit_callbacks(execute=True):
            deal.description = "still won"
            deal.save()
        assert _events(hook) == ["deal.won"]

    def test_deal_lost(self, org_a, django_capture_on_commit_callbacks):
        hook = _endpoint(org_a, "deal.won", "deal.lost")
        with django_capture_on_commit_callbacks(execute=True):
            deal = Opportunity.objects.create(org=org_a, name="Deal")
            deal.stage = "CLOSED_LOST"
            deal.closed_on = "2026-09-01"
            deal.save()
        assert _events(hook) == ["deal.lost"]

    def test_invoice_paid_fires_on_the_move_to_paid_only(
        self, org_a, django_capture_on_commit_callbacks
    ):
        hook = _endpoint(org_a, "invoice.paid")
        with django_capture_on_commit_callbacks(execute=True):
            invoice = Invoice.objects.create(
                org=org_a, invoice_title="I", invoice_number="1", status="Sent"
            )
        with django_capture_on_commit_callbacks(execute=True):
            invoice.status = "Paid"
            invoice.save()
        with django_capture_on_commit_callbacks(execute=True):
            invoice.notes = "thanks"
            invoice.save()
        assert _events(hook) == ["invoice.paid"]


class TestTicketComments:
    def _comment(self, case, **fields):
        return Comment.objects.create(
            comment="We are on it",
            content_type=ContentType.objects.get_for_model(Case),
            object_id=case.id,
            org=case.org,
            **fields,
        )

    def test_public_comment_is_sent_and_internal_note_never_is(
        self, org_a, django_capture_on_commit_callbacks
    ):
        hook = _endpoint(org_a, "ticket.comment_added")
        case = Case.objects.create(org=org_a, name="Broken", priority="High")
        with django_capture_on_commit_callbacks(execute=True):
            self._comment(case, is_internal=True)
        assert _events(hook) == []
        with django_capture_on_commit_callbacks(execute=True):
            self._comment(case, is_internal=False)
        assert _events(hook) == ["ticket.comment_added"]
        data = WebhookDelivery.objects.get(endpoint=hook).payload["data"]
        assert data["ticket_id"] == str(case.id)
        assert data["comment"] == "We are on it"

    def test_a_comment_on_something_else_is_not_a_ticket_comment(
        self, org_a, django_capture_on_commit_callbacks
    ):
        hook = _endpoint(org_a, "ticket.comment_added")
        lead = Lead.objects.create(org=org_a, title="L")
        with django_capture_on_commit_callbacks(execute=True):
            Comment.objects.create(
                comment="hello",
                content_type=ContentType.objects.get_for_model(Lead),
                object_id=lead.id,
                org=org_a,
            )
        assert _events(hook) == []


class TestInsideARequest:
    """Events wait for the end of the request and describe the final state."""

    @pytest.fixture
    def request_scope(self):
        fake = types.SimpleNamespace()
        with mock.patch("webhooks.emit.get_current_request", return_value=fake):
            yield fake

    def test_create_then_assign_then_save_is_one_created_event_with_assignees(
        self, org_a, admin_profile, request_scope
    ):
        hook = _endpoint(org_a, "lead.created", "lead.updated")
        lead = Lead.objects.create(org=org_a, title="Hi")
        lead.assigned_to.add(admin_profile)
        lead.save()
        assert _events(hook) == []  # nothing until the flush
        emit.flush(request_scope)
        assert _events(hook) == ["lead.created"]
        data = WebhookDelivery.objects.get(endpoint=hook).payload["data"]
        assert data["assigned_to"] == [str(admin_profile.id)]

    def test_rolled_back_create_is_dropped_at_flush(self, org_a, request_scope):
        hook = _endpoint(org_a, "lead.created")
        with pytest.raises(RuntimeError):
            with transaction.atomic():
                Lead.objects.create(org=org_a, title="Never")
                raise RuntimeError
        emit.flush(request_scope)
        assert _events(hook) == []

    def test_created_then_deleted_is_only_deleted(self, org_a, request_scope):
        hook = _endpoint(org_a, "lead.created", "lead.deleted")
        lead = Lead.objects.create(org=org_a, title="Brief")
        lead.delete()
        emit.flush(request_scope)
        assert _events(hook) == ["lead.deleted"]

    def test_through_the_api_one_created_event_carries_the_assignee(
        self, admin_client, org_a, admin_profile
    ):
        hook = _endpoint(org_a, "task.created", "task.updated")
        response = admin_client.post(
            "/api/tasks/",
            {
                "title": "Call back",
                "status": "New",
                "priority": "High",
                "assigned_to": [str(admin_profile.id)],
            },
            format="json",
        )
        assert response.status_code == 200
        assert _events(hook) == ["task.created"]
        data = WebhookDelivery.objects.get(endpoint=hook).payload["data"]
        assert data["title"] == "Call back"
        assert data["assigned_to"] == [str(admin_profile.id)]
