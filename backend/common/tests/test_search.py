"""Tests for the org-scoped global search endpoint (GET /api/search/).

The two that matter most are tenant isolation (org A never sees org B) and
intra-org visibility (a non-admin never finds a record they could not open from
the list). Search is a classic place for both leaks.
"""

import pytest
from crum import impersonate

from accounts.models import Account
from cases.models import Case, Solution
from contacts.models import Contact
from invoices.models import Invoice
from leads.models import Lead
from opportunity.models import Opportunity

URL = "/api/search/"


def _seed_all(org, token="Zephyr"):
    """One record of every searchable type in `org`, all matching `token`."""
    Lead.objects.create(
        first_name="Zed",
        last_name="Example",
        email="zed@example.com",
        status="in process",
        company_name=f"{token} Ltd",
        org=org,
    )
    Account.objects.create(name=f"{token} Corp", org=org)
    Contact.objects.create(
        first_name=token, last_name="Person", email="c@example.com", org=org
    )
    Opportunity.objects.create(name=f"{token} deal", stage="PROSPECTING", org=org)
    Case.objects.create(
        name=f"{token} ticket", status="New", priority="Normal", org=org
    )
    billing = Account.objects.create(name="Billing Co", org=org)
    Invoice.objects.create(
        invoice_title=f"{token} invoice",
        client_name=f"{token} Client",
        account=billing,
        currency="USD",
        status="Draft",
        org=org,
    )
    Solution.objects.create(
        org=org,
        title=f"{token} guide",
        description="How to.",
        status="approved",
        is_published=True,
    )


@pytest.mark.django_db
class TestGlobalSearch:
    def test_short_query_returns_empty_even_with_matches(self, admin_client, org_a):
        _seed_all(org_a, token="Zephyr")
        # One character is below the floor, so it matches nothing on purpose...
        assert admin_client.get(f"{URL}?q=Z").json()["results"] == []
        assert admin_client.get(f"{URL}?q=").json()["results"] == []
        # ...two characters searches for real.
        assert len(admin_client.get(f"{URL}?q=Ze").json()["results"]) > 0

    def test_unauthenticated_rejected(self, unauthenticated_client):
        resp = unauthenticated_client.get(f"{URL}?q=zephyr")
        assert resp.status_code in (401, 403)

    def test_finds_every_type(self, admin_client, org_a):
        _seed_all(org_a)
        body = admin_client.get(f"{URL}?q=Zephyr").json()
        types = {r["type"] for r in body["results"]}
        assert types == {
            "lead",
            "deal",
            "account",
            "contact",
            "ticket",
            "invoice",
            "solution",
        }
        for row in body["results"]:
            assert row["id"] and row["title"]

    def test_deal_subtitle_is_the_stage_label(self, admin_client, org_a):
        """With no account, a deal's subtitle is its stage, as the org names it."""
        from opportunity.models import DealStage

        Opportunity.objects.create(name="Zephyr deal", stage="PROSPECTING", org=org_a)
        DealStage.objects.filter(org=org_a, code="PROSPECTING").update(
            label="Discovery call"
        )
        rows = admin_client.get(f"{URL}?q=Zephyr").json()["results"]
        assert [r["subtitle"] for r in rows if r["type"] == "deal"] == [
            "Discovery call"
        ]

    def test_cross_tenant_isolation(self, admin_client, org_b_client, org_a, org_b):
        """org A's admin must not find org B's record, and vice-versa."""
        Account.objects.create(name="SecretB Holdings", org=org_b)
        seen_by_a = admin_client.get(f"{URL}?q=SecretB").json()["results"]
        assert seen_by_a == []
        seen_by_b = org_b_client.get(f"{URL}?q=SecretB").json()["results"]
        assert any(
            r["type"] == "account" and "SecretB" in r["title"] for r in seen_by_b
        )

    def test_non_admin_sees_only_created_or_assigned(
        self, user_client, admin_user, regular_user, user_profile, org_a
    ):
        with impersonate(admin_user):
            Account.objects.create(name="Alpha AdminOnly", org=org_a)
        with impersonate(regular_user):
            Account.objects.create(name="Alpha Mine", org=org_a)
        assigned = Account.objects.create(name="Alpha Assigned", org=org_a)
        assigned.assigned_to.add(user_profile)

        titles = {
            r["title"] for r in user_client.get(f"{URL}?q=Alpha").json()["results"]
        }
        assert "Alpha Mine" in titles  # created by the user
        assert "Alpha Assigned" in titles  # assigned to the user
        assert "Alpha AdminOnly" not in titles  # neither → hidden

    def test_admin_sees_all_in_org(
        self, admin_client, admin_user, regular_user, user_profile, org_a
    ):
        with impersonate(admin_user):
            Account.objects.create(name="Beta AdminOwned", org=org_a)
        with impersonate(regular_user):
            Account.objects.create(name="Beta UserOwned", org=org_a)
        titles = {
            r["title"] for r in admin_client.get(f"{URL}?q=Beta").json()["results"]
        }
        assert {"Beta AdminOwned", "Beta UserOwned"} <= titles

    def test_solutions_visible_to_every_member(
        self, user_client, admin_user, user_profile, org_a
    ):
        """Knowledge base is org-wide, a non-admin who owns nothing still finds it."""
        with impersonate(admin_user):
            Solution.objects.create(
                org=org_a,
                title="SharedKB article",
                description="x",
                status="approved",
                is_published=True,
            )
        results = user_client.get(f"{URL}?q=SharedKB").json()["results"]
        assert any(r["type"] == "solution" for r in results)
