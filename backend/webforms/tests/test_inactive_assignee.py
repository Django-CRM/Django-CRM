"""A form's ``assign_to`` only counts while that member is active.

Deactivating a member left every web form pointing at them still handing that
member each new lead, crediting them as its creator and as the author of the
merge comment, attaching them to the legacy endpoint's contact, and mailing
them the submission. A deactivated member can no longer sign in to see any of
it, and the mail carries the prospect's details to somebody who has left.

An inactive assignee is now treated exactly as no assignee: the lead is left
unassigned and credited to the form's creator, which is what a form with no
assignee has always done. The admin API also refuses to save one, and the
notification skips inactive ``notify_profiles`` for the same reason.
"""

import pytest
from django.contrib.contenttypes.models import ContentType
from django.core import mail

from common.models import APISettings, Comment, Profile, User
from contacts.models import Contact
from leads.models import Lead
from webforms.models import WebForm, WebFormField
from webforms.service import submit_form
from webforms.tasks import send_webform_submission_email

LIST_URL = "/api/webforms/"


@pytest.fixture
def inactive_profile(org_a):
    user = User.objects.create_user(email="gone@test.com", password="testpass123")
    return Profile.objects.create(user=user, org=org_a, role="USER", is_active=False)


def _form(org, assignee, created_by, **extra):
    form = WebForm.objects.create(
        name="Contact us",
        org=org,
        is_published=True,
        assign_to=assignee,
        lead_source="other",
        created_by=created_by,
        **extra,
    )
    for order, name in enumerate(["first_name", "last_name", "email", "description"]):
        WebFormField.objects.create(
            form=form,
            org=org,
            order=order,
            source=WebFormField.SOURCE_LEAD,
            lead_field=name,
            label=name.replace("_", " ").title(),
        )
    return form


@pytest.mark.django_db
class TestSubmission:
    def test_an_inactive_assignee_is_treated_as_no_assignee(
        self, org_a, admin_user, inactive_profile
    ):
        form = _form(org_a, inactive_profile, admin_user)

        lead = submit_form(form, {"email": "pat@example.com"}).lead

        assert lead.assigned_to.count() == 0
        assert lead.created_by == admin_user

    def test_an_active_assignee_is_still_assigned(
        self, org_a, admin_user, user_profile
    ):
        form = _form(org_a, user_profile, admin_user)

        lead = submit_form(form, {"email": "pat@example.com"}).lead

        assert list(lead.assigned_to.all()) == [user_profile]
        assert lead.created_by == user_profile.user

    def test_the_merge_comment_is_not_credited_to_an_inactive_assignee(
        self, org_a, admin_user, inactive_profile
    ):
        form = _form(org_a, inactive_profile, admin_user)
        submit_form(form, {"email": "pat@example.com"})

        lead = submit_form(
            form, {"email": "pat@example.com", "description": "Again"}
        ).lead

        comment = Comment.objects.get(
            content_type=ContentType.objects.get_for_model(Lead), object_id=lead.id
        )
        assert comment.commented_by is None


@pytest.mark.django_db
class TestNotification:
    def test_an_inactive_assignee_is_not_mailed(
        self, org_a, admin_user, inactive_profile, user_profile
    ):
        form = _form(org_a, inactive_profile, admin_user)
        form.notify_profiles.add(user_profile)
        submission = submit_form(form, {"email": "pat@example.com"})
        mail.outbox.clear()

        send_webform_submission_email(str(submission.id), str(org_a.id))

        assert [m.recipients() for m in mail.outbox] == [[user_profile.user.email]]

    def test_an_inactive_notify_profile_is_not_mailed(
        self, org_a, admin_user, admin_profile, inactive_profile
    ):
        form = _form(org_a, admin_profile, admin_user)
        form.notify_profiles.add(inactive_profile)
        submission = submit_form(form, {"email": "pat@example.com"})
        mail.outbox.clear()

        send_webform_submission_email(str(submission.id), str(org_a.id))

        assert [m.recipients() for m in mail.outbox] == [[admin_profile.user.email]]


@pytest.mark.django_db
class TestLegacyEndpoint:
    def _setting(self, org_a, admin_user, assignee):
        setting = APISettings.objects.create(
            title="Site",
            website="https://example.com",
            org=org_a,
            created_by=admin_user,
        )
        _form(org_a, assignee, admin_user, legacy_api_setting=setting)
        return setting

    @pytest.mark.parametrize("active", [True, False])
    def test_the_contact_is_assigned_only_to_an_active_assignee(
        self, admin_client, org_a, admin_user, user_profile, active
    ):
        user_profile.is_active = active
        user_profile.save(update_fields=["is_active"])
        setting = self._setting(org_a, admin_user, user_profile)

        response = admin_client.post(
            "/api/leads/create-from-site/",
            {"apikey": setting.apikey, "email": "pat@example.com"},
            format="json",
        )

        assert response.status_code == 200, response.data
        contact = Contact.objects.get(org=org_a, email="pat@example.com")
        expected = [user_profile] if active else []
        assert list(contact.assigned_to.all()) == expected


@pytest.mark.django_db
class TestAdminApi:
    def test_create_refuses_an_inactive_assignee(self, admin_client, inactive_profile):
        response = admin_client.post(
            LIST_URL,
            {"name": "Probe", "assign_to": str(inactive_profile.id)},
            format="json",
        )

        assert response.status_code == 400
        assert "assign_to" in response.data
        assert not WebForm.objects.filter(name="Probe").exists()

    def test_update_refuses_an_inactive_assignee(
        self, admin_client, org_a, admin_user, admin_profile, inactive_profile
    ):
        form = _form(org_a, admin_profile, admin_user)

        response = admin_client.put(
            f"{LIST_URL}{form.id}/",
            {"assign_to": str(inactive_profile.id)},
            format="json",
        )

        assert response.status_code == 400
        assert "assign_to" in response.data
        form.refresh_from_db()
        assert form.assign_to == admin_profile

    def test_an_active_assignee_is_accepted(self, admin_client, user_profile):
        response = admin_client.post(
            LIST_URL,
            {"name": "Ours", "assign_to": str(user_profile.id)},
            format="json",
        )

        assert response.status_code == 201, response.data
        assert WebForm.objects.get(id=response.data["id"]).assign_to == user_profile


@pytest.mark.django_db
class TestAdminApiKeepsAStoredInactiveAssignee:
    """Clients resend the stored `assign_to` on every save, so a form whose
    assignee was deactivated after being chosen must still save. Keeping that
    value is harmless (`active_assignee` ignores it); choosing a new inactive
    or foreign one is still refused."""

    @pytest.fixture
    def form(self, org_a, admin_user, inactive_profile):
        return _form(org_a, inactive_profile, admin_user)

    def _put(self, client, form, assignee_id):
        return client.put(
            f"{LIST_URL}{form.id}/",
            {"name": "Renamed", "assign_to": assignee_id},
            format="json",
        )

    def test_resending_the_stored_inactive_assignee_saves(
        self, admin_client, form, inactive_profile
    ):
        response = self._put(admin_client, form, str(inactive_profile.id))

        assert response.status_code == 200, response.data
        form.refresh_from_db()
        assert form.name == "Renamed"
        assert form.assign_to == inactive_profile

    def test_changing_to_a_different_inactive_member_is_refused(
        self, admin_client, form, org_a, inactive_profile
    ):
        user = User.objects.create_user(email="gone2@test.com", password="pw123456")
        other = Profile.objects.create(
            user=user, org=org_a, role="USER", is_active=False
        )

        response = self._put(admin_client, form, str(other.id))

        assert response.status_code == 400
        assert "assign_to" in response.data
        form.refresh_from_db()
        assert form.assign_to == inactive_profile

    def test_changing_to_another_orgs_profile_is_refused(
        self, admin_client, form, profile_b, inactive_profile
    ):
        response = self._put(admin_client, form, str(profile_b.id))

        assert response.status_code == 400
        assert "assign_to" in response.data
        form.refresh_from_db()
        assert form.assign_to == inactive_profile

    def test_changing_to_an_active_member_saves(self, admin_client, form, user_profile):
        response = self._put(admin_client, form, str(user_profile.id))

        assert response.status_code == 200, response.data
        form.refresh_from_db()
        assert form.assign_to == user_profile

    def test_a_new_form_cannot_start_with_an_inactive_assignee(
        self, admin_client, form, inactive_profile
    ):
        # "Unchanged" means unchanged on this record, so a create, which has no
        # stored value, is still refused even when another form points at them.
        response = admin_client.post(
            LIST_URL,
            {"name": "Probe", "assign_to": str(inactive_profile.id)},
            format="json",
        )

        assert response.status_code == 400
        assert "assign_to" in response.data


@pytest.mark.django_db
class TestAssigneeDetails:
    """The picker lists active members only, so a client needs to be told who
    the stored assignee is, and whether they are still active, to offer them
    as an option rather than silently clearing the field on save."""

    def test_an_inactive_assignee_is_described(
        self, admin_client, org_a, admin_user, inactive_profile
    ):
        form = _form(org_a, inactive_profile, admin_user)

        response = admin_client.get(f"{LIST_URL}{form.id}/")

        assert response.status_code == 200
        assert response.data["assign_to_details"] == {
            "id": str(inactive_profile.id),
            "email": "gone@test.com",
            "name": inactive_profile.user.name,
            "is_active": False,
        }

    def test_an_active_assignee_is_described_as_active(
        self, admin_client, org_a, admin_user, user_profile
    ):
        form = _form(org_a, user_profile, admin_user)

        response = admin_client.get(f"{LIST_URL}{form.id}/")

        assert response.data["assign_to_details"]["is_active"] is True

    def test_no_assignee_is_null(self, admin_client, org_a, admin_user):
        form = _form(org_a, None, admin_user)

        response = admin_client.get(f"{LIST_URL}{form.id}/")

        assert response.data["assign_to_details"] is None

    def test_it_cannot_be_written(
        self, admin_client, org_a, admin_user, user_profile, inactive_profile
    ):
        form = _form(org_a, user_profile, admin_user)

        response = admin_client.put(
            f"{LIST_URL}{form.id}/",
            {"assign_to_details": {"id": str(inactive_profile.id)}},
            format="json",
        )

        assert response.status_code == 200, response.data
        form.refresh_from_db()
        assert form.assign_to == user_profile
