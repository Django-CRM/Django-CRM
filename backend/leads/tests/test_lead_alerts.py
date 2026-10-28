"""Lead alerts: who is told about a lead, in the app and on the phone.

One hook covers every way a lead is assigned (`m2m_changed` on
`Lead.assigned_to`), so each path is pinned here once: the person newly put
on the lead is told, and the person who did it is not. Intake adds "a new
lead nobody owns" for admins, a minute after it arrives, and "enquired again"
for the assignees. Bulk writes run inside `leads.alerts.quiet()`.
"""

import contextlib
import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from automation.models import AutomationRule
from common.duplicate_detection import merge_records
from common.models import DeviceToken, Notification, Profile, User
from common.packs.applier import apply_pack
from common.packs.loader import get_pack
from common.testing import restore_rls_context
from leads import alerts, intake, updates
from leads.intake import IncomingLead, receive_lead
from leads.models import Lead
from leads.tasks import send_email_to_assigned_user, send_enquired_again_email
from leads.updates import update_lead
from webforms.models import WebForm, WebFormField
from webforms.service import submit_form
from webforms.tasks import send_webform_submission_email

SOURCE = "intake:11111111-1111-4111-8111-111111111111"


def _member(org, name, *, role="USER", active=True, user_active=True):
    user = User.objects.create_user(
        email=f"{name.lower()}@example.com",
        password="testpass123",
        name=name,
        is_active=user_active,
    )
    return Profile.objects.create(user=user, org=org, role=role, is_active=active)


def _lead(org, **fields):
    values = {"first_name": "Maria", "last_name": "Lopez", "status": "assigned"}
    values.update(fields)
    return Lead.objects.create(org=org, **values)


def _alerts(verb=None, **filters):
    rows = Notification.objects.filter(verb__startswith="lead.", **filters)
    if verb is not None:
        rows = rows.filter(verb=verb)
    return rows


def _told(verb):
    """Who received `verb`, as a set of profiles."""
    return {row.recipient for row in _alerts(verb).select_related("recipient")}


def _incoming(**overrides):
    fields = {
        "values": {"first_name": "Pat", "email": "pat@example.com"},
        "custom_fields": {},
        "source": "google ads",
        "external_source": SOURCE,
        "channel_label": "webhook Ads",
    }
    fields.update(overrides)
    return IncomingLead(**fields)


@pytest.fixture
def asha(org_a):
    return _member(org_a, "Asha")


@pytest.fixture
def ravi(org_a):
    return _member(org_a, "Ravi")


@pytest.fixture
def pushes(monkeypatch):
    """Push switched on, and every push queued as (profile id, title, body, link, verb)."""
    sent = []
    monkeypatch.setattr(alerts, "push_configured", lambda: True)
    monkeypatch.setattr(
        alerts.send_push,
        "delay",
        lambda profile_id, title, body, link, verb="": sent.append(
            (profile_id, title, body, link, verb)
        ),
    )
    return sent


@pytest.fixture
def commit(django_capture_on_commit_callbacks):
    return lambda: django_capture_on_commit_callbacks(execute=True)


@pytest.fixture
def later(monkeypatch):
    """`notify_created` as the broker holds it: queued, then run by hand as
    the worker would a minute later."""
    queued = []

    def run():
        for args, _ in queued:
            alerts.notify_created(*args)
            restore_rls_context()
        queued.clear()

    monkeypatch.setattr(
        alerts.notify_created,
        "apply_async",
        lambda args, **options: queued.append((tuple(args), options)),
    )
    return SimpleNamespace(queued=queued, run=run)


@pytest.fixture(autouse=True)
def _no_mail(monkeypatch):
    """The emails are pinned on their own below; here they are only queued noise."""
    from leads.views import lead_views

    for module in (intake, updates, lead_views):
        monkeypatch.setattr(
            module.send_email_to_assigned_user, "delay", lambda *a, **k: None
        )
    monkeypatch.setattr(intake.send_enquired_again_email, "delay", lambda *a, **k: None)


def _device(profile, token="tok-" + "a" * 40):
    return DeviceToken.objects.create(
        org=profile.org, user=profile.user, token=token, platform="android"
    )


# ---------------------------------------------------------------------------
# Assignment, from every path
# ---------------------------------------------------------------------------


class TestAssignment:
    def test_update_view_alerts_the_newly_added_and_never_the_assigner(
        self, admin_client, admin_profile, asha, ravi, org_a
    ):
        lead = _lead(org_a)
        lead.assigned_to.add(ravi)
        Notification.objects.all().delete()

        response = admin_client.put(
            f"/api/leads/{lead.id}/",
            {
                "first_name": "Maria",
                "last_name": "Lopez",
                "assigned_to": [str(admin_profile.id), str(asha.id), str(ravi.id)],
            },
            format="json",
        )

        assert response.status_code == 200, response.content
        assert _told(alerts.ASSIGNED) == {asha}
        [row] = _alerts(alerts.ASSIGNED)
        assert row.link == f"/leads/{lead.id}"
        assert row.entity_type == "Lead" and row.entity_id == lead.id
        assert row.entity_name == "Maria Lopez"
        assert row.actor == admin_profile

    def test_patch_alerts_the_newly_added_and_never_the_assigner(
        self, admin_client, admin_profile, asha, org_a
    ):
        lead = _lead(org_a)
        response = admin_client.patch(
            f"/api/leads/{lead.id}/",
            {"assigned_to": [str(admin_profile.id), str(asha.id)]},
            format="json",
        )
        assert response.status_code == 200, response.content
        assert _told(alerts.ASSIGNED) == {asha}

    def test_create_view_alerts_the_newly_added_and_never_the_assigner(
        self, admin_client, admin_profile, asha, org_a
    ):
        response = admin_client.post(
            "/api/leads/",
            {
                "first_name": "Maria",
                "last_name": "Lopez",
                "source": "google ads",
                "assigned_to": [str(admin_profile.id), str(asha.id)],
            },
            format="json",
        )
        assert response.status_code == 200, response.content
        assert _told(alerts.ASSIGNED) == {asha}
        [row] = _alerts(alerts.ASSIGNED)
        assert row.data == {"source": "Google ads"}

    def test_shared_update_service_alerts_the_newly_added_and_never_the_assigner(
        self, admin_profile, asha, org_a
    ):
        lead = _lead(org_a)
        errors = update_lead(
            SimpleNamespace(profile=admin_profile),
            lead,
            {"assigned_to": [str(admin_profile.id), str(asha.id)]},
        )
        assert errors is None
        assert _told(alerts.ASSIGNED) == {asha}

    def test_automation_alerts_everyone_it_assigns(
        self, admin_client, admin_profile, asha, org_a
    ):
        """A rule assigns, not the member whose save set it off, so even
        that member is told when the rule picks them."""
        AutomationRule.objects.create(
            org=org_a,
            name="Share new leads",
            object_key="lead",
            trigger={"event": "created", "field": None, "to": None},
            actions=[
                {
                    "type": "assign",
                    "people": [str(admin_profile.id), str(asha.id)],
                    "mode": "rotate",
                }
            ],
            owner=admin_profile,
            is_active=True,
        )
        for name in ("Maria", "Sam"):
            response = admin_client.post(
                "/api/leads/", {"first_name": name, "last_name": "X"}, format="json"
            )
            assert response.status_code == 200, response.content

        assert _told(alerts.ASSIGNED) == {admin_profile, asha}

    def test_intake_assignment_alerts_the_assignee(self, asha, org_a, commit, later):
        """A lead webhook or lead address with an owner hands every new lead
        to them. Nobody signed in assigned it, so the owner is told."""
        with commit():
            result = receive_lead(org_a, _incoming(assignee=asha))
        later.run()
        assert result.result == "created"
        assert _told(alerts.ASSIGNED) == {asha}
        assert not _alerts(alerts.CREATED).exists()

    def test_web_form_rotation_alerts_the_member_it_picks(
        self, admin_profile, asha, org_a, commit
    ):
        form = _rotation_form(org_a, admin_profile, [asha])
        with commit():
            submission = submit_form(form, {"email": "pat@example.com"})
        assert list(submission.lead.assigned_to.all()) == [asha]
        assert _told(alerts.ASSIGNED) == {asha}

    def test_resaving_the_same_assignees_alerts_no_one(
        self, admin_client, admin_profile, asha, org_a
    ):
        lead = _lead(org_a)
        lead.assigned_to.add(asha)
        Notification.objects.all().delete()
        body = {"first_name": "Maria", "assigned_to": [str(asha.id)]}

        url = f"/api/leads/{lead.id}/"
        assert admin_client.put(url, body, format="json").status_code == 200
        assert admin_client.patch(url, body, format="json").status_code == 200
        update_lead(SimpleNamespace(profile=admin_profile), lead, body)

        assert not _alerts().exists()

    def test_an_inactive_member_or_user_is_not_told(self, admin_profile, org_a):
        left = _member(org_a, "Left", active=False)
        locked = _member(org_a, "Locked", user_active=False)
        lead = _lead(org_a)
        lead.assigned_to.add(left, locked)
        assert not _alerts().exists()

    def test_a_member_of_another_org_is_not_told(self, org_a, org_b):
        stranger = _member(org_b, "Stranger")
        lead = _lead(org_a)
        lead.assigned_to.add(stranger)
        assert not _alerts().exists()

    def test_removing_someone_alerts_no_one(self, asha, ravi, org_a):
        lead = _lead(org_a)
        lead.assigned_to.add(asha, ravi)
        Notification.objects.all().delete()
        lead.assigned_to.remove(ravi)
        lead.assigned_to.clear()
        assert not _alerts().exists()


def _rotation_form(org, creator, members):
    form = WebForm.objects.create(
        name="Contact us",
        org=org,
        is_published=True,
        assignment_mode=WebForm.ASSIGN_ROTATION,
        created_by=creator.user,
    )
    form.rotation_members.set(members)
    WebFormField.objects.create(
        form=form,
        org=org,
        order=0,
        source=WebFormField.SOURCE_LEAD,
        lead_field="email",
        label="Email",
    )
    return form


# ---------------------------------------------------------------------------
# quiet()
# ---------------------------------------------------------------------------


class TestQuiet:
    def test_quiet_nests(self, asha, ravi, org_a):
        lead = _lead(org_a)
        with alerts.quiet():
            with alerts.quiet():
                lead.assigned_to.add(asha)
            lead.assigned_to.add(ravi)
        assert not _alerts().exists()
        lead.assigned_to.remove(asha)
        lead.assigned_to.add(asha)
        assert _told(alerts.ASSIGNED) == {asha}

    def test_quiet_suppresses_import_alerts(self, admin_client, asha, org_a):
        csv_text = "first_name,last_name,email,assigned_emails\n" + "".join(
            f"Lead{i},X,lead{i}@example.com,{asha.user.email}\n" for i in range(3)
        )
        upload = SimpleUploadedFile(
            "leads.csv", csv_text.encode(), content_type="text/csv"
        )
        response = admin_client.post(
            "/api/leads/import/commit/", {"file": upload}, format="multipart"
        )
        assert response.status_code == 200, response.content
        assert Lead.objects.filter(org=org_a, assigned_to=asha).count() == 3
        assert not _alerts().exists()

    def test_quiet_suppresses_sample_data_alerts(self, admin_profile, org_a, pushes):
        """Applied outside a request there is no assigner, so without
        `quiet()` the admin would be told about every sample lead."""
        _device(admin_profile)
        apply_pack(org_a, get_pack("real-estate"), admin_profile)
        assert Lead.objects.filter(org=org_a, assigned_to=admin_profile).exists()
        assert not _alerts().exists()
        assert pushes == []

    def test_joining_a_team_alerts_no_one(self, asha, ravi, org_a):
        from common.models import Teams
        from common.tasks import update_team_users

        team = Teams.objects.create(name="Field", org=org_a)
        leads = [_lead(org_a, email=f"t{i}@example.com") for i in range(3)]
        for lead in leads:
            lead.teams.add(team)
        team.users.add(asha, ravi)
        # The task stops at the team's deals on a misspelt related name, a
        # separate bug left for its own fix; the leads come before that.
        with contextlib.suppress(AttributeError):
            update_team_users(team.id, org_a.id)
        assert Lead.objects.filter(org=org_a, assigned_to=asha).count() == 3
        assert not _alerts().exists()

    def test_quiet_suppresses_merge_alerts(
        self, admin_client, admin_profile, asha, org_a, monkeypatch
    ):
        """Whatever a merge writes, nobody is told: the people on either
        record already were."""
        keeper = _lead(org_a, email="keeper@example.com")
        loser = _lead(org_a, email="loser@example.com")

        def merge_and_assign(kept, lost):
            merge_records(kept, lost)
            kept.assigned_to.add(asha)

        monkeypatch.setattr(
            "common.views.duplicate_views.merge_records", merge_and_assign
        )
        response = admin_client.post(
            f"/api/leads/{keeper.id}/merge/",
            {"merge_id": str(loser.id)},
            format="json",
        )
        assert response.status_code == 200, response.content
        assert list(keeper.assigned_to.all()) == [asha]
        assert not _alerts().exists()


# ---------------------------------------------------------------------------
# Intake: a new lead nobody owns, and a repeat enquiry
# ---------------------------------------------------------------------------


class TestIntake:
    def test_intake_lead_with_nobody_on_it_alerts_admins_a_minute_later(
        self, admin_profile, asha, org_a, django_capture_on_commit_callbacks, later
    ):
        second_admin = _member(org_a, "Ines", role="ADMIN")
        _member(org_a, "Gone", role="ADMIN", active=False)
        with django_capture_on_commit_callbacks(execute=False) as callbacks:
            result = receive_lead(org_a, _incoming())
        assert later.queued == []
        for callback in callbacks:
            callback()
        assert later.queued == [
            ((str(result.lead.id), str(org_a.id)), {"countdown": 60})
        ]
        assert not _alerts().exists()

        later.run()

        assert result.result == "created"
        assert _told(alerts.CREATED) == {admin_profile, second_admin}
        row = _alerts(alerts.CREATED).first()
        assert row.link == f"/leads/{result.lead.id}"
        assert row.entity_name == "Pat"
        assert row.data == {"source": "Google ads"}

    def test_not_when_someone_took_it_within_the_minute(
        self, admin_profile, asha, org_a, commit, later
    ):
        """An automation rule assigns an intake lead after the intake's
        transaction commits, so only the person it picked hears of it."""
        with commit():
            result = receive_lead(org_a, _incoming())
        result.lead.assigned_to.add(asha)
        later.run()
        assert not _alerts(alerts.CREATED).exists()
        assert _told(alerts.ASSIGNED) == {asha}

    def test_admins_when_the_only_owner_cannot_sign_in(
        self, admin_profile, org_a, commit, later
    ):
        """An owner whose user is deactivated is put on the lead but never
        told, so it is nobody's."""
        locked = _member(org_a, "Locked", user_active=False)
        with commit():
            result = receive_lead(org_a, _incoming(assignee=locked))
        assert list(result.lead.assigned_to.all()) == [locked]
        later.run()
        assert _told(alerts.CREATED) == {admin_profile}

    def test_the_org_context_is_set_before_the_first_query(
        self, admin_profile, org_a, monkeypatch
    ):
        lead = _lead(org_a)
        calls = []
        with CaptureQueriesContext(connection) as ctx:

            def record(org_id):
                read = any('"lead"' in q["sql"] for q in ctx.captured_queries)
                calls.append(("set", str(org_id), read))

            monkeypatch.setattr(alerts, "set_rls_context", record)
            monkeypatch.setattr(
                alerts, "clear_rls_context", lambda: calls.append(("clear",))
            )
            alerts.notify_created(str(lead.id), str(org_a.id))

        assert calls == [("set", str(org_a.id), False), ("clear",)]
        assert _told(alerts.CREATED) == {admin_profile}

    def test_admins_means_role_admin_or_superuser(self, admin_profile, org_a):
        boss = _member(org_a, "Boss")
        User.objects.filter(pk=boss.user_id).update(is_superuser=True)
        _member(org_a, "Plain")
        lead = _lead(org_a)
        alerts.notify_created(str(lead.id), str(org_a.id))
        assert _told(alerts.CREATED) == {admin_profile, boss}

    def test_nothing_for_a_lead_gone_within_the_minute(
        self, admin_profile, org_a, commit, later
    ):
        with commit():
            first = receive_lead(org_a, _incoming()).lead
            second = receive_lead(
                org_a, _incoming(values={"email": "sam@example.com"}, external_id="2")
            ).lead
        Lead.objects.filter(pk=first.pk).delete()
        Lead.objects.filter(pk=second.pk).update(is_active=False)
        later.run()
        assert not _alerts().exists()

    def test_not_when_rotation_assigned_it(
        self, admin_profile, asha, org_a, commit, later
    ):
        form = _rotation_form(org_a, admin_profile, [asha])
        with commit():
            submit_form(form, {"email": "pat@example.com"})
        later.run()
        assert not _alerts(alerts.CREATED).exists()

    def test_a_web_form_lead_with_nobody_on_it_alerts_admins(
        self, admin_profile, org_a, commit, later
    ):
        form = _rotation_form(org_a, admin_profile, [])
        with commit():
            submit_form(form, {"email": "pat@example.com"})
        later.run()
        assert _told(alerts.CREATED) == {admin_profile}

    def test_a_late_arrival_alerts_no_one(
        self, admin_profile, asha, org_a, commit, later
    ):
        old = timezone.now() - timedelta(days=3)
        with commit():
            receive_lead(org_a, _incoming(occurred_at=old))
            receive_lead(
                org_a,
                _incoming(
                    values={"email": "sam@example.com"},
                    external_id="2",
                    occurred_at=old,
                    assignee=asha,
                ),
            )
        assert later.queued == []
        assert not _alerts().exists()

    def test_nothing_is_queued_inside_quiet(self, admin_profile, org_a, commit, later):
        with commit(), alerts.quiet():
            receive_lead(org_a, _incoming())
        assert later.queued == []

    def test_repeat_enquiry_alerts_assignees(
        self, admin_profile, asha, ravi, org_a, commit, later
    ):
        lead = _lead(org_a, email="pat@example.com", source="google ads")
        lead.assigned_to.add(asha, ravi)
        Notification.objects.all().delete()

        with commit():
            result = receive_lead(org_a, _incoming())

        assert result.result == "merged"
        assert later.queued == []
        assert _told(alerts.ENQUIRED) == {asha, ravi}
        assert not _alerts(alerts.CREATED).exists()
        assert {row.link for row in _alerts(alerts.ENQUIRED)} == {f"/leads/{lead.id}"}


# ---------------------------------------------------------------------------
# Switches: email and push
# ---------------------------------------------------------------------------


class TestSwitches:
    def test_email_switch_off_skips_both_emails(self, asha, ravi, org_a):
        Profile.objects.filter(pk=asha.pk).update(lead_alerts_email=False)
        lead = _lead(org_a)
        lead.assigned_to.add(asha, ravi)

        send_email_to_assigned_user([str(asha.id), str(ravi.id)], lead.id, org_a.id)
        assert [m.to for m in mail.outbox] == [[ravi.user.email]]

        mail.outbox.clear()
        send_enquired_again_email(str(org_a.id), lead_id=str(lead.id))
        assert [m.to for m in mail.outbox] == [[ravi.user.email]]

        mail.outbox.clear()
        Profile.objects.filter(pk=ravi.pk).update(lead_alerts_email=False)
        send_email_to_assigned_user([str(asha.id), str(ravi.id)], lead.id, org_a.id)
        send_enquired_again_email(str(org_a.id), lead_id=str(lead.id))
        assert mail.outbox == []

    def test_email_switch_off_skips_the_web_form_email_to_assignees(
        self, admin_profile, asha, ravi, org_a
    ):
        """The form's notify list asked to hear of every submission, so the
        switch does not take a member off it."""
        Profile.objects.filter(pk__in=[asha.pk, ravi.pk]).update(
            lead_alerts_email=False
        )
        form = _rotation_form(org_a, admin_profile, [asha])
        form.notify_profiles.add(ravi)
        submission = submit_form(form, {"email": "pat@example.com"})
        assert list(submission.lead.assigned_to.all()) == [asha]

        mail.outbox.clear()
        send_webform_submission_email(str(submission.id), str(org_a.id))
        assert [m.recipients() for m in mail.outbox] == [[ravi.user.email]]

        Profile.objects.filter(pk=asha.pk).update(lead_alerts_email=True)
        mail.outbox.clear()
        send_webform_submission_email(str(submission.id), str(org_a.id))
        assert [sorted(m.recipients()) for m in mail.outbox] == [
            [asha.user.email, ravi.user.email]
        ]

    def test_push_goes_to_the_newly_assigned_with_the_exact_text(
        self, asha, org_a, pushes, commit
    ):
        _device(asha)
        lead = _lead(org_a, source="google ads")
        with commit():
            lead.assigned_to.add(asha)
        assert pushes == [
            (
                str(asha.id),
                "BottleCRM",
                "New lead assigned: Maria Lopez (Google ads)",
                f"/leads/{lead.id}",
                alerts.ASSIGNED,
            )
        ]

    def test_push_waits_for_the_commit(
        self, asha, org_a, pushes, django_capture_on_commit_callbacks
    ):
        _device(asha)
        lead = _lead(org_a)
        with django_capture_on_commit_callbacks(execute=False) as callbacks:
            lead.assigned_to.add(asha)
        assert pushes == []
        assert len(callbacks) == 1

    def test_push_switch_off_sends_no_push(self, asha, org_a, pushes, commit):
        _device(asha)
        Profile.objects.filter(pk=asha.pk).update(lead_alerts_push=False)
        asha.refresh_from_db()
        with commit():
            _lead(org_a).assigned_to.add(asha)
        assert pushes == []
        assert _told(alerts.ASSIGNED) == {asha}

    def test_push_not_configured_schedules_nothing(
        self, asha, org_a, monkeypatch, django_capture_on_commit_callbacks
    ):
        _device(asha)
        monkeypatch.setattr(alerts, "push_configured", lambda: False)
        queued = []
        monkeypatch.setattr(
            alerts.send_push, "delay", lambda *a, **k: queued.append((a, k))
        )
        with django_capture_on_commit_callbacks(execute=True):
            _lead(org_a).assigned_to.add(asha)
        assert queued == []
        assert _told(alerts.ASSIGNED) == {asha}

    def test_no_push_without_a_registered_phone(self, asha, org_a, pushes, commit):
        with commit():
            _lead(org_a).assigned_to.add(asha)
        assert pushes == []

    def test_quiet_sends_no_push(self, asha, org_a, pushes, commit):
        _device(asha)
        with commit(), alerts.quiet():
            _lead(org_a).assigned_to.add(asha)
        assert pushes == []


# ---------------------------------------------------------------------------
# Wording
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("verb", "source", "text"),
    [
        (alerts.ASSIGNED, "Google ads", "New lead assigned: Maria Lopez (Google ads)"),
        (alerts.CREATED, "Google ads", "New lead: Maria Lopez (Google ads)"),
        (alerts.ENQUIRED, "Google ads", "Maria Lopez enquired again (Google ads)"),
        (alerts.WAITING, "Google ads", "Maria Lopez is waiting for a reply"),
        (alerts.ASSIGNED, "", "New lead assigned: Maria Lopez"),
        (alerts.CREATED, "", "New lead: Maria Lopez"),
        (alerts.ENQUIRED, "", "Maria Lopez enquired again"),
    ],
)
def test_push_text(verb, source, text):
    assert alerts.push_body(verb, "Maria Lopez", source) == text


def test_the_name_is_one_short_line(org_a):
    lead = _lead(org_a, first_name="Maria\n{x}", last_name="L" * 255)
    name = alerts.display_name(lead)
    assert "\n" not in name and len(name) <= 100
    assert name.startswith("Maria {x} L")
    assert alerts.display_name(_lead(org_a, first_name="", last_name="")) == "Lead"
    assert (
        alerts.display_name(
            _lead(org_a, first_name=None, last_name=None, email="x@example.com")
        )
        == "x@example.com"
    )


def test_an_unknown_source_key_is_shown_as_stored(asha, org_a):
    lead = _lead(org_a, source="from a fair")
    lead.assigned_to.add(asha)
    assert _alerts(alerts.ASSIGNED).get().data == {"source": "from a fair"}


def test_the_alert_data_is_json(asha, org_a):
    lead = _lead(org_a)
    lead.assigned_to.add(asha)
    row = _alerts(alerts.ASSIGNED).get()
    assert json.loads(json.dumps(row.data)) == {"source": ""}
