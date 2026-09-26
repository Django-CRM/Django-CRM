"""Transitions into and out of `converted`.

`leads/workflow.py` declared `TERMINAL_STATUSES` and was imported by nothing,
so the rule it described was never enforced. Two concrete failures followed,
and both are covered here in both directions.
"""

import pytest

from leads.models import Lead
from opportunity.models import Opportunity

LEADS_URL = "/api/leads/"


def _detail_url(pk):
    return f"/api/leads/{pk}/"


def _lead(org, **kw):
    return Lead.objects.create(
        first_name=kw.pop("first_name", "Ada"),
        last_name=kw.pop("last_name", "Lovelace"),
        email=kw.pop("email", "ada@example.com"),
        status=kw.pop("status", "in process"),
        company_name=kw.pop("company_name", "Analytical Engines"),
        org=org,
        **kw,
    )


@pytest.mark.django_db
class TestConvertedIsIrreversible:
    def test_converting_an_open_lead_is_allowed(self, admin_client, org_a):
        """The permitted direction. This must still work."""
        lead = _lead(org_a, opportunity_amount="1000.00")
        response = admin_client.put(
            _detail_url(lead.id),
            {
                "first_name": "Ada",
                "last_name": "Lovelace",
                "email": "ada@example.com",
                "status": "converted",
            },
            content_type="application/json",
        )
        assert response.status_code == 200
        lead.refresh_from_db()
        assert lead.status == "converted"

    def test_converting_twice_is_rejected(self, admin_client, org_a):
        """`LeadDetailView.put` calls `convert_lead_to_account()` whenever the
        incoming status is "converted" and never checked whether it had
        already run, so a repeat PUT created a second Opportunity against the
        same Account."""
        lead = _lead(org_a, opportunity_amount="1000.00")
        body = {
            "first_name": "Ada",
            "last_name": "Lovelace",
            "email": "ada@example.com",
            "status": "converted",
        }
        first = admin_client.put(
            _detail_url(lead.id), body, content_type="application/json"
        )
        assert first.status_code == 200
        opportunities_after_first = Opportunity.objects.filter(org=org_a).count()

        second = admin_client.put(
            _detail_url(lead.id), body, content_type="application/json"
        )
        assert second.status_code == 400
        assert "status" in second.json()["errors"]
        assert (
            Opportunity.objects.filter(org=org_a).count() == opportunities_after_first
        )

    def test_converting_twice_over_patch_is_rejected(self, admin_client, org_a):
        """The same rule, over the other verb.

        `validate_status` lives on the serializer, and `LeadDetailView.patch`
        has its own conversion branch that returns before the serializer runs.
        So the rule held on PUT and not on PATCH, and two PATCHes produced two
        Opportunities against one Account, which is what the validator was
        written to prevent.
        """
        lead = _lead(org_a, opportunity_amount="1000.00")
        body = {"status": "converted"}

        first = admin_client.patch(
            _detail_url(lead.id), body, content_type="application/json"
        )
        assert first.status_code == 200
        opportunities_after_first = Opportunity.objects.filter(org=org_a).count()

        second = admin_client.patch(
            _detail_url(lead.id), body, content_type="application/json"
        )
        assert second.status_code == 400
        assert "status" in second.json()["errors"]
        assert (
            Opportunity.objects.filter(org=org_a).count() == opportunities_after_first
        )

    def test_moving_out_of_converted_over_patch_is_rejected(self, admin_client, org_a):
        """PATCH's ordinary path does run the serializer, so this direction was
        already covered. Asserted because the guard above sits in front of it."""
        lead = _lead(org_a, status="converted")

        response = admin_client.patch(
            _detail_url(lead.id),
            {"status": "in process"},
            content_type="application/json",
        )

        assert response.status_code == 400
        lead.refresh_from_db()
        assert lead.status == "converted"

    def test_moving_out_of_converted_is_rejected(self, admin_client, org_a):
        """The account, contact and opportunity are not removed when the
        status changes back, so the lead would reopen with duplicates already
        downstream of it."""
        lead = _lead(org_a, status="converted")
        response = admin_client.put(
            _detail_url(lead.id),
            {
                "first_name": "Ada",
                "last_name": "Lovelace",
                "email": "ada@example.com",
                "status": "in process",
            },
            content_type="application/json",
        )
        assert response.status_code == 400
        lead.refresh_from_db()
        assert lead.status == "converted"


@pytest.mark.django_db
class TestConvertOverPatchRequiresEmail:
    """`LeadCreateSerializer.__init__` flips `email.required = True` when the
    incoming status is `converted`, but a partial serializer does not enforce
    `required` on an absent key. `LeadDetailView.patch` once converted without
    any check, so an email-less lead converted over PATCH and produced an
    Account and an Opportunity with nobody attached. It now checks the email
    the save would leave the lead with."""

    def test_patch_convert_without_email_is_rejected(self, admin_client, org_a):
        lead = _lead(org_a, email="")

        response = admin_client.patch(
            _detail_url(lead.id),
            {"status": "converted"},
            content_type="application/json",
        )

        assert response.status_code == 400
        assert "email" in str(response.json()).lower()
        lead.refresh_from_db()
        assert lead.status != "converted"

    def test_patch_convert_with_email_still_works(self, admin_client, org_a):
        """The happy path must be untouched: the guard rejects only the
        email-less case."""
        lead = _lead(org_a, email="buyer@example.com")

        response = admin_client.patch(
            _detail_url(lead.id),
            {"status": "converted"},
            content_type="application/json",
        )

        assert response.status_code == 200
        body = response.json()
        assert body["account_id"]
        assert body["contact_id"]
        lead.refresh_from_db()
        assert lead.status == "converted"


@pytest.mark.django_db
class TestReversibleTransitionsStillWork:
    """The check has to be able to return False, not just True.

    `closed` is deliberately NOT irreversible: reopening a closed lead is
    ordinary work, and nothing downstream was created by closing it.
    """

    def test_closed_lead_can_be_reopened(self, admin_client, org_a):
        lead = _lead(org_a, status="closed")
        response = admin_client.put(
            _detail_url(lead.id),
            {
                "first_name": "Ada",
                "last_name": "Lovelace",
                "email": "ada@example.com",
                "status": "in process",
            },
            content_type="application/json",
        )
        assert response.status_code == 200
        lead.refresh_from_db()
        assert lead.status == "in process"

    def test_ordinary_status_change_is_allowed(self, admin_client, org_a):
        lead = _lead(org_a, status="assigned")
        response = admin_client.put(
            _detail_url(lead.id),
            {
                "first_name": "Ada",
                "last_name": "Lovelace",
                "email": "ada@example.com",
                "status": "in process",
            },
            content_type="application/json",
        )
        assert response.status_code == 200

    def test_creating_a_lead_as_converted_is_still_allowed(self, admin_client, org_a):
        """There is no prior state to contradict, so the new rule must not
        reach it. The create path saves the lead and then converts it once."""
        response = admin_client.post(
            LEADS_URL,
            {
                "first_name": "Grace",
                "last_name": "Hopper",
                "email": "grace@example.com",
                "status": "converted",
            },
            content_type="application/json",
        )
        assert response.status_code in (200, 201)
        assert Lead.objects.get(email="grace@example.com").status == "converted"
        assert Opportunity.objects.filter(org=org_a).count() == 1

    def test_editing_a_converted_lead_without_touching_status(
        self, admin_client, org_a
    ):
        """The rule is about the status field, not about the record. Fixing a
        typo in the name of a converted lead has to keep working."""
        lead = _lead(org_a, status="converted")
        response = admin_client.put(
            _detail_url(lead.id),
            {
                "first_name": "Augusta",
                "last_name": "Lovelace",
                "email": "ada@example.com",
            },
            content_type="application/json",
        )
        assert response.status_code == 200
        lead.refresh_from_db()
        assert lead.first_name == "Augusta"
        assert lead.status == "converted"
