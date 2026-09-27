"""Lead rotation on a web form (G23).

A lead form in `rotation` mode hands each NEW lead to the next eligible member
of `rotation_members`: active, in the form's org, and under `rotation_cap` open
leads when a cap is set. Members are taken in id order and the cursor
(`rotation_last_assigned`) names the last person served, so adding or removing
a member never shifts whose turn it is. Nobody eligible leaves the lead
unassigned; a repeat submission merges into the existing lead and never
reassigns it.

Profile ids are random UUIDs, so every expected order below is computed by
sorting the ids rather than written out.
"""

import threading
import time
from unittest import mock

import pytest
from django.core import mail
from django.db import connection
from django.db.models import QuerySet

from common.models import APISettings, Profile, User
from contacts.models import Contact
from leads.models import Lead
from webforms import service
from webforms.models import WebForm, WebFormField
from webforms.service import submit_form
from webforms.tasks import send_webform_submission_email

LIST_URL = "/api/webforms/"


def _member(org, email, *, active=True, role="USER"):
    user = User.objects.create_user(email=email, password="testpass123")
    return Profile.objects.create(user=user, org=org, role=role, is_active=active)


def _by_id(*profiles):
    return sorted(profiles, key=lambda p: p.id)


def _form(org, created_by, members, *, cap=None, mode=WebForm.ASSIGN_ROTATION, **kw):
    form = WebForm.objects.create(
        name="Contact us",
        org=org,
        is_published=True,
        assignment_mode=mode,
        rotation_cap=cap,
        created_by=created_by,
        **kw,
    )
    form.rotation_members.set(members)
    for order, name in enumerate(["email", "first_name", "description"]):
        WebFormField.objects.create(
            form=form,
            org=org,
            order=order,
            source=WebFormField.SOURCE_LEAD,
            lead_field=name,
            label=name.title(),
        )
    return form


_sequence = iter(range(10_000))


def _submit(form, email=None):
    """One new lead (or a merge, given a repeat email). Returns the submission."""
    email = email or f"visitor{next(_sequence)}@example.com"
    form.refresh_from_db()
    return submit_form(form, {"email": email})


def _assignees(submission):
    return list(submission.lead.assigned_to.all())


@pytest.fixture
def three(org_a):
    return _by_id(
        _member(org_a, "m1@test.com"),
        _member(org_a, "m2@test.com"),
        _member(org_a, "m3@test.com"),
    )


@pytest.mark.django_db
class TestTurnOrder:
    def test_leads_go_round_in_id_order_and_wrap(self, org_a, admin_user, three):
        form = _form(org_a, admin_user, three)

        got = [_assignees(_submit(form)) for _ in range(4)]

        assert got == [[three[0]], [three[1]], [three[2]], [three[0]]]
        form.refresh_from_db()
        assert form.rotation_last_assigned == three[0]

    def test_a_member_added_mid_rotation_takes_their_turn_by_id(
        self, org_a, admin_user, three
    ):
        low, middle, high = three
        form = _form(org_a, admin_user, [low, high])
        assert _assignees(_submit(form)) == [low]

        form.rotation_members.add(middle)

        assert _assignees(_submit(form)) == [middle]
        assert _assignees(_submit(form)) == [high]
        assert _assignees(_submit(form)) == [low]

    def test_removing_the_last_served_member_does_not_skip_the_next(
        self, org_a, admin_user, three
    ):
        """A stored list index would shift here and hand `low` a second lead
        before `high` had one. The cursor names a person, so it does not."""
        low, middle, high = three
        form = _form(org_a, admin_user, three)
        _submit(form)
        assert _assignees(_submit(form)) == [middle]

        form.rotation_members.remove(middle)

        assert _assignees(_submit(form)) == [high]
        assert _assignees(_submit(form)) == [low]

    def test_the_lead_is_credited_to_the_member_it_went_to(
        self, org_a, admin_user, three
    ):
        form = _form(org_a, admin_user, three)

        lead = _submit(form).lead

        assert lead.created_by == three[0].user

    def test_the_forms_person_is_ignored_while_rotating(
        self, org_a, admin_user, admin_profile, three
    ):
        form = _form(org_a, admin_user, [three[0]], assign_to=admin_profile)

        assert _assignees(_submit(form)) == [three[0]]


@pytest.mark.django_db
class TestEligibility:
    def test_an_inactive_member_is_skipped(self, org_a, admin_user, three):
        low, middle, high = three
        middle.is_active = False
        middle.save(update_fields=["is_active"])
        form = _form(org_a, admin_user, three)

        got = [_assignees(_submit(form)) for _ in range(3)]

        assert got == [[low], [high], [low]]

    def test_another_orgs_profile_is_never_assigned(
        self, org_a, admin_user, profile_b, three
    ):
        # The serializer refuses this; a row written some other way must still
        # never hand one org's lead to another org's member.
        form = _form(org_a, admin_user, [three[0], profile_b])

        got = [_assignees(_submit(form)) for _ in range(2)]

        assert got == [[three[0]], [three[0]]]

    def test_a_capped_member_is_skipped_until_a_lead_converts(
        self, org_a, admin_user, three
    ):
        low, high = three[0], three[2]
        form = _form(org_a, admin_user, [low, high], cap=1)
        first = _submit(form).lead
        assert list(first.assigned_to.all()) == [low]
        assert _assignees(_submit(form)) == [high]

        # Both at their cap: nobody is eligible.
        full = _submit(form)
        assert _assignees(full) == []
        form.refresh_from_db()
        assert form.rotation_last_assigned == high

        first.status = "converted"
        first.save(update_fields=["status"])

        assert _assignees(_submit(form)) == [low]

    def test_only_open_leads_count_against_the_cap(self, org_a, admin_user, three):
        low, middle = three[0], three[1]
        for status, active in [
            ("converted", True),
            ("closed", True),
            ("assigned", False),
        ]:
            lead = Lead.objects.create(
                org=org_a,
                title=f"{status} {active}",
                status=status,
                is_active=active,
            )
            lead.assigned_to.add(low)
        form = _form(org_a, admin_user, [low, middle], cap=1)

        assert _assignees(_submit(form)) == [low]

    @pytest.mark.parametrize("status", ["assigned", "in process", "recycled"])
    def test_an_open_lead_counts_against_the_cap(
        self, org_a, admin_user, three, status
    ):
        low, middle = three[0], three[1]
        lead = Lead.objects.create(org=org_a, title="Open", status=status)
        lead.assigned_to.add(low)
        form = _form(org_a, admin_user, [low, middle], cap=1)

        assert _assignees(_submit(form)) == [middle]

    def test_nobody_eligible_leaves_the_lead_unassigned_and_still_notifies(
        self, org_a, admin_user, user_profile, three
    ):
        for member in three:
            member.is_active = False
            member.save(update_fields=["is_active"])
        form = _form(org_a, admin_user, three)
        form.notify_profiles.add(user_profile)

        submission = _submit(form)
        mail.outbox.clear()
        send_webform_submission_email(str(submission.id), str(org_a.id))

        assert _assignees(submission) == []
        assert submission.lead.created_by == admin_user
        form.refresh_from_db()
        assert form.rotation_last_assigned is None
        assert [m.recipients() for m in mail.outbox] == [[user_profile.user.email]]


@pytest.mark.django_db
class TestOtherPathsAreUnchanged:
    def test_person_mode_ignores_the_members_and_the_cursor(
        self, org_a, admin_user, user_profile, three
    ):
        form = _form(
            org_a,
            admin_user,
            three,
            mode=WebForm.ASSIGN_PERSON,
            assign_to=user_profile,
        )

        assert _assignees(_submit(form)) == [user_profile]
        form.refresh_from_db()
        assert form.rotation_last_assigned is None

    def test_a_repeat_email_merges_without_reassigning_or_turning(
        self, org_a, admin_user, three
    ):
        form = _form(org_a, admin_user, three)
        first = _submit(form, "pat@example.com")

        again = _submit(form, "pat@example.com")

        assert again.lead_id == first.lead_id
        assert _assignees(again) == [three[0]]
        form.refresh_from_db()
        assert form.rotation_last_assigned == three[0]
        assert _assignees(_submit(form)) == [three[1]]

    def test_the_merge_comment_is_not_credited_to_a_leftover_person(
        self, org_a, admin_user, admin_profile, three
    ):
        from django.contrib.contenttypes.models import ContentType

        from common.models import Comment

        form = _form(org_a, admin_user, three, assign_to=admin_profile)
        _submit(form, "pat@example.com")
        lead = submit_form(
            form, {"email": "pat@example.com", "description": "Again"}
        ).lead

        comment = Comment.objects.get(
            content_type=ContentType.objects.get_for_model(Lead), object_id=lead.id
        )
        assert comment.commented_by is None

    def test_the_form_row_is_locked_before_the_cursor_is_read(
        self, org_a, admin_user, three
    ):
        """SQLite drops FOR UPDATE, so this asks which querysets were locked;
        the behaviour under real concurrency is the postgres_only test below."""
        form = _form(org_a, admin_user, three)
        original = QuerySet.select_for_update
        with mock.patch.object(
            QuerySet, "select_for_update", autospec=True, side_effect=original
        ) as spy:
            _submit(form)
        assert WebForm in {call.args[0].model for call in spy.call_args_list}


@pytest.mark.django_db
class TestNotification:
    def test_each_rotated_lead_mails_the_member_it_went_to(
        self, org_a, admin_user, three
    ):
        form = _form(org_a, admin_user, three[:2])
        _submit(form)
        second = _submit(form)
        mail.outbox.clear()

        send_webform_submission_email(str(second.id), str(org_a.id))

        assert [m.recipients() for m in mail.outbox] == [[three[1].user.email]]

    def test_a_merge_mails_the_leads_owner_not_the_forms_person(
        self, org_a, admin_user, admin_profile, user_profile
    ):
        form = _form(
            org_a, admin_user, [], mode=WebForm.ASSIGN_PERSON, assign_to=admin_profile
        )
        lead = Lead.objects.create(
            org=org_a, email="pat@example.com", status="assigned"
        )
        lead.assigned_to.add(user_profile)

        submission = _submit(form, "pat@example.com")
        mail.outbox.clear()
        send_webform_submission_email(str(submission.id), str(org_a.id))

        assert [m.recipients() for m in mail.outbox] == [[user_profile.user.email]]

    def test_an_inactive_lead_owner_is_not_mailed(
        self, org_a, admin_user, user_profile, three
    ):
        form = _form(org_a, admin_user, [three[0]])
        form.notify_profiles.add(user_profile)
        submission = _submit(form)
        three[0].is_active = False
        three[0].save(update_fields=["is_active"])
        mail.outbox.clear()

        send_webform_submission_email(str(submission.id), str(org_a.id))

        assert [m.recipients() for m in mail.outbox] == [[user_profile.user.email]]


@pytest.mark.django_db
class TestLegacyEndpoint:
    def test_create_from_site_rotates_and_the_new_contact_follows(
        self, admin_client, org_a, admin_user, three
    ):
        setting = APISettings.objects.create(
            title="Site",
            website="https://example.com",
            org=org_a,
            created_by=admin_user,
        )
        _form(org_a, admin_user, three[:2], legacy_api_setting=setting)

        for email in ["one@example.com", "two@example.com"]:
            response = admin_client.post(
                "/api/leads/create-from-site/",
                {"apikey": setting.apikey, "email": email},
                format="json",
            )
            assert response.status_code == 200, response.data

        for email, member in [
            ("one@example.com", three[0]),
            ("two@example.com", three[1]),
        ]:
            lead = Lead.objects.get(org=org_a, email=email)
            assert list(lead.assigned_to.all()) == [member]
            contact = Contact.objects.get(org=org_a, email=email)
            assert list(contact.assigned_to.all()) == [member]


@pytest.mark.django_db
class TestAdminApi:
    def _create(self, client, **body):
        return client.post(LIST_URL, {"name": "Probe", **body}, format="json")

    def _put(self, client, form, **body):
        return client.put(f"{LIST_URL}{form.id}/", body, format="json")

    def test_an_admin_creates_a_rotation_and_reads_it_back(
        self, admin_client, org_a, three
    ):
        response = self._create(
            admin_client,
            assignment_mode="rotation",
            rotation_members=[str(p.id) for p in three],
            rotation_cap=5,
        )

        assert response.status_code == 201, response.data
        form = WebForm.objects.get(id=response.data["id"])
        assert form.assignment_mode == "rotation"
        assert set(form.rotation_members.all()) == set(three)
        assert form.rotation_cap == 5
        assert [d["id"] for d in response.data["rotation_members_details"]] == [
            str(p.id) for p in three
        ]
        assert response.data["rotation_last_assigned_details"] is None

    def test_the_last_assigned_member_is_described(
        self, admin_client, org_a, admin_user, three
    ):
        form = _form(org_a, admin_user, three)
        _submit(form)

        data = admin_client.get(f"{LIST_URL}{form.id}/").data

        assert str(data["rotation_last_assigned"]) == str(three[0].id)
        assert data["rotation_last_assigned_details"] == {
            "id": str(three[0].id),
            "email": three[0].user.email,
            "name": three[0].user.name,
            "is_active": True,
        }

    def test_the_cursor_cannot_be_written(self, admin_client, org_a, admin_user, three):
        form = _form(org_a, admin_user, three)

        response = self._put(
            admin_client, form, rotation_last_assigned=str(three[2].id)
        )

        assert response.status_code == 200, response.data
        form.refresh_from_db()
        assert form.rotation_last_assigned is None

    def test_rotation_with_no_members_is_refused(self, admin_client):
        response = self._create(admin_client, assignment_mode="rotation")

        assert response.status_code == 400
        assert "rotation_members" in response.data

    def test_emptying_a_rotations_members_is_refused(
        self, admin_client, org_a, admin_user, three
    ):
        form = _form(org_a, admin_user, three)

        response = self._put(admin_client, form, rotation_members=[])

        assert response.status_code == 400
        assert "rotation_members" in response.data
        assert form.rotation_members.count() == 3

    def test_switching_to_rotation_keeps_stored_members(
        self, admin_client, org_a, admin_user, three
    ):
        form = _form(org_a, admin_user, three, mode=WebForm.ASSIGN_PERSON)

        response = self._put(admin_client, form, assignment_mode="rotation")

        assert response.status_code == 200, response.data
        form.refresh_from_db()
        assert form.assignment_mode == "rotation"

    def test_person_mode_needs_no_members(self, admin_client):
        response = self._create(admin_client, assignment_mode="person")

        assert response.status_code == 201, response.data

    def test_another_orgs_profile_is_refused(self, admin_client, profile_b):
        response = self._create(
            admin_client,
            assignment_mode="rotation",
            rotation_members=[str(profile_b.id)],
        )

        assert response.status_code == 400
        assert "rotation_members" in response.data
        assert not WebForm.objects.filter(name="Probe").exists()

    def test_adding_a_deactivated_member_is_refused(self, admin_client, org_a, three):
        gone = _member(org_a, "gone@test.com", active=False)

        response = self._create(
            admin_client,
            assignment_mode="rotation",
            rotation_members=[str(three[0].id), str(gone.id)],
        )

        assert response.status_code == 400
        assert "rotation_members" in response.data

    def test_a_stored_member_deactivated_since_is_kept_on_resend(
        self, admin_client, org_a, admin_user, three
    ):
        form = _form(org_a, admin_user, three)
        three[1].is_active = False
        three[1].save(update_fields=["is_active"])

        response = self._put(
            admin_client,
            form,
            name="Renamed",
            rotation_members=[str(p.id) for p in three],
        )

        assert response.status_code == 200, response.data
        assert set(form.rotation_members.all()) == set(three)
        details = {
            d["id"]: d["is_active"] for d in response.data["rotation_members_details"]
        }
        assert details[str(three[1].id)] is False

    def test_rotation_on_a_ticket_form_is_refused(self, admin_client, three):
        response = self._create(
            admin_client,
            target="ticket",
            assignment_mode="rotation",
            rotation_members=[str(three[0].id)],
        )

        assert response.status_code == 400
        assert "assignment_mode" in response.data

    def test_turning_a_rotation_form_into_a_ticket_form_is_refused(
        self, admin_client, org_a, admin_user, three
    ):
        form = WebForm.objects.create(
            name="Empty", org=org_a, assignment_mode="rotation", created_by=admin_user
        )
        form.rotation_members.set(three)

        response = self._put(admin_client, form, target="ticket")

        assert response.status_code == 400
        assert "assignment_mode" in response.data
        form.refresh_from_db()
        assert form.target == "lead"

    @pytest.mark.parametrize("cap", [0, -1, "many"])
    def test_a_cap_below_one_is_refused(self, admin_client, three, cap):
        response = self._create(
            admin_client,
            assignment_mode="rotation",
            rotation_members=[str(three[0].id)],
            rotation_cap=cap,
        )

        assert response.status_code == 400
        assert "rotation_cap" in response.data

    def test_a_cap_can_be_cleared(self, admin_client, org_a, admin_user, three):
        form = _form(org_a, admin_user, three, cap=3)

        response = self._put(admin_client, form, rotation_cap=None)

        assert response.status_code == 200, response.data
        form.refresh_from_db()
        assert form.rotation_cap is None

    def test_an_unknown_mode_is_refused(self, admin_client):
        response = self._create(admin_client, assignment_mode="round_robin")

        assert response.status_code == 400
        assert "assignment_mode" in response.data


@pytest.mark.django_db
class TestWhoMayChangeIt:
    """Read is open to every member; changing assignment is admin only, the
    rule `webforms/views.py:_may_write` applies to every write on a form."""

    @pytest.fixture
    def form(self, org_a, admin_user, three):
        return _form(org_a, admin_user, three[:1], mode=WebForm.ASSIGN_PERSON)

    def test_a_member_cannot_switch_on_rotation(self, user_client, form, three):
        response = user_client.put(
            f"{LIST_URL}{form.id}/",
            {
                "assignment_mode": "rotation",
                "rotation_members": [str(p.id) for p in three],
                "rotation_cap": 2,
            },
            format="json",
        )

        assert response.status_code == 403
        form.refresh_from_db()
        assert form.assignment_mode == "person"
        assert form.rotation_cap is None
        assert list(form.rotation_members.all()) == [three[0]]

    def test_an_admin_can(self, admin_client, form, three):
        response = admin_client.put(
            f"{LIST_URL}{form.id}/",
            {
                "assignment_mode": "rotation",
                "rotation_members": [str(p.id) for p in three],
                "rotation_cap": 2,
            },
            format="json",
        )

        assert response.status_code == 200, response.data
        form.refresh_from_db()
        assert form.assignment_mode == "rotation"
        assert form.rotation_cap == 2
        assert set(form.rotation_members.all()) == set(three)

    def test_a_member_can_read_the_rotation(self, user_client, form):
        response = user_client.get(f"{LIST_URL}{form.id}/")

        assert response.status_code == 200
        assert response.data["assignment_mode"] == "person"
        assert len(response.data["rotation_members_details"]) == 1

    def test_another_org_cannot_read_it(self, org_b_client, form):
        assert org_b_client.get(f"{LIST_URL}{form.id}/").status_code == 404


@pytest.mark.postgres_only
@pytest.mark.django_db(transaction=True)
def test_two_submissions_at_once_go_to_two_members(org_a, admin_user):
    """Both submissions start before either commits. The second waits on the
    form's row lock, then reads the cursor the first one moved. Without the
    lock both read "nobody yet" and the same member got both leads."""
    if connection.vendor != "postgresql":
        pytest.skip("row locks need PostgreSQL")

    from common.tasks import set_rls_context

    members = _by_id(_member(org_a, "c1@test.com"), _member(org_a, "c2@test.com"))
    form = _form(org_a, admin_user, members)
    barrier = threading.Barrier(2)
    errors = []
    original = service.rotation_assignee

    def slow_pick(*args, **kwargs):
        chosen = original(*args, **kwargs)
        time.sleep(0.5)
        return chosen

    def worker(email):
        try:
            set_rls_context(org_a.id)
            local = WebForm.objects.get(id=form.id)
            barrier.wait()
            submit_form(local, {"email": email})
        except Exception as exc:  # recorded and asserted on below
            errors.append(exc)
        finally:
            connection.close()

    with mock.patch("webforms.service.rotation_assignee", slow_pick):
        threads = [
            threading.Thread(target=worker, args=(f"race{i}@example.com",))
            for i in range(2)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

    assert errors == []
    set_rls_context(org_a.id)
    owners = sorted(
        profile.id
        for lead in Lead.objects.filter(org=org_a)
        for profile in lead.assigned_to.all()
    )
    assert owners == [m.id for m in members]
