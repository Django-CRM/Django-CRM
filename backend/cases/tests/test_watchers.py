"""Tests for the CaseWatcher model and the watch/unwatch/mentions flows.

The model + API tests live here. End-to-end mention dispatch is exercised in
test_watchers_mentions.py once that lands.
"""

import pytest
from django.db import IntegrityError

from cases.models import Case, CaseWatcher


@pytest.mark.django_db
class TestCaseWatcherModel:
    def test_create_minimal(self, case_a, admin_profile):
        w = CaseWatcher.objects.create(
            case=case_a, profile=admin_profile, org=case_a.org
        )
        assert w.id is not None
        assert w.subscribed_via == "manual"
        assert w in case_a.watcher_rows.all()
        assert admin_profile in case_a.watchers.all()

    def test_unique_per_case_profile(self, case_a, admin_profile):
        CaseWatcher.objects.create(case=case_a, profile=admin_profile, org=case_a.org)
        with pytest.raises(IntegrityError):
            CaseWatcher.objects.create(
                case=case_a, profile=admin_profile, org=case_a.org
            )

    def test_str_includes_subscribed_via(self, case_a, admin_profile):
        w = CaseWatcher.objects.create(
            case=case_a,
            profile=admin_profile,
            org=case_a.org,
            subscribed_via="mention",
        )
        assert "mention" in str(w)

    def test_case_cascade_deletes_watcher(self, case_a, admin_profile):
        CaseWatcher.objects.create(case=case_a, profile=admin_profile, org=case_a.org)
        case_pk = case_a.pk
        case_a.delete()
        assert not CaseWatcher.objects.filter(case_id=case_pk).exists()

    def test_profile_cascade_deletes_watcher(self, case_a, admin_profile):
        CaseWatcher.objects.create(case=case_a, profile=admin_profile, org=case_a.org)
        profile_pk = admin_profile.pk
        admin_profile.delete()
        assert not CaseWatcher.objects.filter(profile_id=profile_pk).exists()

    def test_watchers_m2m_query(self, case_a, admin_profile, user_profile):
        CaseWatcher.objects.create(case=case_a, profile=admin_profile, org=case_a.org)
        # user_profile is NOT watching
        watching_cases = Case.objects.filter(watchers=admin_profile)
        not_watching = Case.objects.filter(watchers=user_profile)
        assert case_a in watching_cases
        assert case_a not in not_watching


@pytest.mark.django_db
class TestWatchAPI:
    def test_post_creates_watch(self, admin_client, case_a, admin_profile):
        r = admin_client.post(f"/api/cases/{case_a.id}/watch/")
        assert r.status_code == 201
        body = r.json()
        assert body["watching"] is True
        assert body["subscribed_via"] == "manual"
        assert CaseWatcher.objects.filter(case=case_a, profile=admin_profile).exists()

    def test_post_idempotent(self, admin_client, case_a, admin_profile):
        admin_client.post(f"/api/cases/{case_a.id}/watch/")
        r = admin_client.post(f"/api/cases/{case_a.id}/watch/")
        assert r.status_code == 200  # already watching → 200, not 201
        assert (
            CaseWatcher.objects.filter(case=case_a, profile=admin_profile).count() == 1
        )

    def test_delete_removes_watch(self, admin_client, case_a, admin_profile):
        CaseWatcher.objects.create(case=case_a, profile=admin_profile, org=case_a.org)
        r = admin_client.delete(f"/api/cases/{case_a.id}/watch/")
        assert r.status_code == 204
        assert not CaseWatcher.objects.filter(
            case=case_a, profile=admin_profile
        ).exists()

    def test_delete_when_not_watching_returns_204(self, admin_client, case_a):
        r = admin_client.delete(f"/api/cases/{case_a.id}/watch/")
        assert r.status_code == 204

    def test_cross_org_case_returns_404(self, admin_client, case_b):
        # admin_client is in org_a; case_b is in org_b, should never be visible
        r = admin_client.post(f"/api/cases/{case_b.id}/watch/")
        assert r.status_code == 404

    def test_unauthenticated_rejected(self, unauthenticated_client, case_a):
        r = unauthenticated_client.post(f"/api/cases/{case_a.id}/watch/")
        assert r.status_code in (401, 403)

    def test_non_admin_who_cannot_see_case_blocked(self, user_client, case_a):
        # user_profile is not admin, not creator, not assigned, not watching.
        # Should not be able to subscribe to a case they cannot see.
        r = user_client.post(f"/api/cases/{case_a.id}/watch/")
        assert r.status_code == 404


@pytest.mark.django_db
class TestWatchersListAPI:
    def test_lists_all_watchers(
        self, admin_client, case_a, admin_profile, user_profile
    ):
        CaseWatcher.objects.create(case=case_a, profile=admin_profile, org=case_a.org)
        CaseWatcher.objects.create(
            case=case_a,
            profile=user_profile,
            org=case_a.org,
            subscribed_via="mention",
        )
        r = admin_client.get(f"/api/cases/{case_a.id}/watchers/")
        assert r.status_code == 200
        body = r.json()
        assert body["count"] == 2
        emails = [w["email"] for w in body["watchers"]]
        assert "admin@test.com" in emails
        assert "user@test.com" in emails

    def test_empty_watchers(self, admin_client, case_a):
        r = admin_client.get(f"/api/cases/{case_a.id}/watchers/")
        assert r.status_code == 200
        assert r.json() == {
            "watchers": [],
            "count": 0,
            "is_current_user_watching": False,
        }

    def test_says_when_the_caller_is_watching(
        self, admin_client, case_a, admin_profile
    ):
        CaseWatcher.objects.create(case=case_a, profile=admin_profile, org=case_a.org)
        r = admin_client.get(f"/api/cases/{case_a.id}/watchers/")
        assert r.json()["is_current_user_watching"] is True

    def test_someone_else_watching_is_not_the_caller(
        self, admin_client, case_a, user_profile
    ):
        CaseWatcher.objects.create(case=case_a, profile=user_profile, org=case_a.org)
        body = admin_client.get(f"/api/cases/{case_a.id}/watchers/").json()
        assert body["count"] == 1
        assert body["is_current_user_watching"] is False

    def test_it_follows_watch_and_unwatch(self, admin_client, case_a):
        url = f"/api/cases/{case_a.id}/watchers/"
        admin_client.post(f"/api/cases/{case_a.id}/watch/")
        assert admin_client.get(url).json()["is_current_user_watching"] is True
        admin_client.delete(f"/api/cases/{case_a.id}/watch/")
        assert admin_client.get(url).json()["is_current_user_watching"] is False

    def test_a_member_who_cannot_open_the_case_gets_404(self, user_client, case_a):
        assert user_client.get(f"/api/cases/{case_a.id}/watchers/").status_code == 404


@pytest.mark.django_db
class TestWatchingListAPI:
    def test_returns_only_my_watched_cases(
        self,
        admin_client,
        case_a,
        case_b_same_org,
        admin_profile,
        user_profile,
    ):
        CaseWatcher.objects.create(case=case_a, profile=admin_profile, org=case_a.org)
        # Another user watches a different case, must not leak.
        CaseWatcher.objects.create(
            case=case_b_same_org,
            profile=user_profile,
            org=case_b_same_org.org,
        )
        r = admin_client.get("/api/cases/watching/")
        assert r.status_code == 200
        body = r.json()
        assert body["count"] == 1
        ids = [c["id"] for c in body["cases"]]
        assert str(case_a.id) in ids
        assert str(case_b_same_org.id) not in ids

    def test_empty_returns_zero(self, admin_client):
        r = admin_client.get("/api/cases/watching/")
        assert r.status_code == 200
        assert r.json()["count"] == 0


@pytest.mark.django_db
class TestVisibilityAllowance:
    def test_watcher_who_lost_access_can_still_see_case(
        self, user_client, case_a, user_profile
    ):
        # user_profile is not admin, not creator, not assigned. Normally
        # invisible. Subscribing them as a watcher should grant read access.
        CaseWatcher.objects.create(case=case_a, profile=user_profile, org=case_a.org)
        r = user_client.delete(f"/api/cases/{case_a.id}/watch/")
        # Watcher allowance lets them at least UNWATCH.
        assert r.status_code == 204

    def test_watcher_sees_case_in_main_list(self, user_client, case_a, user_profile):
        # Non-admin who isn't creator or assigned is invisible by default.
        # Watching the case must surface it in the main list.
        before = user_client.get("/api/cases/")
        assert before.status_code == 200
        before_ids = [c["id"] for c in before.json().get("cases", [])]
        assert str(case_a.id) not in before_ids

        CaseWatcher.objects.create(case=case_a, profile=user_profile, org=case_a.org)
        after = user_client.get("/api/cases/")
        assert after.status_code == 200
        after_ids = [c["id"] for c in after.json().get("cases", [])]
        assert str(case_a.id) in after_ids
