"""A lead edit over PATCH changes what it sends and nothing else.

The phone edited leads with PUT, and `LeadDetailView.put` clears `contacts`,
`teams`, `tags` and `assigned_to` unconditionally. The phone's form sends
neither contacts nor teams, and its detail sheets sent a single key, so an
ordinary edit unlinked every contact and team on the lead. It now edits with
PATCH, so PATCH has to do what PUT did for every key it is sent. The one place
it did not was conversion: PATCH converted straight away and dropped the rest
of the body, while PUT saved the fields first.
"""

from unittest.mock import patch

import pytest

from common.models import Tags, Teams
from contacts.models import Contact
from leads.models import Lead

pytestmark = pytest.mark.django_db


def _url(lead):
    return f"/api/leads/{lead.id}/"


@pytest.fixture
def lead(org_a):
    lead = Lead.objects.create(
        first_name="Ada",
        last_name="Lovelace",
        email="ada@example.com",
        status="in process",
        company_name="Analytical Engines",
        job_title="Analyst",
        org=org_a,
    )
    lead.contacts.add(
        Contact.objects.create(first_name="Linked", last_name="Person", org=org_a)
    )
    lead.teams.add(Teams.objects.create(name="Field", description="-", org=org_a))
    return lead


class TestPatchLeavesAbsentRelationsAlone:
    def test_the_form_body_keeps_contacts_and_teams(
        self, admin_client, lead, org_a, admin_profile
    ):
        tag = Tags.objects.create(name="Hot", org=org_a)
        response = admin_client.patch(
            _url(lead),
            {
                "first_name": "Ada",
                "last_name": "King",
                "email": "ada@example.com",
                "company_name": "Analytical Engines",
                "status": "in process",
                "assigned_to": [str(admin_profile.id)],
                "tags": [str(tag.id)],
            },
            format="json",
        )
        assert response.status_code == 200, response.content
        lead.refresh_from_db()
        assert lead.last_name == "King"
        assert list(lead.assigned_to.all()) == [admin_profile]
        assert list(lead.tags.all()) == [tag]
        assert lead.contacts.count() == 1
        assert lead.teams.count() == 1

    def test_a_single_key_changes_only_that_key(self, admin_client, lead, org_a):
        """What the phone's tag sheet sends."""
        tag = Tags.objects.create(name="Hot", org=org_a)
        response = admin_client.patch(
            _url(lead), {"tags": [str(tag.id)]}, format="json"
        )
        assert response.status_code == 200, response.content
        lead.refresh_from_db()
        assert lead.first_name == "Ada"
        assert list(lead.tags.all()) == [tag]
        assert lead.contacts.count() == 1
        assert lead.teams.count() == 1


class TestConversionSavesTheBodyFirst:
    @patch("leads.views.lead_views.send_email_to_assigned_user.delay")
    def test_an_email_sent_with_the_conversion_is_used(
        self, _mail, admin_client, org_a
    ):
        """The case the web form exists for: add the email, choose converted."""
        lead = Lead.objects.create(
            first_name="Grace", last_name="Hopper", email="", org=org_a
        )
        response = admin_client.patch(
            _url(lead),
            {"email": "grace@example.com", "status": "converted"},
            format="json",
        )
        assert response.status_code == 200, response.content
        assert response.json()["contact_id"]
        lead.refresh_from_db()
        assert lead.status == "converted"
        assert lead.email == "grace@example.com"
        contact = Contact.objects.get(id=response.json()["contact_id"])
        assert contact.email == "grace@example.com"

    @patch("leads.views.lead_views.send_email_to_assigned_user.delay")
    def test_other_fields_sent_with_the_conversion_are_saved(
        self, _mail, admin_client, lead
    ):
        response = admin_client.patch(
            _url(lead),
            {"job_title": "Chief Analyst", "status": "converted"},
            format="json",
        )
        assert response.status_code == 200, response.content
        assert response.json()["account_id"]
        lead.refresh_from_db()
        assert lead.status == "converted"
        assert lead.job_title == "Chief Analyst"

    def test_an_invalid_field_stops_the_conversion(self, admin_client, lead):
        response = admin_client.patch(
            _url(lead),
            {"probability": 250, "status": "converted"},
            format="json",
        )
        assert response.status_code == 400
        lead.refresh_from_db()
        assert lead.status == "in process"

    def test_blanking_the_email_while_converting_is_refused(self, admin_client, lead):
        response = admin_client.patch(
            _url(lead),
            {"email": "", "status": "converted"},
            format="json",
        )
        assert response.status_code == 400
        assert "email" in response.json()["errors"]
        lead.refresh_from_db()
        assert lead.status == "in process"
        assert lead.email == "ada@example.com"

    def test_is_converted_on_a_converted_lead_is_refused(self, admin_client, lead):
        Lead.objects.filter(pk=lead.pk).update(status="converted")
        response = admin_client.patch(_url(lead), {"is_converted": True}, format="json")
        assert response.status_code == 400
        assert "status" in response.json()["errors"]

    @patch("leads.views.lead_views.send_email_to_assigned_user.delay")
    def test_a_new_assignee_on_a_conversion_is_emailed_once(
        self, mail, admin_client, lead, admin_profile
    ):
        response = admin_client.patch(
            _url(lead),
            {"assigned_to": [str(admin_profile.id)], "status": "converted"},
            format="json",
        )
        assert response.status_code == 200, response.content
        mail.assert_called_once()
        assert mail.call_args[0][0] == [admin_profile.id]
