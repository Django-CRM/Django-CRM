"""The two lead alert switches on a member's own profile, and the org's
waiting reminder, which only an admin sets."""

from datetime import timedelta

import pytest
from django.utils import timezone

from common.models import Org, Profile, User

PROFILE = "/api/profile/"
AUTH_PROFILE = "/api/auth/profile/"
ORG_SETTINGS = "/api/org/settings/"


class TestAlertSwitches:
    def test_both_switches_default_on_and_both_reads_show_them(self, user_client):
        own = user_client.get(PROFILE).json()["user_obj"]
        assert (own["lead_alerts_email"], own["lead_alerts_push"]) == (True, True)
        detail = user_client.get(AUTH_PROFILE).json()
        assert (detail["lead_alerts_email"], detail["lead_alerts_push"]) == (
            True,
            True,
        )

    def test_both_reads_return_the_callers_own_values(
        self, user_client, user_profile, admin_profile
    ):
        Profile.objects.filter(pk=user_profile.pk).update(lead_alerts_push=False)
        Profile.objects.filter(pk=admin_profile.pk).update(lead_alerts_email=False)

        own = user_client.get(PROFILE).json()["user_obj"]
        detail = user_client.get(AUTH_PROFILE).json()
        for body in (own, detail):
            assert (body["lead_alerts_email"], body["lead_alerts_push"]) == (
                True,
                False,
            )

    def test_patch_sets_the_callers_switches_only(
        self, user_client, user_profile, admin_profile
    ):
        response = user_client.patch(
            PROFILE,
            {"lead_alerts_email": False, "lead_alerts_push": False},
            format="json",
        )
        assert response.status_code == 200, response.content
        body = response.json()["user_obj"]
        assert (body["lead_alerts_email"], body["lead_alerts_push"]) == (False, False)
        user_profile.refresh_from_db()
        admin_profile.refresh_from_db()
        assert (user_profile.lead_alerts_email, user_profile.lead_alerts_push) == (
            False,
            False,
        )
        assert (admin_profile.lead_alerts_email, admin_profile.lead_alerts_push) == (
            True,
            True,
        )

        response = user_client.patch(PROFILE, {"lead_alerts_push": True}, format="json")
        assert response.status_code == 200
        user_profile.refresh_from_db()
        assert (user_profile.lead_alerts_email, user_profile.lead_alerts_push) == (
            False,
            True,
        )

    def test_a_switch_that_is_not_a_boolean_is_refused(self, user_client, user_profile):
        response = user_client.patch(
            PROFILE, {"lead_alerts_email": "sometimes"}, format="json"
        )
        assert response.status_code == 400
        user_profile.refresh_from_db()
        assert user_profile.lead_alerts_email is True

    def test_an_id_in_the_body_does_not_reach_another_profile(
        self, user_client, admin_profile
    ):
        response = user_client.patch(
            PROFILE,
            {"id": str(admin_profile.id), "lead_alerts_push": False},
            format="json",
        )
        assert response.status_code == 200
        admin_profile.refresh_from_db()
        assert admin_profile.lead_alerts_push is True

    def test_unauthenticated_is_refused(self, unauthenticated_client):
        response = unauthenticated_client.patch(
            PROFILE, {"lead_alerts_push": False}, format="json"
        )
        assert response.status_code == 403


class TestReminderSetting:
    def test_off_by_default(self, admin_client):
        assert admin_client.get(ORG_SETTINGS).json()["lead_reminder_minutes"] is None

    @pytest.mark.parametrize("minutes", [5, 15, 30, 60])
    def test_an_admin_chooses_a_listed_value(self, admin_client, org_a, minutes):
        response = admin_client.patch(
            ORG_SETTINGS, {"lead_reminder_minutes": minutes}, format="json"
        )
        assert response.status_code == 200, response.content
        assert response.json()["lead_reminder_minutes"] == minutes
        org_a.refresh_from_db()
        assert org_a.lead_reminder_minutes == minutes

    def test_an_admin_turns_it_off(self, admin_client, org_a):
        org_a.lead_reminder_minutes = 15
        org_a.save(update_fields=["lead_reminder_minutes"])
        response = admin_client.patch(
            ORG_SETTINGS, {"lead_reminder_minutes": None}, format="json"
        )
        assert response.status_code == 200
        org_a.refresh_from_db()
        assert org_a.lead_reminder_minutes is None

    @pytest.mark.parametrize("minutes", [0, 1, 7, 120, -5, "soon"])
    def test_any_other_value_is_refused(self, admin_client, org_a, minutes):
        response = admin_client.patch(
            ORG_SETTINGS, {"lead_reminder_minutes": minutes}, format="json"
        )
        assert response.status_code == 400
        org_a.refresh_from_db()
        assert org_a.lead_reminder_minutes is None

    def test_a_member_cannot_set_it(self, user_client, admin_client, org_a):
        response = user_client.patch(
            ORG_SETTINGS, {"lead_reminder_minutes": 5}, format="json"
        )
        assert response.status_code == 403
        org_a.refresh_from_db()
        assert org_a.lead_reminder_minutes is None
        assert user_client.get(ORG_SETTINGS).json()["lead_reminder_minutes"] is None

    def test_switching_it_on_stamps_when(self, admin_client, org_a):
        org_a.lead_reminder_since = timezone.now() - timedelta(days=9)
        org_a.save(update_fields=["lead_reminder_since"])
        before = timezone.now()

        response = admin_client.patch(
            ORG_SETTINGS, {"lead_reminder_minutes": 15}, format="json"
        )

        assert response.status_code == 200, response.content
        org_a.refresh_from_db()
        assert org_a.lead_reminder_since >= before
        assert "lead_reminder_since" not in response.json()

    def test_only_switching_it_on_moves_the_stamp(self, admin_client, org_a):
        """A change of minutes keeps it: the leads already waiting since it
        was switched on are still due under the new value."""
        since = timezone.now() - timedelta(hours=2)
        org_a.lead_reminder_minutes = 15
        org_a.lead_reminder_since = since
        org_a.save(update_fields=["lead_reminder_minutes", "lead_reminder_since"])

        for body in ({"lead_reminder_minutes": 30}, {"name": "Renamed"}, {}):
            admin_client.patch(ORG_SETTINGS, body, format="json")
            org_a.refresh_from_db()
            assert org_a.lead_reminder_since == since
        admin_client.patch(ORG_SETTINGS, {"lead_reminder_minutes": None}, format="json")
        org_a.refresh_from_db()
        assert org_a.lead_reminder_since == since

        before = timezone.now()
        admin_client.patch(ORG_SETTINGS, {"lead_reminder_minutes": 5}, format="json")
        org_a.refresh_from_db()
        assert org_a.lead_reminder_since >= before

    def test_the_stamp_is_not_writable(self, admin_client, org_a):
        before = timezone.now()
        admin_client.patch(
            ORG_SETTINGS,
            {"lead_reminder_since": "2000-01-01T00:00:00Z"},
            format="json",
        )
        org_a.refresh_from_db()
        assert org_a.lead_reminder_since.year != 2000

        admin_client.patch(
            ORG_SETTINGS,
            {
                "lead_reminder_minutes": 15,
                "lead_reminder_since": "2000-01-01T00:00:00Z",
            },
            format="json",
        )
        org_a.refresh_from_db()
        assert org_a.lead_reminder_since >= before


class TestDuringTheUpgrade:
    """Between `migrate` and the restart, the previous release still inserts
    profiles and orgs, naming only the columns it knows about."""

    @staticmethod
    def _insert_without(obj, *names):
        model = type(obj)
        fields = [f for f in model._meta.local_concrete_fields if f.name not in names]
        model._base_manager._insert([obj], fields=fields, using="default")
        return model.objects.get(pk=obj.pk)

    def test_a_new_profile_gets_both_switches_on(self, org_a):
        user = User.objects.create_user(email="mid@example.com", password="x")
        profile = Profile(user=user, org=org_a, role="USER")
        stored = self._insert_without(profile, "lead_alerts_email", "lead_alerts_push")
        assert (stored.lead_alerts_email, stored.lead_alerts_push) == (True, True)

    def test_a_new_org_gets_a_reminder_stamp(self):
        before = timezone.now() - timedelta(seconds=1)
        stored = self._insert_without(
            Org(name="Signed up mid-deploy"),
            "lead_reminder_minutes",
            "lead_reminder_since",
        )
        assert stored.lead_reminder_minutes is None
        assert stored.lead_reminder_since >= before
