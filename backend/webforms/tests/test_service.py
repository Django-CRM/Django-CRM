"""The single lead write path.

The duplicate-email case is the one worth reading closely. `Lead` carries
`UniqueConstraint(Lower("email"), "org")`, so a second submission from the same
address is not an edge case, it is the second time anyone fills in your contact
form. The legacy view calls `Lead.objects.create()` and raises IntegrityError
there today.
"""

import pytest
from django.contrib.contenttypes.models import ContentType

from common.models import Comment, CustomFieldDefinition, Tags
from leads.models import Lead
from webforms.models import WebForm, WebFormField, WebFormSubmission
from webforms.service import submit_form


def lead_comments(lead):
    return Comment.objects.filter(
        content_type=ContentType.objects.get_for_model(Lead), object_id=lead.id
    )


@pytest.fixture
def form(org_a, admin_profile):
    web_form = WebForm.objects.create(
        name="Contact us",
        org=org_a,
        is_published=True,
        assign_to=admin_profile,
        lead_source="other",
    )
    for order, name in enumerate(["first_name", "last_name", "email", "description"]):
        WebFormField.objects.create(
            form=web_form,
            org=org_a,
            order=order,
            source=WebFormField.SOURCE_LEAD,
            lead_field=name,
            label=name.replace("_", " ").title(),
        )
    return web_form


@pytest.mark.django_db
class TestFirstSubmission:
    def test_creates_a_lead_with_the_submitted_values(self, form, org_a):
        submission = submit_form(
            form,
            {"first_name": "Pat", "last_name": "Prospect", "email": "pat@example.com"},
        )
        assert submission.status == WebFormSubmission.ACCEPTED
        lead = submission.lead
        assert lead.first_name == "Pat"
        assert lead.email == "pat@example.com"
        assert lead.org_id == org_a.id

    def test_derives_org_source_and_status_rather_than_reading_them(self, form):
        submission = submit_form(form, {"email": "pat@example.com"})
        assert submission.lead.source == "other"
        assert submission.lead.status == "assigned"
        assert submission.lead.is_active is True

    def test_assigns_to_the_forms_assignee(self, form, admin_profile):
        submission = submit_form(form, {"email": "pat@example.com"})
        assert list(submission.lead.assigned_to.all()) == [admin_profile]

    def test_applies_the_forms_tags(self, form, org_a):
        tag = Tags.objects.create(name="inbound", slug="inbound", org=org_a)
        form.tags.add(tag)
        submission = submit_form(form, {"email": "pat@example.com"})
        assert list(submission.lead.tags.all()) == [tag]

    def test_stores_the_ip_and_referer(self, form):
        submission = submit_form(
            form,
            {"email": "pat@example.com"},
            ip="203.0.113.50",
            referer="https://example.com/contact",
        )
        assert submission.submitted_ip == "203.0.113.50"
        assert submission.referer == "https://example.com/contact"

    def test_a_custom_field_value_lands_in_lead_custom_fields(self, form, org_a):
        CustomFieldDefinition.objects.create(
            org=org_a,
            target_model="Lead",
            key="budget",
            label="Budget",
            field_type="number",
        )
        submission = submit_form(
            form, {"email": "pat@example.com"}, custom_fields={"budget": 5000}
        )
        assert submission.lead.custom_fields == {"budget": 5000}

    def test_the_payload_records_what_was_submitted(self, form):
        submission = submit_form(
            form, {"email": "pat@example.com", "first_name": "Pat"}
        )
        assert submission.payload["email"] == "pat@example.com"
        assert submission.payload["first_name"] == "Pat"


@pytest.mark.django_db
class TestDuplicateEmail:
    def test_does_not_create_a_second_lead(self, form, org_a):
        submit_form(form, {"email": "pat@example.com", "first_name": "Pat"})
        submit_form(form, {"email": "pat@example.com", "first_name": "Pat"})
        assert Lead.objects.filter(org=org_a, email="pat@example.com").count() == 1

    def test_matches_case_insensitively(self, form, org_a):
        submit_form(form, {"email": "pat@example.com"})
        submit_form(form, {"email": "PAT@EXAMPLE.COM"})
        assert Lead.objects.filter(org=org_a).count() == 1

    def test_is_recorded_as_a_duplicate(self, form):
        submit_form(form, {"email": "pat@example.com"})
        second = submit_form(form, {"email": "pat@example.com"})
        assert second.status == WebFormSubmission.ACCEPTED_DUPLICATE

    def test_fills_a_field_that_was_empty(self, form):
        submit_form(form, {"email": "pat@example.com"})
        second = submit_form(form, {"email": "pat@example.com", "first_name": "Pat"})
        assert second.lead.first_name == "Pat"

    def test_leaves_a_populated_field_untouched(self, form):
        submit_form(form, {"email": "pat@example.com", "first_name": "Pat"})
        second = submit_form(
            form, {"email": "pat@example.com", "first_name": "Impostor"}
        )
        assert second.lead.first_name == "Pat"

    def test_fills_a_blank_custom_field_but_not_a_populated_one(self, form, org_a):
        CustomFieldDefinition.objects.create(
            org=org_a,
            target_model="Lead",
            key="budget",
            label="Budget",
            field_type="number",
        )
        submit_form(form, {"email": "pat@example.com"}, custom_fields={"budget": 5000})
        second = submit_form(
            form, {"email": "pat@example.com"}, custom_fields={"budget": 1}
        )
        assert second.lead.custom_fields["budget"] == 5000

    def test_appends_the_new_message_as_a_comment(self, form):
        submit_form(form, {"email": "pat@example.com", "description": "First ask"})
        second = submit_form(
            form, {"email": "pat@example.com", "description": "Second ask"}
        )
        comments = lead_comments(second.lead)
        assert comments.count() == 1
        assert "Second ask" in comments.first().comment

    def test_a_duplicate_with_no_message_writes_no_empty_comment(self, form):
        submit_form(form, {"email": "pat@example.com"})
        second = submit_form(form, {"email": "pat@example.com", "first_name": "Pat"})
        assert lead_comments(second.lead).count() == 0

    def test_both_submissions_point_at_the_same_lead(self, form):
        first = submit_form(form, {"email": "pat@example.com"})
        second = submit_form(form, {"email": "pat@example.com"})
        assert first.lead_id == second.lead_id

    def test_a_lead_in_another_org_with_the_same_address_is_not_matched(
        self, form, org_b, admin_profile
    ):
        """Uniqueness is per org, so org B's Pat is a different person. Merging
        across the tenant boundary would leak one org's data into another."""
        Lead.objects.create(
            org=org_b,
            email="pat@example.com",
            first_name="Other Pat",
            status="assigned",
        )
        submission = submit_form(
            form, {"email": "pat@example.com", "first_name": "Pat"}
        )
        assert submission.status == WebFormSubmission.ACCEPTED
        assert submission.lead.org_id == form.org_id
        assert submission.lead.first_name == "Pat"


@pytest.mark.django_db
class TestRejection:
    def test_a_rejected_submission_is_stored_with_no_lead(self, form, org_a):
        submission = submit_form(
            form, {}, rejected=WebFormSubmission.REJECTED_SPAM, reason="honeypot"
        )
        assert submission.lead is None
        assert submission.status == WebFormSubmission.REJECTED_SPAM
        assert submission.reject_reason == "honeypot"
        assert Lead.objects.filter(org=org_a).count() == 0

    def test_an_over_long_reason_is_truncated_rather_than_raising(self, form):
        submission = submit_form(
            form, {}, rejected=WebFormSubmission.REJECTED_INVALID, reason="x" * 400
        )
        assert len(submission.reject_reason) == 255
