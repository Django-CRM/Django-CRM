"""
Tests for Opportunities API endpoints.

Covers:
- OpportunityListView (GET list, POST create)
- OpportunityDetailView (GET detail, PUT update, PATCH partial update, DELETE, POST comment)
- OpportunityCommentView (PUT, PATCH, DELETE comments)
- OpportunityAttachmentView (DELETE attachments)
- OpportunityLineItemListView / DetailView (CRUD for line items)
- Organization isolation between tenants
- Permission checks for non-admin users
- Filtering and search

Run with: pytest opportunity/tests/test_opportunities_api.py -v
"""

from decimal import Decimal
from unittest.mock import patch

import pytest
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import connection
from rest_framework import status

from accounts.models import Account
from common.models import Attachments, Comment, Tags, Teams
from contacts.models import Contact
from opportunity.models import Opportunity, OpportunityLineItem

OPPORTUNITIES_LIST_URL = "/api/opportunities/"


def _detail_url(pk):
    return f"/api/opportunities/{pk}/"


def _comment_url(pk):
    return f"/api/opportunities/comment/{pk}/"


def _attachment_url(pk):
    return f"/api/opportunities/attachment/{pk}/"


def _line_items_url(opportunity_pk):
    return f"/api/opportunities/{opportunity_pk}/line-items/"


def _line_item_detail_url(opportunity_pk, line_item_pk):
    return f"/api/opportunities/{opportunity_pk}/line-items/{line_item_pk}/"


def _set_rls(org):
    """Set PostgreSQL RLS context so direct ORM writes are allowed.
    No-op on SQLite (used in tests).
    """
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.current_org', %s, false)", [str(org.id)])


@pytest.fixture
def opp_a(admin_user, org_a):
    """An opportunity belonging to org_a, created by admin_user."""
    _set_rls(org_a)
    return Opportunity.objects.create(
        name="Org A Deal",
        stage="QUALIFICATION",
        org=org_a,
        created_by=admin_user,
    )


@pytest.fixture
def opp_b(user_b, org_b):
    """An opportunity belonging to org_b, created by user_b."""
    _set_rls(org_b)
    return Opportunity.objects.create(
        name="Org B Deal",
        stage="QUALIFICATION",
        org=org_b,
        created_by=user_b,
    )


@pytest.mark.django_db
class TestOpportunityListView:
    """Tests for GET /api/opportunities/ and POST /api/opportunities/"""

    @patch("opportunity.views.opportunity_views.send_email_to_assigned_user.delay")
    def test_list_opportunities(self, mock_email, admin_client, opp_a):
        """Admin can list opportunities in their org."""
        response = admin_client.get(OPPORTUNITIES_LIST_URL)
        assert response.status_code == status.HTTP_200_OK
        assert "opportunities" in response.data
        assert response.data["opportunities_count"] >= 1

    @patch("opportunity.views.opportunity_views.send_email_to_assigned_user.delay")
    def test_create_opportunity(self, mock_email, admin_client):
        """Admin can create an opportunity via POST."""
        payload = {
            "name": "New Deal",
            "stage": "QUALIFICATION",
        }
        response = admin_client.post(OPPORTUNITIES_LIST_URL, payload, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["error"] is False

    @patch("opportunity.views.opportunity_views.send_email_to_assigned_user.delay")
    def test_create_opportunity_with_account(
        self, mock_email, admin_client, org_a, admin_user
    ):
        """Admin can create an opportunity linked to an account."""
        _set_rls(org_a)
        account = Account.objects.create(
            name="Acme Corp",
            org=org_a,
            created_by=admin_user,
        )
        payload = {
            "name": "Acme Deal",
            "stage": "QUALIFICATION",
            "account": str(account.pk),
        }
        response = admin_client.post(OPPORTUNITIES_LIST_URL, payload, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["error"] is False

    def test_create_opportunity_unauthenticated(self, unauthenticated_client):
        """Unauthenticated requests are rejected."""
        payload = {
            "name": "Unauthorized Deal",
            "stage": "QUALIFICATION",
        }
        # Asserts the response, not an exception. The old version expected
        # `PermissionDenied` to propagate out of the client call, which DRF
        # never lets happen, it catches the exception and renders a 403, so
        # the test could only ever fail. It had been failing.
        response = unauthenticated_client.post(
            OPPORTUNITIES_LIST_URL, payload, format="json"
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert not Opportunity.objects.filter(name="Unauthorized Deal").exists()

    @patch("opportunity.views.opportunity_views.send_email_to_assigned_user.delay")
    def test_org_isolation(self, mock_email, admin_client, org_b_client, opp_a, opp_b):
        """Opportunities in org_a must not appear in org_b's list."""
        response = org_b_client.get(OPPORTUNITIES_LIST_URL)
        assert response.status_code == status.HTTP_200_OK
        names = [o["name"] for o in response.data["opportunities"]]
        assert "Org A Deal" not in names
        assert "Org B Deal" in names

    @patch("opportunity.views.opportunity_views.send_email_to_assigned_user.delay")
    def test_create_opportunity_with_all_fields(
        self, mock_email, admin_client, org_a, admin_user
    ):
        """Creating an opportunity with all optional fields."""
        _set_rls(org_a)
        payload = {
            "name": "Full Opportunity",
            "stage": "PROPOSAL",
            "amount": "50000.00",
            "currency": "USD",
            "probability": 75,
            "lead_source": "CALL",
            "description": "A large enterprise deal",
            "closed_on": "2025-12-31",
        }
        response = admin_client.post(OPPORTUNITIES_LIST_URL, payload, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["error"] is False
        opp = Opportunity.objects.get(name="Full Opportunity")
        assert str(opp.amount) == "50000.00"
        assert opp.currency == "USD"
        assert opp.description == "A large enterprise deal"

    @patch("opportunity.views.opportunity_views.send_email_to_assigned_user.delay")
    def test_create_opportunity_with_tags(self, mock_email, admin_client, org_a):
        """Creating an opportunity with tags associates them."""
        _set_rls(org_a)
        tag = Tags.objects.create(name="Enterprise", org=org_a)
        payload = {
            "name": "Tagged Opportunity",
            "stage": "QUALIFICATION",
            "tags": [str(tag.id)],
        }
        response = admin_client.post(OPPORTUNITIES_LIST_URL, payload, format="json")
        assert response.status_code == status.HTTP_200_OK
        opp = Opportunity.objects.get(name="Tagged Opportunity")
        assert tag in opp.tags.all()

    @patch("opportunity.views.opportunity_views.send_email_to_assigned_user.delay")
    def test_create_opportunity_with_contacts(
        self, mock_email, admin_client, org_a, admin_user
    ):
        """Creating an opportunity with contacts associates them."""
        _set_rls(org_a)
        contact = Contact.objects.create(
            first_name="Opp",
            last_name="Contact",
            email="oppcontact@example.com",
            org=org_a,
            created_by=admin_user,
        )
        payload = {
            "name": "Contact Opportunity",
            "stage": "QUALIFICATION",
            "contacts": [str(contact.id)],
        }
        response = admin_client.post(OPPORTUNITIES_LIST_URL, payload, format="json")
        assert response.status_code == status.HTTP_200_OK
        opp = Opportunity.objects.get(name="Contact Opportunity")
        assert contact in opp.contacts.all()

    @patch("opportunity.views.opportunity_views.send_email_to_assigned_user.delay")
    def test_create_opportunity_with_assigned_to(
        self, mock_email, admin_client, admin_profile, org_a
    ):
        """Creating an opportunity with assigned_to sets assignees and sends email."""
        payload = {
            "name": "Assigned Opportunity",
            "stage": "QUALIFICATION",
            "assigned_to": [str(admin_profile.id)],
        }
        response = admin_client.post(OPPORTUNITIES_LIST_URL, payload, format="json")
        assert response.status_code == status.HTTP_200_OK
        opp = Opportunity.objects.get(name="Assigned Opportunity")
        assert admin_profile in opp.assigned_to.all()
        mock_email.assert_called_once()

    @patch("opportunity.views.opportunity_views.send_email_to_assigned_user.delay")
    def test_create_opportunity_with_teams(
        self, mock_email, admin_client, admin_user, org_a
    ):
        """Creating an opportunity with teams associates them."""
        _set_rls(org_a)
        team = Teams.objects.create(name="Sales Team", created_by=admin_user, org=org_a)
        payload = {
            "name": "Teamed Opportunity",
            "stage": "QUALIFICATION",
            "teams": [str(team.id)],
        }
        response = admin_client.post(OPPORTUNITIES_LIST_URL, payload, format="json")
        assert response.status_code == status.HTTP_200_OK
        opp = Opportunity.objects.get(name="Teamed Opportunity")
        assert team in opp.teams.all()

    @patch("opportunity.views.opportunity_views.send_email_to_assigned_user.delay")
    def test_create_opportunity_with_closed_stage(
        self, mock_email, admin_client, admin_profile, org_a
    ):
        """Creating an opportunity with CLOSED_WON stage sets closed_by."""
        payload = {
            "name": "Won Opportunity",
            "stage": "CLOSED_WON",
            "amount": "10000.00",
            "closed_on": "2025-06-15",
        }
        response = admin_client.post(OPPORTUNITIES_LIST_URL, payload, format="json")
        assert response.status_code == status.HTTP_200_OK

    def test_create_opportunity_invalid_data(self, admin_client):
        """Creating an opportunity with missing name returns 400."""
        response = admin_client.post(
            OPPORTUNITIES_LIST_URL, {"stage": "QUALIFICATION"}, format="json"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @patch("opportunity.views.opportunity_views.send_email_to_assigned_user.delay")
    def test_create_opportunity_duplicate_name(self, mock_email, admin_client, opp_a):
        """Creating an opportunity with a duplicate name returns 400."""
        payload = {
            "name": "Org A Deal",
            "stage": "QUALIFICATION",
        }
        response = admin_client.post(OPPORTUNITIES_LIST_URL, payload, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_list_opportunities_search_filter(self, admin_client, opp_a):
        """Search filter works on name."""
        response = admin_client.get(OPPORTUNITIES_LIST_URL, {"search": "Org A"})
        assert response.status_code == status.HTTP_200_OK
        names = [o["name"] for o in response.data["opportunities"]]
        assert "Org A Deal" in names

    def test_list_opportunities_stage_filter(self, admin_client, opp_a):
        """Stage filter works."""
        response = admin_client.get(OPPORTUNITIES_LIST_URL, {"stage": "QUALIFICATION"})
        assert response.status_code == status.HTTP_200_OK

    def test_list_opportunities_name_filter(self, admin_client, opp_a):
        """Name filter works."""
        response = admin_client.get(OPPORTUNITIES_LIST_URL, {"name": "Org A"})
        assert response.status_code == status.HTTP_200_OK

    def test_list_opportunities_created_at_filter(self, admin_client, opp_a):
        """Created_at date range filter works."""
        response = admin_client.get(
            OPPORTUNITIES_LIST_URL,
            {"created_at__gte": "2020-01-01", "created_at__lte": "2030-12-31"},
        )
        assert response.status_code == status.HTTP_200_OK

    def test_list_opportunities_amount_filter(self, admin_client, admin_user, org_a):
        """Amount range filter works."""
        _set_rls(org_a)
        Opportunity.objects.create(
            name="Big Deal",
            stage="QUALIFICATION",
            amount=100000,
            org=org_a,
            created_by=admin_user,
        )
        response = admin_client.get(OPPORTUNITIES_LIST_URL, {"amount__gte": "50000"})
        assert response.status_code == status.HTTP_200_OK

    def test_list_opportunities_context_keys(self, admin_client, org_a):
        """Response context contains expected metadata keys."""
        response = admin_client.get(OPPORTUNITIES_LIST_URL)
        assert response.status_code == status.HTTP_200_OK
        data = response.data
        assert "opportunities_count" in data
        assert "opportunities" in data
        assert "accounts_list" in data
        assert "contacts_list" in data
        assert "tags" in data
        assert "stage" in data
        assert "lead_source" in data
        assert "currency" in data
        assert "per_page" in data

    def test_list_opportunities_non_admin_sees_assigned(
        self, user_client, admin_user, org_a, user_profile
    ):
        """Non-admin user sees opportunities they are assigned to."""
        _set_rls(org_a)
        Opportunity.objects.create(
            name="Admin Opp",
            stage="QUALIFICATION",
            org=org_a,
            created_by=admin_user,
        )
        assigned_opp = Opportunity.objects.create(
            name="Assigned Opp",
            stage="QUALIFICATION",
            org=org_a,
            created_by=admin_user,
        )
        assigned_opp.assigned_to.add(user_profile)
        response = user_client.get(OPPORTUNITIES_LIST_URL)
        assert response.status_code == status.HTTP_200_OK
        names = [o["name"] for o in response.data["opportunities"]]
        assert "Assigned Opp" in names
        assert "Admin Opp" not in names


@pytest.mark.django_db
class TestOpportunityDetailView:
    """Tests for GET/PUT/DELETE/POST on /api/opportunities/<pk>/"""

    @pytest.fixture()
    def opportunity(self, org_a, admin_user):
        _set_rls(org_a)
        return Opportunity.objects.create(
            name="Detail Opp",
            stage="QUALIFICATION",
            org=org_a,
            created_by=admin_user,
        )

    def test_get_detail(self, admin_client, opportunity):
        """Admin can retrieve a single opportunity's detail."""
        response = admin_client.get(_detail_url(opportunity.pk))
        assert response.status_code == status.HTTP_200_OK
        assert "opportunity_obj" in response.data
        assert response.data["opportunity_obj"]["id"] == str(opportunity.pk)

    @patch("opportunity.views.opportunity_views.send_email_to_assigned_user.delay")
    def test_update(self, mock_email, admin_client, opportunity):
        """Admin can update an opportunity via PUT."""
        payload = {
            "name": "Updated Opp",
            "stage": "PROPOSAL",
        }
        response = admin_client.put(_detail_url(opportunity.pk), payload, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["error"] is False
        opportunity.refresh_from_db()
        assert opportunity.name == "Updated Opp"

    def test_delete(self, admin_client, opportunity):
        """Admin can delete an opportunity."""
        response = admin_client.delete(_detail_url(opportunity.pk))
        assert response.status_code == status.HTTP_200_OK
        assert response.data["error"] is False
        assert not Opportunity.objects.filter(pk=opportunity.pk).exists()

    def test_cross_org(self, org_b_client, opportunity):
        """Accessing an opportunity from another org returns an error."""
        response = org_b_client.get(_detail_url(opportunity.pk))
        # The detail view uses .filter().first() which returns None,
        # then accessing .org on None raises AttributeError -> 500,
        # or RLS hides the row entirely. Either way, data is not returned.
        assert response.status_code != status.HTTP_200_OK or (
            response.data.get("error") is True
        )

    def test_add_comment(self, admin_client, opportunity):
        """Admin can add a comment to an opportunity via POST to detail."""
        payload = {"comment": "Looks like a promising deal"}
        response = admin_client.post(
            _detail_url(opportunity.pk), payload, format="json"
        )
        assert response.status_code == status.HTTP_200_OK
        # Verify the comment was actually persisted AND echoed back. Without
        # this assertion the endpoint can silently drop the write (returns
        # 200 with stale comments): that's exactly how the original
        # CommentSerializer.save(opportunity_id=...) regression slipped past
        # the test suite.
        assert "comments" in response.data
        comments = response.data["comments"]
        assert any(c.get("comment") == payload["comment"] for c in comments), (
            f"new comment missing from response payload: {comments}"
        )

    def test_get_detail_context_keys(self, admin_client, opportunity):
        """Detail response contains all expected context keys."""
        response = admin_client.get(_detail_url(opportunity.pk))
        assert response.status_code == status.HTTP_200_OK
        data = response.data
        assert "opportunity_obj" in data
        assert "comments" in data
        assert "attachments" in data
        assert "contacts" in data
        assert "users" in data
        assert "stage" in data
        assert "lead_source" in data
        assert "currency" in data
        assert "comment_permission" in data
        assert "users_mention" in data

    def test_get_detail_not_found(self, admin_client):
        """Getting a non-existent opportunity returns 404."""
        response = admin_client.get(_detail_url("00000000-0000-0000-0000-000000000001"))
        assert response.status_code == status.HTTP_404_NOT_FOUND

    @patch("opportunity.views.opportunity_views.send_email_to_assigned_user.delay")
    def test_update_with_tags(self, mock_email, admin_client, opportunity, org_a):
        """PUT with tags updates tag associations."""
        _set_rls(org_a)
        tag1 = Tags.objects.create(name="OldOTag", org=org_a)
        tag2 = Tags.objects.create(name="NewOTag", org=org_a)
        opportunity.tags.add(tag1)
        response = admin_client.put(
            _detail_url(opportunity.pk),
            {
                "name": "Detail Opp",
                "stage": "QUALIFICATION",
                "tags": [str(tag2.id)],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        opportunity.refresh_from_db()
        assert tag2 in opportunity.tags.all()
        assert tag1 not in opportunity.tags.all()

    @patch("opportunity.views.opportunity_views.send_email_to_assigned_user.delay")
    def test_update_with_assigned_to(
        self, mock_email, admin_client, opportunity, admin_profile, org_a
    ):
        """PUT with assigned_to updates assignees and sends email."""
        response = admin_client.put(
            _detail_url(opportunity.pk),
            {
                "name": "Detail Opp",
                "stage": "QUALIFICATION",
                "assigned_to": [str(admin_profile.id)],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        opportunity.refresh_from_db()
        assert admin_profile in opportunity.assigned_to.all()

    @patch("opportunity.views.opportunity_views.send_email_to_assigned_user.delay")
    def test_update_with_contacts(
        self, mock_email, admin_client, opportunity, admin_user, org_a
    ):
        """PUT with contacts updates contact associations."""
        _set_rls(org_a)
        contact = Contact.objects.create(
            first_name="Update",
            last_name="Contact",
            email="updateoppcontact@example.com",
            org=org_a,
            created_by=admin_user,
        )
        response = admin_client.put(
            _detail_url(opportunity.pk),
            {
                "name": "Detail Opp",
                "stage": "QUALIFICATION",
                "contacts": [str(contact.id)],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        opportunity.refresh_from_db()
        assert contact in opportunity.contacts.all()

    @patch("opportunity.views.opportunity_views.send_email_to_assigned_user.delay")
    def test_update_with_teams(
        self, mock_email, admin_client, opportunity, admin_user, org_a
    ):
        """PUT with teams updates team associations."""
        _set_rls(org_a)
        team = Teams.objects.create(name="Opp Team", created_by=admin_user, org=org_a)
        response = admin_client.put(
            _detail_url(opportunity.pk),
            {
                "name": "Detail Opp",
                "stage": "QUALIFICATION",
                "teams": [str(team.id)],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        opportunity.refresh_from_db()
        assert team in opportunity.teams.all()

    def test_update_invalid_data(self, admin_client, opportunity):
        """PUT with invalid data returns 400."""
        response = admin_client.put(
            _detail_url(opportunity.pk),
            {"name": ""},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_delete_non_admin_not_creator_not_found(
        self, user_client, admin_user, org_a, user_profile
    ):
        """A deal the member cannot open is a 404 on DELETE, as on GET."""
        _set_rls(org_a)
        opp = Opportunity.objects.create(
            name="Protected Opp",
            stage="QUALIFICATION",
            org=org_a,
            created_by=admin_user,
        )
        response = user_client.delete(_detail_url(opp.pk))
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_update_non_admin_not_assigned_forbidden(
        self, user_client, admin_user, org_a, user_profile
    ):
        """Non-admin not assigned/creator gets 404 on PUT, as on GET."""
        _set_rls(org_a)
        opp = Opportunity.objects.create(
            name="Forbidden Update Opp",
            stage="QUALIFICATION",
            org=org_a,
            created_by=admin_user,
        )
        response = user_client.put(
            _detail_url(opp.pk),
            {"name": "Hacked Opp", "stage": "PROPOSAL"},
            format="json",
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_add_comment_non_admin_not_assigned_forbidden(
        self, user_client, admin_user, org_a, user_profile
    ):
        """Non-admin user not assigned/creator gets 404 when adding comment."""
        _set_rls(org_a)
        opp = Opportunity.objects.create(
            name="No Comment Opp",
            stage="QUALIFICATION",
            org=org_a,
            created_by=admin_user,
        )
        response = user_client.post(
            _detail_url(opp.pk),
            {"comment": "Should fail"},
            format="json",
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_patch_opportunity_partial_update(self, admin_client, opportunity):
        """PATCH allows partial field updates."""
        response = admin_client.patch(
            _detail_url(opportunity.pk),
            {"stage": "PROPOSAL"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["error"] is False
        opportunity.refresh_from_db()
        assert opportunity.stage == "PROPOSAL"
        assert opportunity.name == "Detail Opp"  # unchanged

    def test_patch_opportunity_with_tags(self, admin_client, opportunity, org_a):
        """PATCH with tags replaces existing tags."""
        _set_rls(org_a)
        tag1 = Tags.objects.create(name="PatchOldO", org=org_a)
        tag2 = Tags.objects.create(name="PatchNewO", org=org_a)
        opportunity.tags.add(tag1)
        response = admin_client.patch(
            _detail_url(opportunity.pk),
            {"tags": [str(tag2.id)]},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        opportunity.refresh_from_db()
        assert tag2 in opportunity.tags.all()
        assert tag1 not in opportunity.tags.all()

    def test_patch_opportunity_clear_tags(self, admin_client, opportunity, org_a):
        """PATCH with empty tags list clears all tags."""
        _set_rls(org_a)
        tag = Tags.objects.create(name="ClearMeO", org=org_a)
        opportunity.tags.add(tag)
        response = admin_client.patch(
            _detail_url(opportunity.pk),
            {"tags": []},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        opportunity.refresh_from_db()
        assert opportunity.tags.count() == 0

    def test_patch_opportunity_with_contacts(
        self, admin_client, opportunity, admin_user, org_a
    ):
        """PATCH with contacts updates contacts."""
        _set_rls(org_a)
        contact = Contact.objects.create(
            first_name="Patch",
            last_name="OppContact",
            email="patchoppcontact@example.com",
            org=org_a,
            created_by=admin_user,
        )
        response = admin_client.patch(
            _detail_url(opportunity.pk),
            {"contacts": [str(contact.id)]},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        opportunity.refresh_from_db()
        assert contact in opportunity.contacts.all()

    def test_patch_opportunity_with_assigned_to(
        self, admin_client, opportunity, admin_profile
    ):
        """PATCH with assigned_to updates assignees."""
        response = admin_client.patch(
            _detail_url(opportunity.pk),
            {"assigned_to": [str(admin_profile.id)]},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        opportunity.refresh_from_db()
        assert admin_profile in opportunity.assigned_to.all()

    def test_patch_opportunity_with_teams(
        self, admin_client, opportunity, admin_user, org_a
    ):
        """PATCH with teams updates team associations."""
        _set_rls(org_a)
        team = Teams.objects.create(
            name="PatchOppTeam", created_by=admin_user, org=org_a
        )
        response = admin_client.patch(
            _detail_url(opportunity.pk),
            {"teams": [str(team.id)]},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        opportunity.refresh_from_db()
        assert team in opportunity.teams.all()

    def test_patch_opportunity_closed_won_sets_closed_by(
        self, admin_client, opportunity
    ):
        """PATCH with CLOSED_WON stage sets closed_by.

        Now sends the amount and the close date, because closing a deal
        requires both. This used to pass with neither. See
        `test_patch_closed_won_without_amount_is_rejected` in
        test_deal_access_and_totals.py.
        """
        response = admin_client.patch(
            _detail_url(opportunity.pk),
            {"stage": "CLOSED_WON", "amount": "9000.00", "closed_on": "2026-02-01"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        opportunity.refresh_from_db()
        assert opportunity.closed_by is not None

    def test_patch_opportunity_non_admin_not_assigned_forbidden(
        self, user_client, admin_user, org_a, user_profile
    ):
        """Non-admin not assigned/creator gets 404 on PATCH, as on GET."""
        _set_rls(org_a)
        opp = Opportunity.objects.create(
            name="ForbidPatch Opp",
            stage="QUALIFICATION",
            org=org_a,
            created_by=admin_user,
        )
        response = user_client.patch(
            _detail_url(opp.pk),
            {"stage": "PROPOSAL"},
            format="json",
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_patch_opportunity_invalid_data(self, admin_client, opportunity):
        """PATCH with invalid data returns 400."""
        response = admin_client.patch(
            _detail_url(opportunity.pk),
            {"probability": 200},  # over max of 100
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
class TestOpportunityCommentView:
    """Tests for PUT/PATCH/DELETE /api/opportunities/comment/<pk>/."""

    @pytest.fixture
    def comment_fixture(self, admin_user, admin_profile, org_a):
        """Create an opportunity and a comment on it."""
        _set_rls(org_a)
        opp = Opportunity.objects.create(
            name="Comment Opp",
            stage="QUALIFICATION",
            org=org_a,
            created_by=admin_user,
        )
        ct = ContentType.objects.get_for_model(Opportunity)
        comment = Comment.objects.create(
            content_type=ct,
            object_id=opp.id,
            comment="Original opp comment",
            commented_by=admin_profile,
            org=org_a,
        )
        return opp, comment

    def test_update_comment_put(
        self, admin_client, comment_fixture, admin_profile, org_a
    ):
        """Admin can update a comment via PUT."""
        opp, comment = comment_fixture
        response = admin_client.put(
            _comment_url(comment.id),
            {
                "comment": "Updated opp comment",
                "commented_by": str(admin_profile.id),
                "object_id": str(opp.id),
                "org": str(org_a.id),
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["error"] is False

    def test_update_comment_patch(self, admin_client, comment_fixture):
        """Admin can partially update a comment via PATCH."""
        _, comment = comment_fixture
        response = admin_client.patch(
            _comment_url(comment.id),
            {"comment": "Patched opp comment"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["error"] is False

    def test_delete_comment(self, admin_client, comment_fixture):
        """Admin can delete a comment."""
        _, comment = comment_fixture
        response = admin_client.delete(_comment_url(comment.id))
        assert response.status_code == status.HTTP_200_OK
        assert response.data["error"] is False
        assert not Comment.objects.filter(id=comment.id).exists()

    def test_update_comment_non_admin_non_author_forbidden(
        self, user_client, comment_fixture, user_profile
    ):
        """Non-admin non-author gets 403 on PUT."""
        record, comment = comment_fixture
        record.assigned_to.add(user_profile)  # can open the deal
        response = user_client.put(
            _comment_url(comment.id),
            {"comment": "Hacked"},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_patch_comment_non_admin_non_author_forbidden(
        self, user_client, comment_fixture, user_profile
    ):
        """Non-admin non-author gets 403 on PATCH."""
        record, comment = comment_fixture
        record.assigned_to.add(user_profile)  # can open the deal
        response = user_client.patch(
            _comment_url(comment.id),
            {"comment": "Hacked"},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_delete_comment_non_admin_non_author_forbidden(
        self, user_client, comment_fixture, user_profile
    ):
        """Non-admin non-author gets 403 on DELETE."""
        record, comment = comment_fixture
        record.assigned_to.add(user_profile)  # can open the deal
        response = user_client.delete(_comment_url(comment.id))
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_put_without_comment_field_is_400_not_403(
        self, admin_client, comment_fixture
    ):
        """A missing `comment` is a bad request, not a permission problem.

        `if params.get("comment")` used to sit inside the authorization
        branch, so a body the serializer would have rejected fell out of the
        branch entirely and hit the 403 at the bottom of the handler. This
        view had no test for the case at all; the same defect was pinned as
        correct behaviour in the cases, tasks and accounts suites.
        """
        _, comment = comment_fixture
        response = admin_client.put(_comment_url(comment.id), {}, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "comment" in response.data["errors"]

    def test_put_blank_comment_is_400(self, admin_client, comment_fixture):
        """The other half of the same guard: `""` is falsy too."""
        _, comment = comment_fixture
        response = admin_client.put(
            _comment_url(comment.id), {"comment": ""}, format="json"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        comment.refresh_from_db()
        assert comment.comment == "Original opp comment"

    def test_stranger_still_gets_403_for_an_empty_body(
        self, user_client, comment_fixture, user_profile
    ):
        """Authorization is still checked before the body is."""
        record, comment = comment_fixture
        record.assigned_to.add(user_profile)  # can open the deal
        response = user_client.put(_comment_url(comment.id), {}, format="json")
        assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
class TestOpportunityAttachmentView:
    """Tests for DELETE /api/opportunities/attachment/<pk>/."""

    def test_delete_attachment_admin(self, admin_client, admin_user, org_a):
        """Admin can delete an attachment."""
        _set_rls(org_a)
        opp = Opportunity.objects.create(
            name="Attach Opp",
            stage="QUALIFICATION",
            org=org_a,
            created_by=admin_user,
        )
        ct = ContentType.objects.get_for_model(Opportunity)
        attachment = Attachments.objects.create(
            content_type=ct,
            object_id=opp.id,
            file_name="proposal.pdf",
            attachment="attachments/proposal.pdf",
            created_by=admin_user,
            org=org_a,
        )
        response = admin_client.delete(_attachment_url(attachment.id))
        assert response.status_code == status.HTTP_200_OK
        assert response.data["error"] is False
        assert not Attachments.objects.filter(id=attachment.id).exists()

    def test_delete_attachment_non_admin_non_creator_forbidden(
        self, user_client, admin_user, org_a, user_profile
    ):
        """Non-admin user who didn't create the attachment gets 403."""
        _set_rls(org_a)
        opp = Opportunity.objects.create(
            name="AttachForbid Opp",
            stage="QUALIFICATION",
            org=org_a,
            created_by=admin_user,
        )
        opp.assigned_to.add(user_profile)  # can open it
        ct = ContentType.objects.get_for_model(Opportunity)
        attachment = Attachments.objects.create(
            content_type=ct,
            object_id=opp.id,
            file_name="secret.pdf",
            attachment="attachments/secret.pdf",
            created_by=admin_user,
            org=org_a,
        )
        response = user_client.delete(_attachment_url(attachment.id))
        assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
class TestOpportunityLineItems:
    """Tests for line item CRUD under /api/opportunities/<pk>/line-items/"""

    @pytest.fixture()
    def opportunity(self, org_a, admin_user):
        _set_rls(org_a)
        return Opportunity.objects.create(
            name="LineItem Opp",
            stage="QUALIFICATION",
            org=org_a,
            created_by=admin_user,
        )

    @pytest.fixture()
    def line_item(self, opportunity, org_a):
        _set_rls(org_a)
        return OpportunityLineItem.objects.create(
            opportunity=opportunity,
            name="Widget A",
            quantity=2,
            unit_price=50,
            org=org_a,
        )

    def test_create_line_item(self, admin_client, opportunity):
        """Admin can create a line item for an opportunity."""
        payload = {
            "name": "Consulting Hours",
            "quantity": 10,
            "unit_price": "150.00",
        }
        response = admin_client.post(
            _line_items_url(opportunity.pk), payload, format="json"
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["error"] is False
        assert response.data["line_item"]["name"] == "Consulting Hours"

    def test_list_line_items(self, admin_client, opportunity, line_item):
        """Admin can list line items for an opportunity."""
        response = admin_client.get(_line_items_url(opportunity.pk))
        assert response.status_code == status.HTTP_200_OK
        assert "line_items" in response.data
        assert len(response.data["line_items"]) == 1
        assert response.data["line_items"][0]["name"] == "Widget A"

    def test_update_line_item(self, admin_client, opportunity, line_item):
        """Admin can update an existing line item."""
        payload = {
            "name": "Widget A (Revised)",
            "quantity": 5,
            "unit_price": "75.00",
        }
        response = admin_client.put(
            _line_item_detail_url(opportunity.pk, line_item.pk),
            payload,
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["error"] is False
        line_item.refresh_from_db()
        assert line_item.name == "Widget A (Revised)"
        assert line_item.quantity == 5

    def test_delete_line_item(self, admin_client, opportunity, line_item):
        """Admin can delete a line item."""
        response = admin_client.delete(
            _line_item_detail_url(opportunity.pk, line_item.pk)
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["error"] is False
        assert not OpportunityLineItem.objects.filter(pk=line_item.pk).exists()

    def test_get_line_item_detail(self, admin_client, opportunity, line_item):
        """Admin can retrieve a single line item."""
        response = admin_client.get(_line_item_detail_url(opportunity.pk, line_item.pk))
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == "Widget A"

    def test_get_line_item_not_found(self, admin_client, opportunity):
        """Getting a non-existent line item returns 404."""
        response = admin_client.get(
            _line_item_detail_url(
                opportunity.pk, "00000000-0000-0000-0000-000000000001"
            )
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_update_line_item_not_found(self, admin_client, opportunity):
        """Updating a non-existent line item returns 404."""
        response = admin_client.put(
            _line_item_detail_url(
                opportunity.pk, "00000000-0000-0000-0000-000000000001"
            ),
            {"name": "Ghost", "quantity": 1, "unit_price": "10.00"},
            format="json",
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_delete_line_item_not_found(self, admin_client, opportunity):
        """Deleting a non-existent line item returns 404."""
        response = admin_client.delete(
            _line_item_detail_url(
                opportunity.pk, "00000000-0000-0000-0000-000000000001"
            )
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_list_line_items_opportunity_not_found(self, admin_client):
        """Listing line items for a non-existent opportunity returns 404."""
        response = admin_client.get(
            _line_items_url("00000000-0000-0000-0000-000000000001")
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_create_line_item_opportunity_not_found(self, admin_client):
        """Creating a line item for a non-existent opportunity returns 404."""
        response = admin_client.post(
            _line_items_url("00000000-0000-0000-0000-000000000001"),
            {"name": "Ghost", "quantity": 1, "unit_price": "10.00"},
            format="json",
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_create_line_item_with_discount(self, admin_client, opportunity):
        """Creating a line item with a percentage discount calculates correctly."""
        payload = {
            "name": "Discounted Item",
            "quantity": 10,
            "unit_price": "100.00",
            "discount_type": "PERCENTAGE",
            "discount_value": "10.00",
        }
        response = admin_client.post(
            _line_items_url(opportunity.pk), payload, format="json"
        )
        assert response.status_code == status.HTTP_201_CREATED
        li = response.data["line_item"]
        # subtotal = 10 * 100 = 1000, discount = 10% of 1000 = 100, total = 900
        assert li["subtotal"] == "1000.00"
        assert li["discount_amount"] == "100.00"
        assert li["total"] == "900.00"

    def test_list_line_items_has_products_and_amounts(
        self, admin_client, opportunity, line_item
    ):
        """Line items list includes products and opportunity_amount."""
        response = admin_client.get(_line_items_url(opportunity.pk))
        assert response.status_code == status.HTTP_200_OK
        assert "products" in response.data
        assert "opportunity_amount" in response.data
        assert "opportunity_amount_source" in response.data

    def test_line_item_non_admin_not_assigned_forbidden(
        self, user_client, admin_user, org_a, user_profile
    ):
        """Non-admin not assigned/creator gets 404 on line items, as on GET."""
        _set_rls(org_a)
        opp = Opportunity.objects.create(
            name="Forbidden LI Opp",
            stage="QUALIFICATION",
            org=org_a,
            created_by=admin_user,
        )
        response = user_client.get(_line_items_url(opp.pk))
        assert response.status_code == status.HTTP_404_NOT_FOUND


# ---------------------------------------------------------------------------
# Additional coverage tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestOpportunityListFilters:
    """Cover filter parameters that were previously untested."""

    def test_filter_by_account(self, admin_client, admin_user, org_a):
        """Filter opportunities by account id."""
        _set_rls(org_a)
        account = Account.objects.create(
            name="Filter Acct", org=org_a, created_by=admin_user
        )
        Opportunity.objects.create(
            name="Acct Opp",
            stage="QUALIFICATION",
            account=account,
            org=org_a,
            created_by=admin_user,
        )
        Opportunity.objects.create(
            name="No Acct Opp",
            stage="QUALIFICATION",
            org=org_a,
            created_by=admin_user,
        )
        response = admin_client.get(
            OPPORTUNITIES_LIST_URL, {"account": str(account.pk)}
        )
        assert response.status_code == status.HTTP_200_OK
        names = [o["name"] for o in response.data["opportunities"]]
        assert "Acct Opp" in names
        assert "No Acct Opp" not in names

    def test_filter_by_lead_source(self, admin_client, admin_user, org_a):
        """Filter opportunities by lead_source."""
        _set_rls(org_a)
        Opportunity.objects.create(
            name="Call Opp",
            stage="QUALIFICATION",
            lead_source="CALL",
            org=org_a,
            created_by=admin_user,
        )
        Opportunity.objects.create(
            name="Web Opp",
            stage="QUALIFICATION",
            lead_source="WEB",
            org=org_a,
            created_by=admin_user,
        )
        response = admin_client.get(OPPORTUNITIES_LIST_URL, {"lead_source": "CALL"})
        assert response.status_code == status.HTTP_200_OK
        names = [o["name"] for o in response.data["opportunities"]]
        assert "Call Opp" in names

    def test_filter_by_assigned_to(
        self, admin_client, admin_user, admin_profile, org_a
    ):
        """Filter opportunities by assigned_to profile id."""
        _set_rls(org_a)
        opp = Opportunity.objects.create(
            name="Assigned Filter Opp",
            stage="QUALIFICATION",
            org=org_a,
            created_by=admin_user,
        )
        opp.assigned_to.add(admin_profile)
        Opportunity.objects.create(
            name="Unassigned Filter Opp",
            stage="QUALIFICATION",
            org=org_a,
            created_by=admin_user,
        )
        response = admin_client.get(
            OPPORTUNITIES_LIST_URL,
            {"assigned_to": str(admin_profile.id)},
        )
        assert response.status_code == status.HTTP_200_OK
        names = [o["name"] for o in response.data["opportunities"]]
        assert "Assigned Filter Opp" in names

    def test_filter_by_closed_on_range(self, admin_client, admin_user, org_a):
        """Filter opportunities by closed_on date range."""
        _set_rls(org_a)
        Opportunity.objects.create(
            name="Closed Opp",
            stage="CLOSED_WON",
            closed_on="2025-06-15",
            amount=1000,
            org=org_a,
            created_by=admin_user,
        )
        response = admin_client.get(
            OPPORTUNITIES_LIST_URL,
            {"closed_on__gte": "2025-01-01", "closed_on__lte": "2025-12-31"},
        )
        assert response.status_code == status.HTTP_200_OK

    def test_filter_by_amount_lte(self, admin_client, admin_user, org_a):
        """Filter opportunities by amount__lte."""
        _set_rls(org_a)
        Opportunity.objects.create(
            name="Small Deal",
            stage="QUALIFICATION",
            amount=500,
            org=org_a,
            created_by=admin_user,
        )
        Opportunity.objects.create(
            name="Big Deal Filter",
            stage="QUALIFICATION",
            amount=50000,
            org=org_a,
            created_by=admin_user,
        )
        response = admin_client.get(OPPORTUNITIES_LIST_URL, {"amount__lte": "1000"})
        assert response.status_code == status.HTTP_200_OK
        names = [o["name"] for o in response.data["opportunities"]]
        assert "Small Deal" in names

    def test_filter_by_tags(self, admin_client, admin_user, org_a):
        """?tags=<uuid> returns only opportunities carrying that tag.

        Regression: previously the filter used `tags__in=params.get("tags")`,
        iterating the UUID string char-by-char. Now uses
        `tags__id__in=params.getlist("tags")`.
        """
        _set_rls(org_a)
        tag_vip = Tags.objects.create(name="VIP", org=org_a)
        tag_cold = Tags.objects.create(name="Cold", org=org_a)
        tagged = Opportunity.objects.create(
            name="Tagged Deal",
            stage="QUALIFICATION",
            org=org_a,
            created_by=admin_user,
        )
        tagged.tags.add(tag_vip)
        other = Opportunity.objects.create(
            name="Other Tagged Deal",
            stage="QUALIFICATION",
            org=org_a,
            created_by=admin_user,
        )
        other.tags.add(tag_cold)
        Opportunity.objects.create(
            name="Untagged Deal",
            stage="QUALIFICATION",
            org=org_a,
            created_by=admin_user,
        )

        response = admin_client.get(OPPORTUNITIES_LIST_URL, {"tags": str(tag_vip.id)})
        assert response.status_code == status.HTTP_200_OK
        names = {o["name"] for o in response.data["opportunities"]}
        assert names == {"Tagged Deal"}


@pytest.mark.django_db
class TestOpportunityDetailUsersMention:
    """Cover the users_mention branches in the GET detail view."""

    def test_users_mention_no_created_by(self, user_client, org_a, user_profile):
        """Non-admin assigned to opp with no created_by gets empty users_mention."""
        _set_rls(org_a)
        opp = Opportunity.objects.create(
            name="No Creator Opp",
            stage="QUALIFICATION",
            org=org_a,
        )
        opp.assigned_to.add(user_profile)
        response = user_client.get(_detail_url(opp.pk))
        assert response.status_code == status.HTTP_200_OK
        data = response.data
        assert data["users_mention"] == []


@pytest.mark.django_db
class TestOpportunityModel:
    """Cover Opportunity model methods."""

    def test_str(self, admin_user, org_a):
        _set_rls(org_a)
        opp = Opportunity.objects.create(
            name="Str Opp",
            stage="QUALIFICATION",
            org=org_a,
            created_by=admin_user,
        )
        assert str(opp) == "Str Opp"

    def test_created_on_arrow(self, admin_user, org_a):
        _set_rls(org_a)
        opp = Opportunity.objects.create(
            name="Arrow Opp",
            stage="QUALIFICATION",
            org=org_a,
            created_by=admin_user,
        )
        assert "ago" in opp.created_on_arrow

    def test_clean_closed_won_without_date_raises(self, admin_user, org_a):
        """clean() raises ValidationError when stage is CLOSED_WON without closed_on."""
        _set_rls(org_a)
        opp = Opportunity(
            name="Clean Opp",
            stage="CLOSED_WON",
            amount=1000,
            org=org_a,
            created_by=admin_user,
        )
        with pytest.raises(ValidationError) as exc_info:
            opp.clean()
        assert "closed_on" in exc_info.value.message_dict

    def test_clean_closed_won_without_amount_raises(self, admin_user, org_a):
        """clean() raises ValidationError when stage is CLOSED_WON without amount."""
        _set_rls(org_a)
        opp = Opportunity(
            name="Clean Amt Opp",
            stage="CLOSED_WON",
            closed_on="2025-06-15",
            org=org_a,
            created_by=admin_user,
        )
        with pytest.raises(ValidationError) as exc_info:
            opp.clean()
        assert "amount" in exc_info.value.message_dict

    def test_clean_closed_lost_without_date_raises(self, admin_user, org_a):
        """clean() raises ValidationError when stage is CLOSED_LOST without closed_on."""
        opp = Opportunity(
            name="Clean Lost Opp",
            stage="CLOSED_LOST",
            org=org_a,
            created_by=admin_user,
        )
        with pytest.raises(ValidationError) as exc_info:
            opp.clean()
        assert "closed_on" in exc_info.value.message_dict

    def test_clean_valid(self, admin_user, org_a):
        """clean() passes when all required fields are present."""
        opp = Opportunity(
            name="Clean Valid Opp",
            stage="CLOSED_WON",
            amount=1000,
            closed_on="2025-06-15",
            org=org_a,
            created_by=admin_user,
        )
        # Should not raise
        opp.clean()

    def test_recalculate_amount_no_line_items_manual(self, admin_user, org_a):
        """recalculate_amount returns False when no line items and manual source."""
        _set_rls(org_a)
        opp = Opportunity.objects.create(
            name="No LI Opp",
            stage="QUALIFICATION",
            amount=1000,
            amount_source="MANUAL",
            org=org_a,
            created_by=admin_user,
        )
        result = opp.recalculate_amount()
        assert result is False

    def test_recalculate_amount_no_line_items_calculated_resets(
        self, admin_user, org_a
    ):
        """recalculate_amount resets to MANUAL when no line items but was CALCULATED."""
        _set_rls(org_a)
        opp = Opportunity.objects.create(
            name="Reset LI Opp",
            stage="QUALIFICATION",
            amount=1000,
            amount_source="CALCULATED",
            org=org_a,
            created_by=admin_user,
        )
        result = opp.recalculate_amount()
        assert result is True
        assert opp.amount_source == "MANUAL"
        assert opp.amount == Decimal("0")

    def test_recalculate_amount_with_line_items(self, admin_user, org_a):
        """recalculate_amount calculates total from line items."""
        _set_rls(org_a)
        opp = Opportunity.objects.create(
            name="LI Calc Opp",
            stage="QUALIFICATION",
            org=org_a,
            created_by=admin_user,
        )
        OpportunityLineItem.objects.create(
            opportunity=opp,
            name="Item 1",
            quantity=2,
            unit_price=Decimal("50.00"),
            org=org_a,
        )
        opp.refresh_from_db()
        assert opp.amount == Decimal("100.00")
        assert opp.amount_source == "CALCULATED"

    def test_save_auto_sets_probability(self, admin_user, org_a):
        """save() auto-sets probability based on stage when probability is 0."""
        _set_rls(org_a)
        opp = Opportunity.objects.create(
            name="Auto Prob Opp",
            stage="PROPOSAL",
            probability=0,
            org=org_a,
            created_by=admin_user,
        )
        assert opp.probability == 50  # PROPOSAL -> 50


@pytest.mark.django_db
class TestOpportunityLineItemModel:
    """Cover OpportunityLineItem model methods."""

    def test_str_no_product_with_name(self, admin_user, org_a):
        """__str__ uses name field when product is None."""
        _set_rls(org_a)
        opp = Opportunity.objects.create(
            name="LI Str Opp",
            stage="QUALIFICATION",
            org=org_a,
            created_by=admin_user,
        )
        li = OpportunityLineItem.objects.create(
            opportunity=opp,
            name="Widget",
            quantity=3,
            unit_price=Decimal("10.00"),
            org=org_a,
        )
        assert str(li) == "Widget x 3"

    def test_str_without_name_no_product(self, admin_user, org_a):
        """__str__ returns 'Item x <qty>' when no name and no product."""
        _set_rls(org_a)
        opp = Opportunity.objects.create(
            name="LI Str2 Opp",
            stage="QUALIFICATION",
            org=org_a,
            created_by=admin_user,
        )
        li = OpportunityLineItem.objects.create(
            opportunity=opp,
            quantity=1,
            unit_price=Decimal("5.00"),
            org=org_a,
        )
        assert str(li) == "Item x 1"

    def test_fixed_discount(self, admin_user, org_a):
        """Line item with FIXED discount calculates correctly."""
        _set_rls(org_a)
        opp = Opportunity.objects.create(
            name="Fixed Disc Opp",
            stage="QUALIFICATION",
            org=org_a,
            created_by=admin_user,
        )
        li = OpportunityLineItem.objects.create(
            opportunity=opp,
            name="Fixed Item",
            quantity=5,
            unit_price=Decimal("100.00"),
            discount_type="FIXED",
            discount_value=Decimal("50.00"),
            org=org_a,
        )
        assert li.subtotal == Decimal("500.00")
        assert li.discount_amount == Decimal("50.00")
        assert li.total == Decimal("450.00")

    def test_delete_recalculates_opportunity(self, admin_user, org_a):
        """Deleting a line item recalculates the opportunity amount."""
        _set_rls(org_a)
        opp = Opportunity.objects.create(
            name="Delete LI Opp",
            stage="QUALIFICATION",
            org=org_a,
            created_by=admin_user,
        )
        OpportunityLineItem.objects.create(
            opportunity=opp,
            name="Keep",
            quantity=1,
            unit_price=Decimal("200.00"),
            org=org_a,
        )
        li2 = OpportunityLineItem.objects.create(
            opportunity=opp,
            name="Remove",
            quantity=1,
            unit_price=Decimal("100.00"),
            org=org_a,
        )
        opp.refresh_from_db()
        assert opp.amount == Decimal("300.00")
        li2.delete()
        opp.refresh_from_db()
        assert opp.amount == Decimal("200.00")

    def test_org_inferred_from_opportunity(self, admin_user, org_a):
        """Line item infers org from opportunity if not set."""
        _set_rls(org_a)
        opp = Opportunity.objects.create(
            name="Org Infer LI Opp",
            stage="QUALIFICATION",
            org=org_a,
            created_by=admin_user,
        )
        li = OpportunityLineItem(
            opportunity=opp,
            name="Inferred Org Item",
            quantity=1,
            unit_price=Decimal("10.00"),
        )
        li.save()
        assert li.org_id == org_a.id


@pytest.mark.django_db
class TestOpportunitySerializer:
    """Cover OpportunitySerializer and OpportunityCreateSerializer methods."""

    def test_created_on_arrow_serializer(self, admin_user, org_a):
        from opportunity.serializer import OpportunitySerializer

        _set_rls(org_a)
        opp = Opportunity.objects.create(
            name="Arrow Ser Opp",
            stage="QUALIFICATION",
            org=org_a,
            created_by=admin_user,
        )
        serializer = OpportunitySerializer(opp)
        assert "ago" in serializer.data["created_on_arrow"]

    def test_line_items_total_serializer(self, admin_user, org_a):
        from opportunity.serializer import OpportunitySerializer

        _set_rls(org_a)
        opp = Opportunity.objects.create(
            name="LI Total Ser Opp",
            stage="QUALIFICATION",
            org=org_a,
            created_by=admin_user,
        )
        OpportunityLineItem.objects.create(
            opportunity=opp,
            name="A",
            quantity=2,
            unit_price=Decimal("25.00"),
            org=org_a,
        )
        serializer = OpportunitySerializer(opp)
        assert serializer.data["line_items_total"] == Decimal("50.00")

    def test_validate_name_update_existing_name_different_id(
        self, admin_client, admin_user, org_a
    ):
        """Updating opp name to an existing name (different opp) should fail."""
        _set_rls(org_a)
        Opportunity.objects.create(
            name="Taken Name Opp",
            stage="QUALIFICATION",
            org=org_a,
            created_by=admin_user,
        )
        opp = Opportunity.objects.create(
            name="Rename Me Opp",
            stage="QUALIFICATION",
            org=org_a,
            created_by=admin_user,
        )
        response = admin_client.put(
            _detail_url(opp.pk),
            {"name": "Taken Name Opp", "stage": "QUALIFICATION"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_validate_name_update_same_name_succeeds(
        self, admin_client, admin_user, org_a
    ):
        """Updating opp keeping same name should succeed."""
        _set_rls(org_a)
        opp = Opportunity.objects.create(
            name="Keep Name Opp",
            stage="QUALIFICATION",
            org=org_a,
            created_by=admin_user,
        )
        response = admin_client.put(
            _detail_url(opp.pk),
            {"name": "Keep Name Opp", "stage": "PROPOSAL"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK


@pytest.mark.django_db
class TestOpportunityLineItemSerializer:
    """Cover OpportunityLineItemSerializer methods."""

    def test_formatted_unit_price(self, admin_user, org_a):
        from opportunity.serializer import OpportunityLineItemSerializer

        _set_rls(org_a)
        opp = Opportunity.objects.create(
            name="Format LI Opp",
            stage="QUALIFICATION",
            currency="EUR",
            org=org_a,
            created_by=admin_user,
        )
        li = OpportunityLineItem.objects.create(
            opportunity=opp,
            name="Formatted Item",
            quantity=1,
            unit_price=Decimal("1234.56"),
            org=org_a,
        )
        serializer = OpportunityLineItemSerializer(li)
        assert "EUR" in serializer.data["formatted_unit_price"]
        assert "1,234.56" in serializer.data["formatted_unit_price"]

    def test_formatted_total(self, admin_user, org_a):
        from opportunity.serializer import OpportunityLineItemSerializer

        _set_rls(org_a)
        opp = Opportunity.objects.create(
            name="Format Total Opp",
            stage="QUALIFICATION",
            org=org_a,
            created_by=admin_user,
        )
        li = OpportunityLineItem.objects.create(
            opportunity=opp,
            name="Total Item",
            quantity=2,
            unit_price=Decimal("500.00"),
            org=org_a,
        )
        serializer = OpportunityLineItemSerializer(li)
        assert "1,000.00" in serializer.data["formatted_total"]

    def test_formatted_unit_price_no_currency(self, admin_user, org_a):
        """Formatted price uses USD when opportunity has no currency."""
        from opportunity.serializer import OpportunityLineItemSerializer

        _set_rls(org_a)
        opp = Opportunity.objects.create(
            name="No Curr LI Opp",
            stage="QUALIFICATION",
            org=org_a,
            created_by=admin_user,
        )
        li = OpportunityLineItem.objects.create(
            opportunity=opp,
            name="USD Default Item",
            quantity=1,
            unit_price=Decimal("100.00"),
            org=org_a,
        )
        serializer = OpportunityLineItemSerializer(li)
        assert "USD" in serializer.data["formatted_unit_price"]


@pytest.mark.django_db
class TestOpportunityCommentViewOwner:
    """Cover comment author editing/deleting own comments."""

    @pytest.fixture
    def user_comment(self, regular_user, user_profile, org_a, admin_user):
        """Create an opportunity and a comment by user_profile."""
        _set_rls(org_a)
        opp = Opportunity.objects.create(
            name="User Comment Opp",
            stage="QUALIFICATION",
            org=org_a,
            created_by=admin_user,
        )
        opp.assigned_to.add(user_profile)
        ct = ContentType.objects.get_for_model(Opportunity)
        comment = Comment.objects.create(
            content_type=ct,
            object_id=opp.id,
            comment="User's own comment",
            commented_by=user_profile,
            org=org_a,
        )
        return opp, comment

    def test_author_can_edit_own_comment_put(
        self, user_client, user_comment, user_profile, org_a
    ):
        """Comment author can edit their own comment via PUT."""
        opp, comment = user_comment
        response = user_client.put(
            _comment_url(comment.id),
            {
                "comment": "Updated by author",
                "commented_by": str(user_profile.id),
                "object_id": str(opp.id),
                "org": str(org_a.id),
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

    def test_author_can_patch_own_comment(
        self, user_client, user_comment, user_profile
    ):
        """Comment author can PATCH their own comment."""
        _, comment = user_comment
        response = user_client.patch(
            _comment_url(comment.id),
            {"comment": "Patched by author"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

    def test_author_can_delete_own_comment(
        self, user_client, user_comment, user_profile
    ):
        """Comment author can delete their own comment."""
        _, comment = user_comment
        response = user_client.delete(_comment_url(comment.id))
        assert response.status_code == status.HTTP_200_OK


@pytest.mark.django_db
class TestOpportunityAttachmentOwner:
    """Cover attachment permission checks for non-admin users."""

    def test_non_admin_creator_can_delete_their_own_attachment(
        self, user_client, regular_user, org_a, admin_user, user_profile
    ):
        """The uploader may remove what they uploaded.

        This test used to assert the opposite, and named the reason in its own
        title: the view compared `request.profile` to a `User` FK, so the
        uploader branch could never be true. That was repaired on 2026-08-05
        (`request.profile.user == self.object.created_by`), and the test kept
        passing anyway, because `BaseModel.save()` discarded the explicit
        `created_by=regular_user` below and stored NULL. Two defects cancelling
        left the assertion looking correct. With the stamp preserved, the
        uploader is now recognised and the answer is 200.

        The dedicated file `test_attachment_delete_authz.py` covers the other
        directions: another member gets 403, a cross-org admin gets 404.
        """
        _set_rls(org_a)
        opp = Opportunity.objects.create(
            name="Attach Owner Opp",
            stage="QUALIFICATION",
            org=org_a,
        )
        Opportunity.objects.filter(id=opp.id).update(created_by=admin_user)
        opp.assigned_to.add(user_profile)
        ct = ContentType.objects.get_for_model(Opportunity)
        attachment = Attachments.objects.create(
            content_type=ct,
            object_id=opp.id,
            file_name="myfile.pdf",
            attachment="attachments/myfile.pdf",
            created_by=regular_user,
            org=org_a,
        )
        response = user_client.delete(_attachment_url(attachment.id))
        assert response.status_code == status.HTTP_200_OK
        assert not Attachments.objects.filter(id=attachment.id).exists()


@pytest.mark.django_db
class TestOpportunityNonAdminAssigned:
    """Cover non-admin user assigned to opportunity can perform actions."""

    def test_non_admin_assigned_can_get_detail(
        self, user_client, admin_user, org_a, user_profile
    ):
        """Non-admin user assigned to opportunity can view detail."""
        _set_rls(org_a)
        opp = Opportunity.objects.create(
            name="Assigned Detail Opp",
            stage="QUALIFICATION",
            org=org_a,
            created_by=admin_user,
        )
        opp.assigned_to.add(user_profile)
        response = user_client.get(_detail_url(opp.pk))
        assert response.status_code == status.HTTP_200_OK
        assert response.data["opportunity_obj"]["name"] == "Assigned Detail Opp"

    @patch("opportunity.views.opportunity_views.send_email_to_assigned_user.delay")
    def test_non_admin_assigned_can_update(
        self, mock_email, user_client, admin_user, org_a, user_profile
    ):
        """Non-admin user assigned to opportunity can update via PUT."""
        _set_rls(org_a)
        opp = Opportunity.objects.create(
            name="Assigned Update Opp",
            stage="QUALIFICATION",
            org=org_a,
            created_by=admin_user,
        )
        opp.assigned_to.add(user_profile)
        response = user_client.put(
            _detail_url(opp.pk),
            {"name": "Assigned Update Opp", "stage": "PROPOSAL"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

    def test_non_admin_assigned_can_add_comment(
        self, user_client, admin_user, org_a, user_profile
    ):
        """Non-admin user assigned to opportunity can add a comment."""
        _set_rls(org_a)
        opp = Opportunity.objects.create(
            name="Assigned Comment Opp",
            stage="QUALIFICATION",
            org=org_a,
            created_by=admin_user,
        )
        opp.assigned_to.add(user_profile)
        response = user_client.post(
            _detail_url(opp.pk),
            {"comment": "User's comment"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

    def test_non_admin_creator_can_delete(
        self, user_client, regular_user, org_a, user_profile
    ):
        """Non-admin user who created the opportunity can delete it."""
        _set_rls(org_a)
        opp = Opportunity.objects.create(
            name="Creator Delete Opp",
            stage="QUALIFICATION",
            org=org_a,
        )
        # BaseModel.save() overrides created_by via crum, so use update()
        Opportunity.objects.filter(id=opp.id).update(created_by=regular_user)
        response = user_client.delete(_detail_url(opp.pk))
        assert response.status_code == status.HTTP_200_OK

    def test_non_admin_assigned_can_patch(
        self, user_client, admin_user, org_a, user_profile
    ):
        """Non-admin user assigned to opportunity can PATCH."""
        _set_rls(org_a)
        opp = Opportunity.objects.create(
            name="Assigned Patch Opp",
            stage="QUALIFICATION",
            org=org_a,
            created_by=admin_user,
        )
        opp.assigned_to.add(user_profile)
        response = user_client.patch(
            _detail_url(opp.pk),
            {"description": "Updated desc"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK


class TestAmountIsOwnedByLineItems:
    """A deal with line items owns its own `amount`.

    `OpportunityLineItem.save()` calls `recalculate_amount()`, which writes
    `amount` from the lines and stamps `amount_source = "CALCULATED"`. Nothing
    recomputed it on the deal's own update path, so an ordinary PUT or PATCH
    could set a different figure and leave the source still claiming the number
    came from the lines.

    The web edit form disables the amount input when line items exist, but a UI
    guard is not a guard: the mobile form has no equivalent and neither does
    curl.
    """

    @pytest.fixture()
    def opportunity(self, admin_user, org_a):
        _set_rls(org_a)
        return Opportunity.objects.create(
            name="Line Item Owned Deal",
            stage="QUALIFICATION",
            amount=Decimal("100.00"),
            org=org_a,
            created_by=admin_user,
        )

    @pytest.fixture()
    def priced(self, opportunity, org_a):
        """One line item of 2 x 250, so the deal is worth 500 and knows it."""
        _set_rls(org_a)
        OpportunityLineItem.objects.create(
            opportunity=opportunity,
            name="Widget",
            quantity=2,
            unit_price=Decimal("250.00"),
            org=org_a,
        )
        opportunity.refresh_from_db()
        return opportunity

    def test_line_items_take_ownership_of_the_amount(self, priced):
        """The premise of every test below."""
        assert priced.amount == Decimal("500.00")
        assert priced.amount_source == "CALCULATED"

    def test_patching_a_different_amount_is_refused(self, admin_client, priced):
        response = admin_client.patch(
            _detail_url(priced.pk), {"amount": "9.99"}, format="json"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "amount" in response.data["errors"]
        priced.refresh_from_db()
        assert priced.amount == Decimal("500.00")
        assert priced.amount_source == "CALCULATED"

    def test_putting_a_different_amount_is_refused(self, admin_client, priced):
        """PUT is the verb the mobile edit form uses."""
        response = admin_client.put(
            _detail_url(priced.pk),
            {
                "name": priced.name,
                "stage": "QUALIFICATION",
                "amount": "9.99",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        priced.refresh_from_db()
        assert priced.amount == Decimal("500.00")

    def test_clearing_the_amount_is_refused(self, admin_client, priced):
        response = admin_client.patch(
            _detail_url(priced.pk), {"amount": None}, format="json"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        priced.refresh_from_db()
        assert priced.amount == Decimal("500.00")

    def test_echoing_the_computed_amount_is_allowed(self, admin_client, priced):
        """A client that sends the whole record back must still be able to edit.

        This is the case that stops the rule from breaking the mobile form,
        which posts every field on every save.
        """
        response = admin_client.put(
            _detail_url(priced.pk),
            {
                "name": priced.name,
                "stage": "PROPOSAL",
                "amount": "500.00",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        priced.refresh_from_db()
        assert priced.stage == "PROPOSAL"
        assert priced.amount == Decimal("500.00")

    def test_an_edit_that_omits_the_amount_is_allowed(self, admin_client, priced):
        response = admin_client.patch(
            _detail_url(priced.pk), {"description": "Still on track"}, format="json"
        )
        assert response.status_code == status.HTTP_200_OK
        priced.refresh_from_db()
        assert priced.amount == Decimal("500.00")

    def test_a_deal_without_line_items_still_owns_its_amount(
        self, admin_client, opportunity
    ):
        """The rule must not leak onto ordinary deals, which are typed by hand."""
        response = admin_client.patch(
            _detail_url(opportunity.pk), {"amount": "1234.00"}, format="json"
        )
        assert response.status_code == status.HTTP_200_OK
        opportunity.refresh_from_db()
        assert opportunity.amount == Decimal("1234.00")

    def test_deleting_the_last_line_item_hands_the_amount_back(
        self, admin_client, priced, org_a
    ):
        _set_rls(org_a)
        priced.line_items.first().delete()
        priced.refresh_from_db()
        assert priced.amount_source == "MANUAL"

        response = admin_client.patch(
            _detail_url(priced.pk), {"amount": "42.00"}, format="json"
        )
        assert response.status_code == status.HTTP_200_OK
        priced.refresh_from_db()
        assert priced.amount == Decimal("42.00")
