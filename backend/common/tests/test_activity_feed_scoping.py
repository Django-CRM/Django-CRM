"""D43: the recent-activity feed answers with each record's own read rule.

Both feeds (``activities`` on ``GET /api/dashboard/`` and ``GET
/api/activities/``) returned every activity in the org to every member. An
activity row carries the record's name and what was done to it, so a member
read the names of accounts, deals and tickets hidden from them everywhere else.

The rule now: an admin sees the org's feed; anyone else sees an activity only
while its record passes that module's read rule. An entity type with no read
rule, and an activity whose record is gone, fail closed for a member.

``ActivityListView`` and ``ApiTodayView`` also lacked ``HasOrgContext``, so the
without-org-claim case is pinned here too.
"""

import uuid

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import Account
from cases.models import Case, CaseWatcher
from common.models import Activity
from conftest import rls_org

pytestmark = pytest.mark.django_db

FEED = "/api/activities/"
HOME = "/api/dashboard/"


def _stamp(model, obj, user):
    """Set ``created_by`` without the thread-local request user restamping it."""
    model.objects.filter(pk=obj.pk).update(created_by=user)
    obj.refresh_from_db()
    return obj


def _activity(org, entity_type, entity_id, name, actor=None):
    return Activity.objects.create(
        user=actor,
        action="UPDATE",
        entity_type=entity_type,
        entity_id=entity_id,
        entity_name=name,
        org=org,
    )


def _feed_names(client, url=FEED):
    response = client.get(url)
    assert response.status_code == status.HTTP_200_OK, response.content
    return {row["entity_name"] for row in response.data["activities"]}


@pytest.fixture
def my_account(org_a, regular_user):
    return _stamp(
        Account, Account.objects.create(name="Mine Co", org=org_a), regular_user
    )


@pytest.fixture
def hidden_account(org_a, admin_user):
    return _stamp(
        Account, Account.objects.create(name="Hidden Co", org=org_a), admin_user
    )


@pytest.fixture
def feed(org_a, org_b, admin_profile, my_account, hidden_account, profile_b):
    """One activity on a visible record, one on a hidden one, one abroad."""
    _activity(org_a, "Account", my_account.id, "Mine Co", admin_profile)
    _activity(org_a, "Account", hidden_account.id, "Hidden Co", admin_profile)
    with rls_org(org_b):
        foreign = Account.objects.create(name="Foreign Co", org=org_b)
        _activity(org_b, "Account", foreign.id, "Foreign Co", profile_b)


@pytest.mark.parametrize("url", [FEED, HOME])
class TestBothFeedsFollowTheReadRule:
    def test_member_sees_activity_on_a_record_they_can_open(
        self, url, user_client, feed
    ):
        assert "Mine Co" in _feed_names(user_client, url)

    def test_member_does_not_see_activity_on_a_hidden_record(
        self, url, user_client, feed
    ):
        assert "Hidden Co" not in _feed_names(user_client, url)

    def test_admin_sees_both(self, url, admin_client, feed):
        names = _feed_names(admin_client, url)
        assert {"Mine Co", "Hidden Co"} <= names

    def test_another_org_never_appears(self, url, admin_client, user_client, feed):
        assert "Foreign Co" not in _feed_names(admin_client, url)
        assert "Foreign Co" not in _feed_names(user_client, url)


class TestFailClosedForMembers:
    def test_activity_on_a_deleted_record_is_hidden_from_a_member(
        self, org_a, user_profile, user_client, admin_client
    ):
        # Same author and org as a visible row; only the record is missing.
        _activity(org_a, "Account", uuid.uuid4(), "Gone Co", user_profile)
        assert "Gone Co" not in _feed_names(user_client)
        assert "Gone Co" in _feed_names(admin_client)

    def test_entity_type_without_a_read_rule_is_hidden_from_a_member(
        self, org_a, user_profile, user_client, admin_client
    ):
        _activity(org_a, "Team", uuid.uuid4(), "Some Team", user_profile)
        assert "Some Team" not in _feed_names(user_client)
        assert "Some Team" in _feed_names(admin_client)

    def test_the_rule_is_each_modules_own_a_watcher_sees_the_ticket(
        self, org_a, admin_user, admin_profile, user_profile, user_client
    ):
        """Proves the per-type rule is the module's, not a generic owner check:
        tickets admit watchers, so their activity follows."""
        case = _stamp(
            Case,
            Case.objects.create(name="Watched", status="New", org=org_a),
            admin_user,
        )
        _activity(org_a, "Case", case.id, "Watched", admin_profile)
        assert "Watched" not in _feed_names(user_client)
        CaseWatcher.objects.create(case=case, profile=user_profile, org=org_a)
        assert "Watched" in _feed_names(user_client)

    def test_entity_type_filter_still_narrows_within_what_is_visible(
        self, org_a, admin_profile, user_client, my_account, hidden_account, feed
    ):
        response = user_client.get(FEED, {"entity_type": "Account"})
        assert {r["entity_name"] for r in response.data["activities"]} == {"Mine Co"}

    def test_the_query_count_does_not_grow_with_the_rows(
        self, org_a, admin_profile, user_client, my_account
    ):
        def queries():
            with CaptureQueriesContext(connection) as ctx:
                assert user_client.get(FEED).status_code == status.HTTP_200_OK
            return len(ctx.captured_queries)

        _activity(org_a, "Account", my_account.id, "Mine Co", admin_profile)
        one = queries()
        for _ in range(5):
            _activity(org_a, "Account", my_account.id, "Mine Co", admin_profile)
        assert queries() == one


class TestActivityListLimit:
    @pytest.mark.parametrize("limit", ["abc", "-1", "0", ""])
    def test_an_unusable_limit_is_the_default_not_a_500(
        self, limit, admin_client, feed
    ):
        response = admin_client.get(FEED, {"limit": limit})
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 2

    def test_a_usable_limit_is_honoured(self, admin_client, feed):
        response = admin_client.get(FEED, {"limit": "1"})
        assert response.data["count"] == 1


def _client_without_org_claim(user):
    """A JWT that authenticates but names no org, as between login and org pick."""
    client = APIClient()
    token = RefreshToken.for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token.access_token}")
    return client


@pytest.mark.parametrize("url", [FEED, "/api/dashboard/today/", HOME])
class TestOrgContextIsRequired:
    def test_without_org_context_it_is_refused_not_crashed(
        self, url, admin_user, admin_profile
    ):
        response = _client_without_org_claim(admin_user).get(url)
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_with_org_context_it_still_answers(self, url, admin_client):
        assert admin_client.get(url).status_code == status.HTTP_200_OK

    def test_the_permission_class_is_declared(self, url):
        """The middleware answers 403 first today, so the pair above passes
        without the class. The class is the contract; this pins it."""
        from django.urls import resolve

        from common.permissions import HasOrgContext

        assert HasOrgContext in resolve(url).func.view_class.permission_classes
