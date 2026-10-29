"""A lead's first response and last outbound time, recomputed from what is
stored after every create, edit and delete that touches the lead.

Outbound means an outbound call, meeting, email, WhatsApp or SMS, whoever or
whatever logged it; enquiries are inbound and never count. The first response
is never earlier than the lead itself.
"""

from datetime import date, datetime, timedelta
from datetime import timezone as dt_timezone
from unittest import mock

import pytest
from django.db import connection
from django.db.models import QuerySet
from django.utils import timezone

from accounts.models import Account
from contacts.models import Contact
from interactions import signals
from interactions.models import Interaction
from interactions.service import (
    change_interaction,
    delete_interaction,
    log_interaction,
    refresh_lead_response,
)
from leads.models import Lead
from webhooks.models import WebhookDelivery, WebhookEndpoint

URL = "/api/interactions/"


def _at(lead, **delta):
    return lead.created_at + timedelta(**delta)


def _call(lead, who, **fields):
    return log_interaction(
        lead.org, home="lead", record=lead, kind="call", logged_by=who, **fields
    )


def _fields(lead):
    lead.refresh_from_db()
    return (lead.first_response_at, lead.first_response_by_id, lead.last_outbound_at)


class TestWhatCounts:
    def test_the_first_outbound_sets_all_three(self, lead, user_profile):
        call = _call(lead, user_profile, occurred_at=_at(lead, minutes=3))
        assert _fields(lead) == (call.occurred_at, user_profile.id, call.occurred_at)

    @pytest.mark.parametrize(
        "fields",
        [
            {"direction": "inbound"},
            {"kind": "enquiry", "direction": "inbound"},
        ],
    )
    def test_inbound_and_enquiries_do_not(self, lead, user_profile, fields):
        log_interaction(
            lead.org,
            home="lead",
            record=lead,
            logged_by=user_profile,
            occurred_at=_at(lead, minutes=1),
            **{"kind": "call", **fields},
        )
        assert _fields(lead) == (None, None, None)

    def test_a_connectors_row_with_no_logger_counts(self, lead):
        sent = log_interaction(
            lead.org,
            home="lead",
            record=lead,
            kind="whatsapp",
            origin="whatsapp_cloud",
            occurred_at=_at(lead, minutes=4),
        )
        assert sent.logged_by_id is None
        assert _fields(lead) == (sent.occurred_at, None, sent.occurred_at)

    def test_a_call_whose_logger_was_removed_still_counts(
        self, user_client, lead, user_profile, colleague
    ):
        theirs = _call(lead, colleague, occurred_at=_at(lead, minutes=3))
        mine = _call(lead, user_profile, occurred_at=_at(lead, minutes=10))
        colleague.delete()

        # Deleting the later call recomputes from what is left.
        assert user_client.delete(f"{URL}{mine.id}/").status_code == 204

        theirs.refresh_from_db()
        assert theirs.logged_by_id is None
        assert _fields(lead) == (theirs.occurred_at, None, theirs.occurred_at)

    @pytest.mark.parametrize("kind", ["call", "meeting", "email", "whatsapp", "sms"])
    def test_every_outbound_kind_does(self, lead, user_profile, kind):
        log_interaction(
            lead.org,
            home="lead",
            record=lead,
            kind=kind,
            logged_by=user_profile,
            occurred_at=_at(lead, minutes=2),
        )
        assert _fields(lead)[1] == user_profile.id

    def test_a_call_nobody_answered_counts(self, lead, user_profile):
        _call(lead, user_profile, outcome="no_answer", occurred_at=_at(lead, minutes=2))
        assert _fields(lead)[1] == user_profile.id

    def test_a_link_from_another_home_counts(self, lead, user_profile, org_a):
        from contacts.models import Contact

        contact = Contact.objects.create(org=org_a, first_name="M", last_name="L")
        log_interaction(
            org_a,
            home="contact",
            record=contact,
            kind="email",
            logged_by=user_profile,
            occurred_at=_at(lead, minutes=5),
            lead=lead,
        )
        assert _fields(lead)[0] == _at(lead, minutes=5)


class TestRecompute:
    def test_a_later_outbound_moves_only_the_last(self, lead, user_profile, colleague):
        first = _call(lead, user_profile, occurred_at=_at(lead, minutes=3))
        later = _call(lead, colleague, occurred_at=_at(lead, hours=2))
        assert _fields(lead) == (first.occurred_at, user_profile.id, later.occurred_at)

    def test_one_logged_earlier_after_the_fact_becomes_the_first(
        self, lead, user_profile, colleague
    ):
        _call(lead, user_profile, occurred_at=_at(lead, hours=1))
        earlier = _call(lead, colleague, occurred_at=_at(lead, minutes=1))
        assert _fields(lead)[:2] == (earlier.occurred_at, colleague.id)

    def test_deleting_the_first_response_moves_it_to_the_next(
        self, user_client, colleague_client, lead, user_profile, colleague
    ):
        first = _call(lead, user_profile, occurred_at=_at(lead, minutes=2))
        second = _call(lead, colleague, occurred_at=_at(lead, minutes=30))

        assert user_client.delete(f"{URL}{first.id}/").status_code == 204
        assert _fields(lead) == (second.occurred_at, colleague.id, second.occurred_at)

        assert colleague_client.delete(f"{URL}{second.id}/").status_code == 204
        # Nothing outbound is left: the lead is waiting for an answer again.
        assert _fields(lead) == (None, None, None)

    def test_backdating_through_the_api_follows(
        self, user_client, lead, user_profile, colleague
    ):
        _call(lead, colleague, occurred_at=_at(lead, minutes=30))
        mine = _call(lead, user_profile, occurred_at=_at(lead, hours=3))

        response = user_client.patch(
            f"{URL}{mine.id}/",
            {"occurred_at": _at(lead, minutes=5).isoformat()},
            format="json",
        )

        assert response.status_code == 200, response.content
        assert _fields(lead) == (
            _at(lead, minutes=5),
            user_profile.id,
            _at(lead, minutes=30),
        )

    def test_turning_it_inbound_through_the_api_follows(
        self, user_client, lead, user_profile
    ):
        mine = _call(lead, user_profile, occurred_at=_at(lead, minutes=5))
        response = user_client.patch(
            f"{URL}{mine.id}/", {"direction": "inbound"}, format="json"
        )
        assert response.status_code == 200
        assert _fields(lead) == (None, None, None)

    def test_a_call_before_the_lead_existed_is_a_zero_response(
        self, user_client, lead, user_profile
    ):
        before = lead.created_at - timedelta(days=1)
        response = user_client.post(
            URL,
            {"kind": "call", "lead": str(lead.id), "occurred_at": before.isoformat()},
            format="json",
        )
        assert response.status_code == 201
        first_at, by, last_at = _fields(lead)
        assert first_at == lead.created_at
        assert first_at - lead.created_at == timedelta(0)
        assert by == user_profile.id
        # Clamped as well, so the lead does not read as waiting for an answer.
        assert last_at == lead.created_at

    def test_it_answers_with_what_changed(self, lead, user_profile):
        assert refresh_lead_response(lead) == []
        Interaction.objects.create(
            org=lead.org,
            home="lead",
            lead=lead,
            kind="call",
            logged_by=user_profile,
            occurred_at=_at(lead, minutes=1),
        )
        changed = refresh_lead_response(lead)
        assert set(changed) == {
            "first_response_at",
            "first_response_by",
            "last_outbound_at",
            "last_contacted",
        }
        assert lead.first_response_by_id == user_profile.id
        assert refresh_lead_response(lead) == []

    def test_a_lead_from_another_org_is_never_counted(self, lead, org_b, user_profile):
        other = Lead.objects.create(org=org_b, first_name="Elsewhere")
        Interaction.objects.create(
            org=org_b,
            home="lead",
            lead=other,
            kind="call",
            logged_by=user_profile,
            occurred_at=_at(lead, minutes=1),
        )
        assert refresh_lead_response(lead) == []
        assert _fields(lead) == (None, None, None)


class TestLastContacted:
    def test_it_takes_the_org_local_date(self, lead, user_profile, org_a):
        org_a.timezone = "Pacific/Kiritimati"  # UTC+14
        org_a.save(update_fields=["timezone"])
        lead.refresh_from_db()
        when = datetime(2026, 3, 1, 12, 0, tzinfo=dt_timezone.utc)
        Lead.objects.filter(pk=lead.pk).update(created_at=when - timedelta(days=1))
        lead.refresh_from_db()
        _call(lead, user_profile, occurred_at=when)
        lead.refresh_from_db()
        assert lead.last_contacted == date(2026, 3, 2)

    def test_it_only_moves_forward(self, lead, user_profile):
        typed = timezone.localdate() + timedelta(days=3)
        Lead.objects.filter(pk=lead.pk).update(last_contacted=typed)
        call = _call(lead, user_profile, occurred_at=_at(lead, minutes=1))
        lead.refresh_from_db()
        assert lead.last_contacted == typed

        call.delete()
        refresh_lead_response(lead)
        lead.refresh_from_db()
        assert lead.last_contacted == typed

    def test_a_delete_does_not_take_it_back(self, user_client, lead, user_profile):
        Lead.objects.filter(pk=lead.pk).update(last_contacted=date(2020, 1, 1))
        call = _call(lead, user_profile, occurred_at=_at(lead, minutes=1))
        lead.refresh_from_db()
        moved = lead.last_contacted
        assert moved > date(2020, 1, 1)
        assert user_client.delete(f"{URL}{call.id}/").status_code == 204
        lead.refresh_from_db()
        assert lead.last_contacted == moved


class TestQuietly:
    """The derived fields are written with a queryset update: no `lead.updated`
    webhook and no automation rule. The edit time moves, since a change feed
    reads it and the response fields are part of the lead."""

    def test_no_lead_updated_and_no_rules(
        self, user_client, lead, org_a, admin_profile
    ):
        hook = WebhookEndpoint.objects.create(
            org=org_a,
            url="https://hooks.example.com/in",
            events=["lead.updated"],
            created_by=admin_profile.user,
        )
        edited = Lead.objects.get(pk=lead.pk).updated_at
        received = []
        with (
            mock.patch("automation.engine.process", side_effect=received.extend),
            mock.patch("webhooks.emit.deliver_webhook.delay"),
        ):
            response = user_client.post(
                URL, {"kind": "call", "lead": str(lead.id)}, format="json"
            )
        assert response.status_code == 201
        lead.refresh_from_db()
        assert lead.first_response_at is not None
        assert lead.updated_at > edited
        assert not WebhookDelivery.objects.filter(endpoint=hook).exists()
        assert [c for c in received if c.object_key == "lead"] == []


class TestMerge:
    def test_the_keeper_takes_the_losers_interactions_and_is_recomputed(
        self, admin_client, org_a, user_profile, colleague
    ):
        keeper = Lead.objects.create(org=org_a, first_name="Keep", status="assigned")
        loser = Lead.objects.create(org=org_a, first_name="Lose", status="assigned")
        Lead.objects.filter(pk__in=[keeper.pk, loser.pk]).update(
            created_at=timezone.now() - timedelta(days=2)
        )
        keeper.refresh_from_db()
        loser.refresh_from_db()
        late = _call(keeper, colleague, occurred_at=_at(keeper, hours=5))
        early = _call(loser, user_profile, occurred_at=_at(keeper, minutes=10))
        enquiry = log_interaction(
            org_a,
            home="lead",
            record=loser,
            kind="enquiry",
            direction="inbound",
            origin="intake",
        )

        response = admin_client.post(
            f"/api/leads/{keeper.id}/merge/", {"merge_id": str(loser.id)}, format="json"
        )

        assert response.status_code == 200, response.content
        assert not Lead.objects.filter(pk=loser.pk).exists()
        moved = Interaction.objects.filter(pk__in=[early.pk, enquiry.pk])
        assert {(i.home, i.lead_id) for i in moved} == {("lead", keeper.id)}
        assert _fields(keeper) == (early.occurred_at, user_profile.id, late.occurred_at)

    def test_a_keeper_with_no_outbound_takes_none_from_the_loser(
        self, admin_client, org_a, user_profile
    ):
        """The loser's stored fields are not copied over a blank keeper: they are
        recomputed from the interactions that moved, here none."""
        keeper = Lead.objects.create(org=org_a, first_name="Keep", status="assigned")
        loser = Lead.objects.create(org=org_a, first_name="Lose", status="assigned")
        Lead.objects.filter(pk=loser.pk).update(
            first_response_at=timezone.now(),
            first_response_by=user_profile,
            last_outbound_at=timezone.now(),
        )
        response = admin_client.post(
            f"/api/leads/{keeper.id}/merge/", {"merge_id": str(loser.id)}, format="json"
        )
        assert response.status_code == 200
        assert _fields(keeper) == (None, None, None)


class TestRecordDeletes:
    """Deleting the record an interaction is home to takes the interaction with
    it, and the lead it linked is recomputed once the delete commits."""

    def _convert(self, client, lead):
        response = client.patch(
            f"/api/leads/{lead.id}/", {"status": "converted"}, format="json"
        )
        assert response.status_code == 200, response.content

    def test_deleting_the_contact_a_converted_lead_became(
        self,
        admin_client,
        lead,
        user_profile,
        org_a,
        django_capture_on_commit_callbacks,
    ):
        _call(lead, user_profile, occurred_at=_at(lead, minutes=5))
        self._convert(admin_client, lead)
        contact = Contact.objects.get(org=org_a, email__iexact=lead.email)
        assert _fields(lead)[0] is not None

        with django_capture_on_commit_callbacks(execute=True):
            response = admin_client.delete(f"/api/contacts/{contact.id}/")

        assert response.status_code in (200, 204), response.content
        assert not Interaction.objects.filter(lead=lead).exists()
        assert _fields(lead) == (None, None, None)

    def test_deleting_the_account_a_converted_lead_became(
        self,
        user_client,
        admin_client,
        lead,
        user_profile,
        admin_profile,
        org_a,
        django_capture_on_commit_callbacks,
    ):
        # A contact with the lead's email the member may not open: the
        # conversion makes no contact, so the account is home.
        stranger = Contact.objects.create(
            org=org_a, first_name="Some", last_name="One", email=lead.email
        )
        stranger.assigned_to.add(admin_profile)
        call = _call(lead, user_profile, occurred_at=_at(lead, minutes=5))
        self._convert(user_client, lead)
        call.refresh_from_db()
        assert call.home == "account"
        account = Account.objects.get(org=org_a)

        with django_capture_on_commit_callbacks(execute=True):
            response = admin_client.delete(f"/api/accounts/{account.id}/")

        assert response.status_code in (200, 204), response.content
        assert not Interaction.objects.filter(pk=call.pk).exists()
        assert _fields(lead) == (None, None, None)

    def test_a_record_that_is_only_linked_leaves_the_lead_answered(
        self,
        admin_client,
        lead,
        user_profile,
        org_a,
        django_capture_on_commit_callbacks,
    ):
        contact = Contact.objects.create(org=org_a, first_name="M", last_name="L")
        call = log_interaction(
            org_a,
            home="lead",
            record=lead,
            kind="call",
            logged_by=user_profile,
            occurred_at=_at(lead, minutes=5),
            contact=contact,
        )

        with django_capture_on_commit_callbacks(execute=True):
            response = admin_client.delete(f"/api/contacts/{contact.id}/")

        assert response.status_code in (200, 204), response.content
        call.refresh_from_db()
        assert call.contact_id is None
        assert _fields(lead) == (call.occurred_at, user_profile.id, call.occurred_at)

    def test_each_lead_is_recomputed_once(
        self, lead, user_profile, colleague, org_a, django_capture_on_commit_callbacks
    ):
        contact = Contact.objects.create(org=org_a, first_name="M", last_name="L")
        kept = _call(lead, colleague, occurred_at=_at(lead, hours=2))
        for minutes in (5, 10):
            log_interaction(
                org_a,
                home="contact",
                record=contact,
                kind="call",
                logged_by=user_profile,
                occurred_at=_at(lead, minutes=minutes),
                lead=lead,
            )
        assert _fields(lead)[1] == user_profile.id

        with (
            mock.patch.object(
                signals, "refresh_lead_response", wraps=refresh_lead_response
            ) as refreshed,
            django_capture_on_commit_callbacks(execute=True),
        ):
            contact.delete()

        assert refreshed.call_count == 1
        assert _fields(lead) == (kept.occurred_at, colleague.id, kept.occurred_at)

    def test_nothing_is_recomputed_before_the_delete_commits(
        self, lead, user_profile, org_a, django_capture_on_commit_callbacks
    ):
        contact = Contact.objects.create(org=org_a, first_name="M", last_name="L")
        log_interaction(
            org_a,
            home="contact",
            record=contact,
            kind="call",
            logged_by=user_profile,
            lead=lead,
        )
        with django_capture_on_commit_callbacks(execute=False) as callbacks:
            contact.delete()
        assert _fields(lead)[0] is not None
        for callback in callbacks:
            callback()
        assert _fields(lead) == (None, None, None)


class TestLockOrder:
    """Every write locks the lead before it touches an interaction, the order a
    merge and a conversion take, so two writers on one lead queue."""

    def _first_lead_lock(self, look):
        seen = []
        original = QuerySet.select_for_update

        def spy(queryset, *args, **kwargs):
            if queryset.model is Lead and not seen:
                seen.append(look())
            return original(queryset, *args, **kwargs)

        return seen, mock.patch.object(
            QuerySet, "select_for_update", autospec=True, side_effect=spy
        )

    def test_logging(self, lead, user_profile):
        seen, spying = self._first_lead_lock(Interaction.objects.count)
        with spying:
            _call(lead, user_profile)
        assert seen == [0]

    def test_changing(self, lead, user_profile):
        row = _call(lead, user_profile, body="before")
        seen, spying = self._first_lead_lock(
            lambda: Interaction.objects.get(pk=row.pk).body
        )
        with spying:
            change_interaction(row, {"body": "after"})
        assert seen == ["before"]

    def test_deleting(self, lead, user_profile):
        row = _call(lead, user_profile)
        seen, spying = self._first_lead_lock(
            lambda: Interaction.objects.filter(pk=row.pk).exists()
        )
        with spying:
            delete_interaction(row)
        assert seen == [True]


@pytest.mark.postgres_only
@pytest.mark.django_db(transaction=True)
def test_two_calls_logged_at_once_leave_the_first_and_last(
    org_a, lead, user_profile, colleague
):
    """Two members log a call on one lead at the same moment. The lead row
    lock makes the second wait for the first, so its recompute sees both rows.
    The gate after each insert holds both transactions open together, so
    without the lock each would recompute from its own row alone and the last
    to commit would win."""
    import threading

    from django.db.models.signals import post_save

    from common.tasks import set_rls_context
    from common.testing import _make_authenticated_client

    if connection.vendor != "postgresql":
        pytest.skip("row locks need PostgreSQL")

    Lead.objects.filter(pk=lead.pk).update(
        created_at=timezone.now() - timedelta(hours=2)
    )
    lead.refresh_from_db()
    early, late = _at(lead, minutes=5), _at(lead, minutes=20)
    jobs = [
        (_make_authenticated_client(user_profile.user, org_a, user_profile), early),
        (_make_authenticated_client(colleague.user, org_a, colleague), late),
    ]
    barrier = threading.Barrier(len(jobs))
    # With the lead locked the second insert waits, so the first gives up
    # waiting at the gate and carries on.
    gate = threading.Barrier(len(jobs))
    statuses, errors = [], []

    def hold(sender, instance, created, **kwargs):
        if created:
            try:
                gate.wait(timeout=3)
            except threading.BrokenBarrierError:
                pass

    def worker(client, when):
        try:
            barrier.wait()
            response = client.post(
                URL,
                {"kind": "call", "lead": str(lead.id), "occurred_at": when.isoformat()},
                format="json",
            )
            statuses.append(response.status_code)
        except Exception as exc:  # recorded and asserted on below
            errors.append(exc)
        finally:
            connection.close()

    post_save.connect(hold, sender=Interaction, weak=False)
    try:
        threads = [threading.Thread(target=worker, args=job) for job in jobs]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
    finally:
        post_save.disconnect(hold, sender=Interaction)

    assert errors == []
    assert statuses == [201, 201]
    set_rls_context(org_a.id)
    assert _fields(lead) == (early, user_profile.id, late)
