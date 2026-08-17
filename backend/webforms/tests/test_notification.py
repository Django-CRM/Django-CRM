"""Who gets told about a submission.

This is the fix for the legacy endpoint's fourth defect: `APISettings` has a
`lead_assigned_to` M2M that `CreateLeadFromSite` never reads, so for the whole
life of that feature the people configured to be notified were notified of
nothing.
"""

import uuid

import pytest
from django.core import mail

from webforms.models import WebForm, WebFormField, WebFormSubmission
from webforms.service import submit_form
from webforms.tasks import send_webform_submission_email


@pytest.fixture
def form(org_a, admin_profile):
    web_form = WebForm.objects.create(
        name="Contact us", org=org_a, is_published=True, assign_to=admin_profile
    )
    for order, name in enumerate(["email", "first_name", "description"]):
        WebFormField.objects.create(
            form=web_form,
            org=org_a,
            order=order,
            source=WebFormField.SOURCE_LEAD,
            lead_field=name,
            label=name.title(),
        )
    return web_form


@pytest.mark.django_db
class TestRecipients:
    def test_notifies_the_configured_recipients(self, form, org_a, user_profile):
        form.notify_profiles.add(user_profile)
        submission = submit_form(form, {"email": "pat@example.com"})
        mail.outbox.clear()
        send_webform_submission_email(str(submission.id), str(org_a.id))
        assert user_profile.user.email in mail.outbox[0].recipients()

    def test_notifies_the_assignee_too(self, form, org_a, admin_profile):
        submission = submit_form(form, {"email": "pat@example.com"})
        mail.outbox.clear()
        send_webform_submission_email(str(submission.id), str(org_a.id))
        assert admin_profile.user.email in mail.outbox[0].recipients()

    def test_an_assignee_who_is_also_a_recipient_gets_one_email_not_two(
        self, form, org_a, admin_profile
    ):
        form.notify_profiles.add(admin_profile)
        submission = submit_form(form, {"email": "pat@example.com"})
        mail.outbox.clear()
        send_webform_submission_email(str(submission.id), str(org_a.id))
        assert len(mail.outbox) == 1
        assert mail.outbox[0].recipients().count(admin_profile.user.email) == 1

    def test_no_recipients_sends_nothing_rather_than_raising(self, form, org_a):
        form.assign_to = None
        form.save(update_fields=["assign_to"])
        submission = submit_form(form, {"email": "pat@example.com"})
        mail.outbox.clear()
        send_webform_submission_email(str(submission.id), str(org_a.id))
        assert mail.outbox == []

    def test_a_profile_with_no_email_address_is_skipped(
        self, form, org_a, admin_profile
    ):
        admin_profile.user.email = ""
        admin_profile.user.save(update_fields=["email"])
        submission = submit_form(form, {"email": "pat@example.com"})
        mail.outbox.clear()
        send_webform_submission_email(str(submission.id), str(org_a.id))
        assert mail.outbox == []


@pytest.mark.django_db
class TestContent:
    def test_the_subject_names_the_form(self, form, org_a):
        submission = submit_form(form, {"email": "pat@example.com"})
        mail.outbox.clear()
        send_webform_submission_email(str(submission.id), str(org_a.id))
        assert "Contact us" in mail.outbox[0].subject

    def test_the_body_carries_the_submitted_values(self, form, org_a):
        submission = submit_form(
            form, {"email": "pat@example.com", "first_name": "Pat"}
        )
        mail.outbox.clear()
        send_webform_submission_email(str(submission.id), str(org_a.id))
        body = mail.outbox[0].alternatives[0][0]
        assert "pat@example.com" in body
        assert "Pat" in body

    def test_the_body_links_to_the_lead_on_the_frontend(self, form, org_a):
        submission = submit_form(form, {"email": "pat@example.com"})
        mail.outbox.clear()
        send_webform_submission_email(str(submission.id), str(org_a.id))
        body = mail.outbox[0].alternatives[0][0]
        assert f"/leads/{submission.lead_id}" in body

    def test_a_submitted_script_tag_is_escaped_rather_than_rendered(self, form, org_a):
        """The payload is submitter-controlled and lands in an HTML email that
        a colleague opens. Django autoescapes; this is the test that says so,
        because a `|safe` added later would look harmless."""
        submission = submit_form(
            form,
            {"email": "pat@example.com", "first_name": "<script>alert(1)</script>"},
        )
        mail.outbox.clear()
        send_webform_submission_email(str(submission.id), str(org_a.id))
        body = mail.outbox[0].alternatives[0][0]
        assert "<script>alert(1)</script>" not in body
        assert "&lt;script&gt;" in body

    def test_a_duplicate_says_so(self, form, org_a):
        submit_form(form, {"email": "pat@example.com"})
        second = submit_form(form, {"email": "pat@example.com"})
        mail.outbox.clear()
        send_webform_submission_email(str(second.id), str(org_a.id))
        body = mail.outbox[0].alternatives[0][0]
        assert "merged" in body.lower()


@pytest.mark.django_db
class TestRejectedSubmissions:
    def test_a_spam_rejection_notifies_nobody(self, form, org_a):
        """Telling an org about every bot that hits their form is how they
        learn to ignore the notification."""
        submission = submit_form(
            form, {}, rejected=WebFormSubmission.REJECTED_SPAM, reason="honeypot"
        )
        mail.outbox.clear()
        send_webform_submission_email(str(submission.id), str(org_a.id))
        assert mail.outbox == []

    def test_an_invalid_rejection_notifies_nobody(self, form, org_a):
        submission = submit_form(
            form, {}, rejected=WebFormSubmission.REJECTED_INVALID, reason="bad input"
        )
        mail.outbox.clear()
        send_webform_submission_email(str(submission.id), str(org_a.id))
        assert mail.outbox == []


@pytest.mark.django_db
class TestRobustness:
    def test_a_deleted_submission_is_a_no_op_not_a_crash(self, org_a):
        mail.outbox.clear()
        send_webform_submission_email(str(uuid.uuid4()), str(org_a.id))
        assert mail.outbox == []

    def test_a_submission_from_another_org_is_not_sent(self, form, org_a, org_b):
        """The org id is an argument here, so a wrong one must find nothing
        rather than notifying across the tenant boundary."""
        submission = submit_form(form, {"email": "pat@example.com"})
        mail.outbox.clear()
        send_webform_submission_email(str(submission.id), str(org_b.id))
        assert mail.outbox == []
