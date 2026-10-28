"""The waiting reminder (`leads.tasks.remind_waiting_leads`).

An org that sets `lead_reminder_minutes` to N has each unanswered lead's
assignees, or its admins when nobody is on it, told once per enquiry after it
has waited N minutes. A lead that has waited more than 24 hours, or since
before the reminder was switched on, is left alone, so neither a scheduler
that starts late nor switching it on with a backlog sends a burst.
"""

from datetime import timedelta

import pytest
from celery.schedules import crontab
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from common.models import Notification, Org, Profile, User
from common.testing import restore_rls_context, rls_org, set_modules_off
from crm.celery import app
from leads import alerts, tasks
from leads.models import Lead
from leads.tasks import remind_waiting_leads


def _member(org, name, *, role="USER", active=True):
    user = User.objects.create_user(
        email=f"{name.lower()}@example.com", password="x", name=name
    )
    return Profile.objects.create(user=user, org=org, role=role, is_active=active)


@pytest.fixture
def org(org_a):
    org_a.lead_response_since = timezone.now() - timedelta(days=30)
    org_a.lead_reminder_minutes = 15
    org_a.lead_reminder_since = timezone.now() - timedelta(days=30)
    org_a.save(
        update_fields=[
            "lead_response_since",
            "lead_reminder_minutes",
            "lead_reminder_since",
        ]
    )
    return org_a


@pytest.fixture
def asha(org):
    return _member(org, "Asha")


def _lead(org, *, waited, assigned=(), **fields):
    """A lead that arrived `waited` ago and nobody has answered."""
    fields.setdefault("status", "assigned")
    fields.setdefault("first_name", "Maria")
    with rls_org(org):
        lead = Lead.objects.create(org=org, **fields)
        Lead.objects.filter(pk=lead.pk).update(created_at=timezone.now() - waited)
        for profile in assigned:
            lead.assigned_to.add(profile)
    Notification.objects.filter(verb__startswith="lead.").delete()
    lead.refresh_from_db()
    return lead


def _reminded():
    rows = Notification.objects.filter(verb=alerts.WAITING).select_related("recipient")
    return sorted((row.recipient.user.name, row.link) for row in rows)


def _run():
    remind_waiting_leads()
    restore_rls_context()


def test_the_beat_runs_it_every_minute():
    assert app.conf.beat_schedule["remind-waiting-leads"] == {
        "task": "leads.tasks.remind_waiting_leads",
        "schedule": crontab(minute="*"),
    }
    assert remind_waiting_leads.name == "leads.tasks.remind_waiting_leads"


def test_off_does_nothing(org, asha):
    org.lead_reminder_minutes = None
    org.save(update_fields=["lead_reminder_minutes"])
    lead = _lead(org, waited=timedelta(minutes=40), assigned=[asha])
    _run()
    assert _reminded() == []
    lead.refresh_from_db()
    assert lead.waiting_reminded_at is None


def test_n_minutes_passes_once_not_twice(org, asha):
    lead = _lead(org, waited=timedelta(minutes=16), assigned=[asha])
    _run()
    assert _reminded() == [("Asha", f"/leads/{lead.id}")]
    lead.refresh_from_db()
    assert lead.waiting_reminded_at is not None
    row = Notification.objects.get(verb=alerts.WAITING)
    assert row.entity_name == "Maria" and row.entity_id == lead.id

    _run()
    assert len(_reminded()) == 1


def test_not_before_n_minutes(org, asha):
    _lead(org, waited=timedelta(minutes=10), assigned=[asha])
    _run()
    assert _reminded() == []


def test_not_after_24_hours(org, asha):
    lead = _lead(org, waited=timedelta(hours=24, minutes=1), assigned=[asha])
    _run()
    assert _reminded() == []
    lead.refresh_from_db()
    assert lead.waiting_reminded_at is None


def test_again_after_a_new_enquiry(org, asha):
    lead = _lead(org, waited=timedelta(hours=2), assigned=[asha])
    _run()
    assert len(_reminded()) == 1

    # Reminded an hour ago; the person asked again since.
    now = timezone.now()
    Lead.objects.filter(pk=lead.pk).update(
        waiting_reminded_at=now - timedelta(hours=1),
        last_enquiry_at=now - timedelta(minutes=20),
    )
    _run()
    assert len(_reminded()) == 2
    _run()
    assert len(_reminded()) == 2


def test_a_new_enquiry_after_24_hours_unanswered_is_reminded(org, asha):
    """The window runs from the latest enquiry: a person who asks again
    after a day without an answer is waiting now."""
    lead = _lead(org, waited=timedelta(days=3), assigned=[asha])
    Lead.objects.filter(pk=lead.pk).update(
        last_enquiry_at=timezone.now() - timedelta(minutes=20)
    )
    _run()
    assert _reminded() == [("Asha", f"/leads/{lead.id}")]


def test_switching_it_on_reminds_no_one_already_waiting(org, asha, admin_client):
    org.lead_reminder_minutes = None
    org.save(update_fields=["lead_reminder_minutes"])
    _lead(org, waited=timedelta(minutes=30), assigned=[asha])

    response = admin_client.patch(
        "/api/org/settings/", {"lead_reminder_minutes": 15}, format="json"
    )
    assert response.status_code == 200, response.content
    _run()
    assert _reminded() == []

    # Switched on 20 minutes ago: a lead that arrived 16 minutes ago has
    # waited since then, the one from 30 minutes ago has not.
    Org.objects.filter(pk=org.pk).update(
        lead_reminder_since=timezone.now() - timedelta(minutes=20)
    )
    new = _lead(org, waited=timedelta(minutes=16), assigned=[asha])
    _run()
    assert _reminded() == [("Asha", f"/leads/{new.id}")]


def test_an_enquiry_after_switching_it_on_is_reminded(org, asha):
    lead = _lead(org, waited=timedelta(hours=2), assigned=[asha])
    Org.objects.filter(pk=org.pk).update(
        lead_reminder_since=timezone.now() - timedelta(hours=1)
    )
    _run()
    assert _reminded() == []

    Lead.objects.filter(pk=lead.pk).update(
        last_enquiry_at=timezone.now() - timedelta(minutes=20)
    )
    _run()
    assert _reminded() == [("Asha", f"/leads/{lead.id}")]


def test_admins_when_unassigned(org, admin_profile, asha):
    admin_profile.user.name = "Olga"
    admin_profile.user.save(update_fields=["name"])
    _member(org, "Gone", role="ADMIN", active=False)
    lead = _lead(org, waited=timedelta(minutes=30))
    _run()
    assert _reminded() == [("Olga", f"/leads/{lead.id}")]


def test_admins_when_every_assignee_has_left(org, admin_profile):
    gone = _member(org, "Gone", active=False)
    _lead(org, waited=timedelta(minutes=30), assigned=[gone])
    _run()
    assert [name for name, _ in _reminded()] == [admin_profile.user.name]


@pytest.mark.parametrize("status", ["converted", "closed"])
def test_a_closed_lead_is_not_waiting(org, asha, status):
    _lead(org, waited=timedelta(minutes=30), assigned=[asha], status=status)
    _run()
    assert _reminded() == []


def test_an_answered_lead_is_not_waiting(org, asha):
    lead = _lead(org, waited=timedelta(minutes=30), assigned=[asha])
    Lead.objects.filter(pk=lead.pk).update(
        last_outbound_at=timezone.now() - timedelta(minutes=5)
    )
    _run()
    assert _reminded() == []


def test_a_deleted_lead_is_not_waiting(org, asha):
    _lead(org, waited=timedelta(minutes=30), assigned=[asha], is_active=False)
    _run()
    assert _reminded() == []


def test_leads_switched_off_reminds_no_one(org, asha):
    _lead(org, waited=timedelta(minutes=30), assigned=[asha])
    set_modules_off(org, "leads")
    _run()
    assert _reminded() == []


def test_another_orgs_setting_is_its_own(org, org_b, asha):
    other = _member(org_b, "Bob")
    _lead(org, waited=timedelta(minutes=10), assigned=[asha])
    with rls_org(org_b):
        org_b.lead_response_since = timezone.now() - timedelta(days=1)
        org_b.lead_reminder_minutes = 5
        org_b.lead_reminder_since = timezone.now() - timedelta(days=1)
        org_b.save(
            update_fields=[
                "lead_response_since",
                "lead_reminder_minutes",
                "lead_reminder_since",
            ]
        )
        theirs = _lead(org_b, waited=timedelta(minutes=10), assigned=[other])
    _run()
    assert _reminded() == [("Bob", f"/leads/{theirs.id}")]


def test_a_doubled_beat_reminds_once(org, asha, monkeypatch):
    """Two runs that both picked the lead before either stamped it: the
    stamp is a conditional update, so only one of them tells anybody."""
    _lead(org, waited=timedelta(minutes=30), assigned=[asha])
    real = tasks._due
    stale = list(real(org, timezone.now()))
    monkeypatch.setattr(tasks, "_due", lambda org, now: stale)
    _run()
    _run()
    assert len(_reminded()) == 1


def test_the_reminder_pushes(
    org, asha, monkeypatch, django_capture_on_commit_callbacks
):
    from common.models import DeviceToken

    DeviceToken.objects.create(
        org=org, user=asha.user, token="tok-" + "a" * 40, platform="android"
    )
    sent = []
    monkeypatch.setattr(tasks, "push_configured", lambda: True)
    monkeypatch.setattr(
        alerts.send_push, "delay", lambda *args, **kwargs: sent.append((args, kwargs))
    )
    lead = _lead(org, waited=timedelta(minutes=30), assigned=[asha])
    # A worker runs outside any transaction, so the push goes at once; the
    # test's own transaction holds it until the commit.
    with django_capture_on_commit_callbacks(execute=True):
        _run()
    assert sent == [
        (
            (
                str(asha.id),
                "BottleCRM",
                "Maria is waiting for a reply",
                f"/leads/{lead.id}",
            ),
            {"verb": alerts.WAITING},
        )
    ]


def test_the_push_setting_is_read_once_per_run(org, asha, monkeypatch):
    for minutes in (20, 30, 40):
        _lead(org, waited=timedelta(minutes=minutes), assigned=[asha])
    reads = []
    monkeypatch.setattr(tasks, "push_configured", lambda: reads.append(1) or False)

    def per_alert():
        raise AssertionError("read per alert")

    monkeypatch.setattr(alerts, "push_configured", per_alert)
    _run()
    assert len(_reminded()) == 3
    assert reads == [1]


def test_rls_context_is_set_before_the_first_query(org, asha, monkeypatch):
    _lead(org, waited=timedelta(minutes=30), assigned=[asha])
    calls = []
    with CaptureQueriesContext(connection) as ctx:

        def record(org_id):
            leads_read = any('"lead"' in q["sql"] for q in ctx.captured_queries)
            calls.append((str(org_id), leads_read))

        monkeypatch.setattr(tasks, "set_rls_context", record)
        monkeypatch.setattr(tasks, "clear_rls_context", lambda: None)
        remind_waiting_leads()
        read = any('"lead"' in q["sql"] for q in ctx.captured_queries)

    assert calls == [(str(org.id), False)]
    assert read
    assert len(_reminded()) == 1
