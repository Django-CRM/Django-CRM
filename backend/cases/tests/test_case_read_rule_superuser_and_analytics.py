"""`visible_cases_qs` admits superusers, and the analytics read through it.

Two follow-ups from the 1.10.0 pass, pinned both ways:

* `visible_cases_qs` ignored ``is_superuser`` while its siblings admitted it.
  It goes through `is_org_admin`, which now admits a superuser's profile, so a
  superuser on a USER profile sees every ticket in the org and a plain member
  still sees only their own.
* `cases/analytics_views.py` carried an inline copy of the read rule (D40). It
  now uses `visible_cases_qs`, so a member's figures count only tickets they
  can open, and an admin's or a superuser's count them all.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from cases.access import visible_cases_qs
from cases.models import Case, CaseWatcher
from conftest import rls_org


@pytest.fixture
def superuser(regular_user):
    """The `user_client` caller, still on a USER profile, made superuser."""
    regular_user.is_superuser = True
    regular_user.save(update_fields=["is_superuser"])
    return regular_user


@pytest.fixture
def tickets(admin_user, regular_user, user_profile, org_a):
    """Four tickets answered yesterday: the member created one, is assigned
    one, watches one, and has nothing to do with the fourth."""
    yesterday = timezone.now() - timedelta(days=1)

    def make(name, creator):
        case = Case.objects.create(
            name=name,
            status="New",
            priority="Normal",
            org=org_a,
            created_by=creator,
            sla_first_response_hours=4,
        )
        Case.objects.filter(pk=case.pk).update(
            created_at=yesterday, first_response_at=yesterday + timedelta(hours=1)
        )
        return case

    created = make("Created", regular_user)
    assigned = make("Assigned", admin_user)
    assigned.assigned_to.add(user_profile)
    watched = make("Watched", admin_user)
    CaseWatcher.objects.create(case=watched, profile=user_profile, org=org_a)
    hidden = make("Hidden", admin_user)
    return {
        "created": created,
        "assigned": assigned,
        "watched": watched,
        "hidden": hidden,
    }


def _names(qs):
    return set(qs.values_list("name", flat=True))


ALL = {"Created", "Assigned", "Watched", "Hidden"}
MEMBERS = {"Created", "Assigned", "Watched"}


class TestVisibleCasesQs:
    def test_a_plain_member_sees_only_their_own(self, user_profile, tickets):
        assert _names(visible_cases_qs(user_profile)) == MEMBERS

    def test_a_superuser_on_a_member_profile_sees_every_ticket(
        self, superuser, user_profile, tickets
    ):
        user_profile.refresh_from_db()
        assert _names(visible_cases_qs(user_profile)) == ALL

    def test_the_detail_view_agrees(self, user_client, tickets, request):
        hidden = tickets["hidden"]
        assert user_client.get(f"/api/cases/{hidden.id}/").status_code == 404
        request.getfixturevalue("superuser")
        assert user_client.get(f"/api/cases/{hidden.id}/").status_code == 200

    def test_another_orgs_ticket_stays_out_for_a_superuser(
        self, superuser, user_profile, user_b, org_b, tickets
    ):
        with rls_org(org_b):
            Case.objects.create(
                name="Foreign", status="New", priority="Normal", org=org_b
            )
        assert "Foreign" not in _names(visible_cases_qs(user_profile))


FRT = "/api/cases/analytics/frt/"
DRILL = "/api/cases/analytics/drilldown/"


class TestAnalyticsFollowTheReadRule:
    def test_a_members_figures_leave_out_what_they_cannot_open(
        self, user_client, tickets
    ):
        assert user_client.get(FRT).data["count"] == 3
        ids = {
            row["id"]
            for row in user_client.get(DRILL, {"metric": "frt"}).data["results"]
        }
        assert ids == {str(tickets[k].id) for k in ("created", "assigned", "watched")}

    def test_an_admin_counts_everything(self, admin_client, tickets):
        assert admin_client.get(FRT).data["count"] == 4

    def test_a_superuser_counts_everything(self, superuser, user_client, tickets):
        assert user_client.get(FRT).data["count"] == 4
        drilled = user_client.get(DRILL, {"metric": "frt"}).data["results"]
        assert {row["name"] for row in drilled} == ALL

    def test_the_service_overview_admits_a_superuser_and_refuses_a_member(
        self, user_client, tickets, request
    ):
        assert user_client.get("/api/cases/analytics/service/").status_code == 403
        request.getfixturevalue("superuser")
        assert user_client.get("/api/cases/analytics/service/").status_code == 200
