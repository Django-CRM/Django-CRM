"""
Tests for the Case list and detail API views.

Run with: pytest cases/tests/test_cases_api.py -v
"""

import json
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework import status

from accounts.models import Account
from cases.models import Case, CasePipeline, CaseStage, Solution
from common.models import Attachments, Comment, Tags, Teams
from contacts.models import Contact

CASES_LIST_URL = "/api/cases/"


def _detail_url(pk):
    return f"/api/cases/{pk}/"


@pytest.mark.django_db
class TestCaseListView:
    """Tests for GET /api/cases/ and POST /api/cases/"""

    def test_list_cases(self, admin_client, case_a):
        response = admin_client.get(CASES_LIST_URL)
        assert response.status_code == status.HTTP_200_OK
        assert "cases" in response.data
        assert response.data["cases_count"] >= 1

    def test_list_excludes_soft_deleted_by_default(self, admin_client, case_a):
        case_a.is_active = False
        case_a.save()
        response = admin_client.get(CASES_LIST_URL)
        assert response.status_code == status.HTTP_200_OK
        ids = [c["id"] for c in response.json().get("cases", [])]
        assert str(case_a.id) not in ids

    def test_list_includes_soft_deleted_when_admin_passes_flag(
        self, admin_client, case_a
    ):
        case_a.is_active = False
        case_a.save()
        response = admin_client.get(CASES_LIST_URL + "?include_deleted=true")
        assert response.status_code == status.HTTP_200_OK
        ids = [c["id"] for c in response.json().get("cases", [])]
        assert str(case_a.id) in ids

    @patch("cases.views.send_email_to_assigned_user")
    def test_create_case(self, mock_email, admin_client):
        response = admin_client.post(
            CASES_LIST_URL,
            {
                "name": "New support ticket",
                "status": "New",
                "priority": "Normal",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["error"] is False
        assert "id" in response.data

    def test_create_case_unauthenticated(self, unauthenticated_client):
        """Asserted `pytest.raises(PermissionDenied)`, which can never hold
        through an APIClient, DRF renders an APIException as a response
        rather than letting it escape, so this had always failed. It now
        checks the response the client actually receives."""
        response = unauthenticated_client.post(
            CASES_LIST_URL,
            {
                "name": "Should fail",
                "status": "New",
                "priority": "Normal",
            },
            format="json",
        )
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )
        assert not Case.objects.filter(name="Should fail").exists()

    def test_org_isolation(self, org_b_client, case_a):
        """org_b_client must not see cases belonging to org_a."""
        response = org_b_client.get(CASES_LIST_URL)
        assert response.status_code == status.HTTP_200_OK
        case_ids = [c["id"] for c in response.data["cases"]]
        assert str(case_a.id) not in case_ids

    @patch("cases.views.send_email_to_assigned_user")
    def test_create_case_invalid(self, mock_email, admin_client):
        """POST without the required 'name' field should return 400."""
        response = admin_client.post(
            CASES_LIST_URL,
            {
                "status": "New",
                "priority": "Normal",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"] is True

    @patch("cases.views.send_email_to_assigned_user")
    def test_create_case_with_all_fields(
        self, mock_email, admin_client, admin_profile, admin_user, org_a
    ):
        """Create a case with contacts, teams, assigned_to, tags, and case_type."""
        contact = Contact.objects.create(
            first_name="Test",
            last_name="Contact",
            org=org_a,
            created_by=admin_user,
        )
        team = Teams.objects.create(
            name="Support Team", org=org_a, created_by=admin_user
        )
        tag = Tags.objects.create(name="critical", slug="critical", org=org_a)
        response = admin_client.post(
            CASES_LIST_URL,
            {
                "name": "Full Case",
                "status": "New",
                "priority": "Urgent",
                "case_type": "Incident",
                "description": "Full description",
                "contacts": [str(contact.id)],
                "teams": [str(team.id)],
                "assigned_to": [str(admin_profile.id)],
                "tags": [str(tag.id)],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["error"] is False
        case = Case.objects.get(name="Full Case")
        assert case.contacts.count() == 1
        assert case.teams.count() == 1
        assert case.assigned_to.count() == 1
        assert case.tags.count() == 1
        assert case.case_type == "Incident"

    @patch("cases.views.send_email_to_assigned_user")
    def test_create_case_duplicate_name_returns_400(
        self, mock_email, admin_client, case_a
    ):
        """Creating a case with a duplicate name in the same org should fail."""
        response = admin_client.post(
            CASES_LIST_URL,
            {
                "name": "Bug in login page",
                "status": "New",
                "priority": "Normal",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"] is True

    def test_list_cases_filter_by_status(self, admin_client, admin_user, org_a):
        """Filter cases by status query param."""
        Case.objects.create(
            name="New Case",
            status="New",
            priority="Normal",
            org=org_a,
            created_by=admin_user,
        )
        Case.objects.create(
            name="Closed Case",
            status="Closed",
            priority="Normal",
            closed_on="2026-01-01",
            org=org_a,
            created_by=admin_user,
        )
        response = admin_client.get(f"{CASES_LIST_URL}?status=New")
        assert response.status_code == status.HTTP_200_OK
        names = [c["name"] for c in response.data["cases"]]
        assert "New Case" in names
        assert "Closed Case" not in names

    def test_list_cases_filter_by_priority(self, admin_client, admin_user, org_a):
        """Filter cases by priority query param."""
        Case.objects.create(
            name="Urgent Case",
            status="New",
            priority="Urgent",
            org=org_a,
            created_by=admin_user,
        )
        Case.objects.create(
            name="Low Case",
            status="New",
            priority="Low",
            org=org_a,
            created_by=admin_user,
        )
        response = admin_client.get(f"{CASES_LIST_URL}?priority=Urgent")
        assert response.status_code == status.HTTP_200_OK
        names = [c["name"] for c in response.data["cases"]]
        assert "Urgent Case" in names
        assert "Low Case" not in names

    def test_list_cases_filter_by_case_type(self, admin_client, admin_user, org_a):
        """Filter cases by case_type query param."""
        Case.objects.create(
            name="Question Case",
            status="New",
            priority="Normal",
            case_type="Question",
            org=org_a,
            created_by=admin_user,
        )
        Case.objects.create(
            name="Incident Case",
            status="New",
            priority="Normal",
            case_type="Incident",
            org=org_a,
            created_by=admin_user,
        )
        response = admin_client.get(f"{CASES_LIST_URL}?case_type=Question")
        assert response.status_code == status.HTTP_200_OK
        names = [c["name"] for c in response.data["cases"]]
        assert "Question Case" in names
        assert "Incident Case" not in names

    def test_list_cases_filter_by_search(self, admin_client, admin_user, org_a):
        """Search filter should match case name."""
        Case.objects.create(
            name="Login Bug",
            status="New",
            priority="Normal",
            org=org_a,
            created_by=admin_user,
        )
        Case.objects.create(
            name="Payment Error",
            status="New",
            priority="Normal",
            org=org_a,
            created_by=admin_user,
        )
        response = admin_client.get(f"{CASES_LIST_URL}?search=Login")
        assert response.status_code == status.HTTP_200_OK
        names = [c["name"] for c in response.data["cases"]]
        assert "Login Bug" in names
        assert "Payment Error" not in names

    def test_list_cases_filter_by_name(self, admin_client, admin_user, org_a):
        """Name filter should filter by name."""
        Case.objects.create(
            name="Alpha Case",
            status="New",
            priority="Normal",
            org=org_a,
            created_by=admin_user,
        )
        Case.objects.create(
            name="Beta Case",
            status="New",
            priority="Normal",
            org=org_a,
            created_by=admin_user,
        )
        response = admin_client.get(f"{CASES_LIST_URL}?name=Alpha")
        assert response.status_code == status.HTTP_200_OK
        names = [c["name"] for c in response.data["cases"]]
        assert "Alpha Case" in names
        assert "Beta Case" not in names

    def test_list_cases_filter_by_assigned_to(
        self, admin_client, admin_profile, admin_user, org_a
    ):
        """Filter cases by assigned_to query param."""
        case = Case.objects.create(
            name="Assigned Case",
            status="New",
            priority="Normal",
            org=org_a,
            created_by=admin_user,
        )
        case.assigned_to.add(admin_profile)
        Case.objects.create(
            name="Unassigned Case",
            status="New",
            priority="Normal",
            org=org_a,
            created_by=admin_user,
        )
        response = admin_client.get(f"{CASES_LIST_URL}?assigned_to={admin_profile.id}")
        assert response.status_code == status.HTTP_200_OK
        names = [c["name"] for c in response.data["cases"]]
        assert "Assigned Case" in names
        assert "Unassigned Case" not in names

    def test_list_cases_filter_by_tags(self, admin_client, admin_user, org_a):
        """Filter cases by tags query param."""
        tag = Tags.objects.create(name="bug", slug="bug", org=org_a)
        case = Case.objects.create(
            name="Tagged Case",
            status="New",
            priority="Normal",
            org=org_a,
            created_by=admin_user,
        )
        case.tags.add(tag)
        Case.objects.create(
            name="Untagged Case",
            status="New",
            priority="Normal",
            org=org_a,
            created_by=admin_user,
        )
        response = admin_client.get(f"{CASES_LIST_URL}?tags={tag.id}")
        assert response.status_code == status.HTTP_200_OK
        names = [c["name"] for c in response.data["cases"]]
        assert "Tagged Case" in names
        assert "Untagged Case" not in names

    def test_list_cases_non_admin_sees_only_assigned(
        self, user_client, regular_user, admin_user, org_a, user_profile
    ):
        """Non-admin user should only see cases they are assigned to.

        Note: ORM-created objects have created_by=None (crum middleware not
        active in tests), so only assigned_to filtering works for non-admin
        visibility. The created_by=regular_user set in ORM gets overridden
        by BaseModel.save() to None.
        """
        Case.objects.create(
            name="Admin Only Case",
            status="New",
            priority="Normal",
            org=org_a,
            created_by=admin_user,
        )
        assigned_case = Case.objects.create(
            name="User Assigned Case",
            status="New",
            priority="Normal",
            org=org_a,
            created_by=admin_user,
        )
        assigned_case.assigned_to.add(user_profile)
        response = user_client.get(CASES_LIST_URL)
        assert response.status_code == status.HTTP_200_OK
        names = [c["name"] for c in response.data["cases"]]
        assert "User Assigned Case" in names
        assert "Admin Only Case" not in names

    def test_list_cases_response_includes_metadata(
        self, admin_client, admin_user, org_a
    ):
        """Response should include status, priority, type_of_case, accounts_list, contacts_list."""
        Case.objects.create(
            name="Meta Case",
            status="New",
            priority="Normal",
            org=org_a,
            created_by=admin_user,
        )
        response = admin_client.get(CASES_LIST_URL)
        assert response.status_code == status.HTTP_200_OK
        assert "status" in response.data
        assert "priority" in response.data
        assert "type_of_case" in response.data
        assert "accounts_list" in response.data
        assert "contacts_list" in response.data


@pytest.mark.django_db
class TestCaseDetailView:
    """Tests for GET/PUT/DELETE/POST /api/cases/<pk>/"""

    def test_get_detail(self, admin_client, case_a):
        response = admin_client.get(_detail_url(case_a.pk))
        assert response.status_code == status.HTTP_200_OK
        assert response.data["cases_obj"]["id"] == str(case_a.id)

    @patch("cases.views.send_email_to_assigned_user")
    def test_update_case(self, mock_email, admin_client, case_a):
        response = admin_client.put(
            _detail_url(case_a.pk),
            {
                "name": "Updated case name",
                "status": "Assigned",
                "priority": "Urgent",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["error"] is False
        case_a.refresh_from_db()
        assert case_a.name == "Updated case name"

    def test_delete_case(self, admin_client, case_a):
        response = admin_client.delete(_detail_url(case_a.pk))
        assert response.status_code == status.HTTP_200_OK
        assert response.data["error"] is False
        assert not Case.objects.filter(pk=case_a.pk).exists()

    def test_cross_org_detail_returns_404(self, org_b_client, case_a):
        """A client from org_b cannot retrieve a case from org_a."""
        response = org_b_client.get(_detail_url(case_a.pk))
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_add_comment(self, admin_client, case_a):
        response = admin_client.post(
            _detail_url(case_a.pk),
            {"comment": "This is a test comment."},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert "comments" in response.data

    @patch("cases.views.send_email_to_assigned_user")
    def test_update_case_with_tags_and_assigned_to(
        self, mock_email, admin_client, admin_profile, case_a, org_a
    ):
        """PUT should clear and re-set tags and assigned_to."""
        tag1 = Tags.objects.create(name="tag1", slug="tag1", org=org_a)
        tag2 = Tags.objects.create(name="tag2", slug="tag2", org=org_a)
        case_a.tags.add(tag1)
        case_a.assigned_to.add(admin_profile)
        response = admin_client.put(
            _detail_url(case_a.pk),
            {
                "name": "Bug in login page",
                "status": "New",
                "priority": "High",
                "tags": [str(tag2.id)],
                "assigned_to": [str(admin_profile.id)],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        case_a.refresh_from_db()
        tag_ids = list(case_a.tags.values_list("id", flat=True))
        assert tag2.id in tag_ids
        assert tag1.id not in tag_ids

    @patch("cases.views.send_email_to_assigned_user")
    def test_update_case_with_contacts_and_teams(
        self, mock_email, admin_client, admin_user, case_a, org_a
    ):
        """PUT should set contacts and teams."""
        contact = Contact.objects.create(
            first_name="Test",
            last_name="Contact",
            org=org_a,
            created_by=admin_user,
        )
        team = Teams.objects.create(name="Support", org=org_a, created_by=admin_user)
        response = admin_client.put(
            _detail_url(case_a.pk),
            {
                "name": "Bug in login page",
                "status": "New",
                "priority": "High",
                "contacts": [str(contact.id)],
                "teams": [str(team.id)],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        case_a.refresh_from_db()
        assert case_a.contacts.count() == 1
        assert case_a.teams.count() == 1

    @patch("cases.views.send_email_to_assigned_user")
    def test_update_case_clears_m2m(
        self, mock_email, admin_client, admin_profile, admin_user, case_a, org_a
    ):
        """PUT without M2M fields should clear them."""
        contact = Contact.objects.create(
            first_name="C", last_name="D", org=org_a, created_by=admin_user
        )
        case_a.contacts.add(contact)
        case_a.assigned_to.add(admin_profile)
        response = admin_client.put(
            _detail_url(case_a.pk),
            {
                "name": "Bug in login page",
                "status": "New",
                "priority": "High",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        case_a.refresh_from_db()
        assert case_a.contacts.count() == 0
        assert case_a.assigned_to.count() == 0
        assert case_a.tags.count() == 0

    @patch("cases.views.send_email_to_assigned_user")
    def test_update_case_invalid_returns_400(self, mock_email, admin_client, case_a):
        """PUT with invalid data should return 400."""
        response = admin_client.put(
            _detail_url(case_a.pk),
            {"status": "New", "priority": "Normal"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_update_case_non_admin_forbidden(self, user_client, case_a):
        """Non-admin who is not creator/assignee should get 403."""
        response = user_client.put(
            _detail_url(case_a.pk),
            {"name": "Hacked", "status": "New", "priority": "Normal"},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_delete_case_non_admin_not_creator(self, user_client, case_a):
        """Non-admin who is not creator should get 403 on delete."""
        response = user_client.delete(_detail_url(case_a.pk))
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_get_detail_non_admin_forbidden(self, user_client, case_a):
        """Non-admin who is not creator/assignee/watcher should get 403.

        The status is unchanged; the body is now DRF's standard
        `{"detail": ...}` because the view raises `PermissionDenied` from the
        one rule in `cases.access` instead of hand-building an envelope in
        five places. Same shape as leads, opportunity, accounts and contacts.
        """
        response = user_client.get(_detail_url(case_a.pk))
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert "Permission" in response.data["detail"]

    def test_get_detail_non_admin_as_assignee(
        self, user_client, admin_user, user_profile, org_a
    ):
        """Non-admin who is assigned to the case should be able to see it.

        Note: ORM-created objects have created_by=None (crum middleware not
        active in tests), so creator-based permission doesn't work.
        Testing with assigned_to instead.
        """
        case = Case.objects.create(
            name="User Case",
            status="New",
            priority="Normal",
            org=org_a,
            created_by=admin_user,
        )
        case.assigned_to.add(user_profile)
        response = user_client.get(_detail_url(case.pk))
        assert response.status_code == status.HTTP_200_OK
        assert response.data["cases_obj"]["name"] == "User Case"

    def test_get_detail_response_fields(self, admin_client, case_a):
        """GET detail should return expected response fields."""
        response = admin_client.get(_detail_url(case_a.pk))
        assert response.status_code == status.HTTP_200_OK
        assert "cases_obj" in response.data
        assert "attachments" in response.data
        assert "comments" in response.data
        assert "comment_permission" in response.data
        assert "users_mention" in response.data
        assert "contacts" in response.data
        assert "status" in response.data
        assert "priority" in response.data

    def test_patch_case_partial_update(self, admin_client, case_a):
        """PATCH should allow partial updates."""
        response = admin_client.patch(
            _detail_url(case_a.pk),
            {"status": "Assigned"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["error"] is False
        case_a.refresh_from_db()
        assert case_a.status == "Assigned"
        assert case_a.name == "Bug in login page"  # Unchanged

    def test_patch_case_with_m2m_fields(
        self, admin_client, admin_profile, case_a, org_a
    ):
        """PATCH should handle M2M fields when present."""
        tag = Tags.objects.create(name="patchtag", slug="patchtag", org=org_a)
        response = admin_client.patch(
            _detail_url(case_a.pk),
            {
                "tags": [str(tag.id)],
                "assigned_to": [str(admin_profile.id)],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        case_a.refresh_from_db()
        assert case_a.tags.count() == 1
        assert case_a.assigned_to.count() == 1

    def test_patch_case_clear_m2m(self, admin_client, admin_profile, case_a):
        """PATCH with empty list for M2M should clear them."""
        case_a.assigned_to.add(admin_profile)
        response = admin_client.patch(
            _detail_url(case_a.pk),
            {"assigned_to": [], "tags": []},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        case_a.refresh_from_db()
        assert case_a.assigned_to.count() == 0
        assert case_a.tags.count() == 0

    def test_patch_case_non_admin_forbidden(self, user_client, case_a):
        """Non-admin who is not creator/assignee should get 403 on PATCH."""
        response = user_client.patch(
            _detail_url(case_a.pk),
            {"status": "Assigned"},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_post_comment_non_admin_forbidden(self, user_client, case_a):
        """Non-admin who is not creator/assignee should get 403 on POST (comment)."""
        response = user_client.post(
            _detail_url(case_a.pk),
            {"comment": "Should not work"},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_post_comment_non_admin_as_assignee(
        self, user_client, admin_user, user_profile, org_a
    ):
        """Non-admin who is assigned should be able to post comments."""
        case = Case.objects.create(
            name="Assignee Comment Case",
            status="New",
            priority="Normal",
            org=org_a,
            created_by=admin_user,
        )
        case.assigned_to.add(user_profile)
        response = user_client.post(
            _detail_url(case.pk),
            {"comment": "Assignee comment"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert "comments" in response.data


@pytest.mark.django_db
class TestCaseCommentView:
    """Tests for PUT/PATCH/DELETE /api/cases/comment/<pk>/."""

    def _create_case_with_comment(self, admin_user, admin_profile, org_a):
        """Helper to create a case with a comment."""
        case = Case.objects.create(
            name="Comment Test Case",
            status="New",
            priority="Normal",
            org=org_a,
            created_by=admin_user,
        )
        ct = ContentType.objects.get_for_model(Case)
        comment = Comment.objects.create(
            content_type=ct,
            object_id=case.id,
            comment="Original comment",
            commented_by=admin_profile,
            org=org_a,
        )
        return case, comment

    def test_update_comment_put_edits_the_text(
        self, admin_client, admin_user, admin_profile, org_a
    ):
        """PUT with only `comment` edits the comment.

        This used to assert a 400 and describe it as correct: the serializer
        ran non-partial with `object_id` and `org` as required fields, so an
        ordinary "fix my typo" edit failed validation. Those two are the
        server's to set, are now read-only, and no longer stand between an
        author and their own text.
        """
        _case, comment = self._create_case_with_comment(
            admin_user, admin_profile, org_a
        )
        response = admin_client.put(
            f"/api/cases/comment/{comment.id}/",
            {"comment": "Updated comment text"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        comment.refresh_from_db()
        assert comment.comment == "Updated comment text"

    def test_update_comment_patch(self, admin_client, admin_user, admin_profile, org_a):
        """Admin should be able to partially update via PATCH."""
        _case, comment = self._create_case_with_comment(
            admin_user, admin_profile, org_a
        )
        response = admin_client.patch(
            f"/api/cases/comment/{comment.id}/",
            {"comment": "Patched comment"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["error"] is False
        assert response.json()["message"] == "Comment Updated"

    def test_delete_comment(self, admin_client, admin_user, admin_profile, org_a):
        """Admin should be able to delete a comment."""
        _case, comment = self._create_case_with_comment(
            admin_user, admin_profile, org_a
        )
        response = admin_client.delete(f"/api/cases/comment/{comment.id}/")
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["error"] is False
        assert not Comment.objects.filter(id=comment.id).exists()

    def test_update_comment_non_admin_forbidden(
        self, user_client, admin_user, admin_profile, org_a
    ):
        """Non-admin who did not create comment should get 403."""
        _case, comment = self._create_case_with_comment(
            admin_user, admin_profile, org_a
        )
        response = user_client.put(
            f"/api/cases/comment/{comment.id}/",
            {"comment": "Should fail"},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_delete_comment_non_admin_forbidden(
        self, user_client, admin_user, admin_profile, org_a
    ):
        """Non-admin who did not create the comment should get 403."""
        _case, comment = self._create_case_with_comment(
            admin_user, admin_profile, org_a
        )
        response = user_client.delete(f"/api/cases/comment/{comment.id}/")
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_put_comment_without_comment_field_is_400_not_403(
        self, admin_client, admin_user, admin_profile, org_a
    ):
        """A missing `comment` is a bad request, not a permission problem.

        This asserted 403 and called it correct. The `if params.get("comment")`
        guard sat inside the authorization branch, so a body the serializer
        would have rejected fell out of the branch entirely and hit the 403 at
        the bottom of the handler: the caller was told they may not edit their
        own comment, which is both wrong and unactionable.
        """
        _case, comment = self._create_case_with_comment(
            admin_user, admin_profile, org_a
        )
        response = admin_client.put(
            f"/api/cases/comment/{comment.id}/",
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "comment" in response.json()["errors"]

    def test_put_blank_comment_is_400(
        self, admin_client, admin_user, admin_profile, org_a
    ):
        """The other half of the same guard: `""` is falsy too."""
        _case, comment = self._create_case_with_comment(
            admin_user, admin_profile, org_a
        )
        response = admin_client.put(
            f"/api/cases/comment/{comment.id}/",
            {"comment": ""},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        comment.refresh_from_db()
        assert comment.comment == "Original comment"

    def test_a_stranger_still_gets_403_for_an_empty_body(
        self, user_client, admin_user, admin_profile, org_a
    ):
        """Authorization is still checked first, and still wins.

        The fix moved a validation failure out of the 403 branch. It must not
        have moved the authorization check with it: someone who may not touch
        this comment learns nothing about whether their body was well formed.
        """
        _case, comment = self._create_case_with_comment(
            admin_user, admin_profile, org_a
        )
        response = user_client.put(
            f"/api/cases/comment/{comment.id}/",
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
class TestCaseAttachmentView:
    """Tests for DELETE /api/cases/attachment/<pk>/."""

    def test_delete_attachment(self, admin_client, admin_user, org_a):
        """Admin should be able to delete a case attachment."""
        from django.core.files.uploadedfile import SimpleUploadedFile

        case = Case.objects.create(
            name="Attachment Case",
            status="New",
            priority="Normal",
            org=org_a,
            created_by=admin_user,
        )
        ct = ContentType.objects.get_for_model(Case)
        attachment = Attachments.objects.create(
            content_type=ct,
            object_id=case.id,
            file_name="test.txt",
            attachment=SimpleUploadedFile("test.txt", b"file content"),
            created_by=admin_user,
            org=org_a,
        )
        response = admin_client.delete(f"/api/cases/attachment/{attachment.id}/")
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["error"] is False
        assert not Attachments.objects.filter(id=attachment.id).exists()

    def test_delete_attachment_non_admin_forbidden(
        self, user_client, admin_user, org_a
    ):
        """Non-admin who did not create attachment should get 403."""
        from django.core.files.uploadedfile import SimpleUploadedFile

        case = Case.objects.create(
            name="Att Deny Case",
            status="New",
            priority="Normal",
            org=org_a,
            created_by=admin_user,
        )
        ct = ContentType.objects.get_for_model(Case)
        attachment = Attachments.objects.create(
            content_type=ct,
            object_id=case.id,
            file_name="test.txt",
            attachment=SimpleUploadedFile("test.txt", b"data"),
            created_by=admin_user,
            org=org_a,
        )
        response = user_client.delete(f"/api/cases/attachment/{attachment.id}/")
        assert response.status_code == status.HTTP_403_FORBIDDEN


# ---------------------------------------------------------------------------
# Additional coverage tests for uncovered lines in cases/views.py
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCaseListFiltersCoverage:
    """Tests covering filter parameters that were previously uncovered
    (lines 84-91, account filter).
    """

    def test_filter_by_created_at_gte(self, admin_client, admin_user, org_a):
        """Filter cases by created_at__gte."""
        Case.objects.create(
            name="GTE Case",
            status="New",
            priority="Normal",
            org=org_a,
            created_by=admin_user,
        )
        response = admin_client.get(f"{CASES_LIST_URL}?created_at__gte=2020-01-01")
        assert response.status_code == status.HTTP_200_OK
        names = [c["name"] for c in response.data["cases"]]
        assert "GTE Case" in names

    def test_filter_by_created_at_lte(self, admin_client, admin_user, org_a):
        """Filter cases by created_at__lte."""
        Case.objects.create(
            name="LTE Case",
            status="New",
            priority="Normal",
            org=org_a,
            created_by=admin_user,
        )
        response = admin_client.get(f"{CASES_LIST_URL}?created_at__lte=2099-12-31")
        assert response.status_code == status.HTTP_200_OK
        names = [c["name"] for c in response.data["cases"]]
        assert "LTE Case" in names

    def test_filter_by_created_at_range(self, admin_client, admin_user, org_a):
        """Filter by created_at__gte and created_at__lte together."""
        Case.objects.create(
            name="Range Case",
            status="New",
            priority="Normal",
            org=org_a,
            created_by=admin_user,
        )
        response = admin_client.get(
            f"{CASES_LIST_URL}?created_at__gte=2020-01-01&created_at__lte=2099-12-31"
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["cases_count"] >= 1

    def test_filter_by_account(self, admin_client, admin_user, org_a):
        """Filter cases by account query param."""
        account = Account.objects.create(
            name="Filter Account", org=org_a, created_by=admin_user
        )
        Case.objects.create(
            name="Account Case",
            status="New",
            priority="Normal",
            account=account,
            org=org_a,
            created_by=admin_user,
        )
        Case.objects.create(
            name="No Account Case",
            status="New",
            priority="Normal",
            org=org_a,
            created_by=admin_user,
        )
        response = admin_client.get(f"{CASES_LIST_URL}?account={account.id}")
        assert response.status_code == status.HTTP_200_OK
        names = [c["name"] for c in response.data["cases"]]
        assert "Account Case" in names
        assert "No Account Case" not in names


@pytest.mark.django_db
class TestCaseCreateCoverage:
    """Tests for case creation covering previously uncovered lines
    (contacts as JSON string, teams as JSON string, attachment upload, etc.).
    """

    @patch("cases.views.send_email_to_assigned_user")
    def test_create_case_with_contacts_as_json_string(
        self, mock_email, admin_client, admin_user, org_a
    ):
        """Create case with contacts as JSON string (covers line 173)."""
        contact = Contact.objects.create(
            first_name="JSONStr",
            last_name="Contact",
            org=org_a,
            created_by=admin_user,
        )
        response = admin_client.post(
            CASES_LIST_URL,
            {
                "name": "JSON Contact Case",
                "status": "New",
                "priority": "Normal",
                "contacts": json.dumps([str(contact.id)]),
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        case = Case.objects.get(name="JSON Contact Case")
        assert case.contacts.count() == 1

    @patch("cases.views.send_email_to_assigned_user")
    def test_create_case_with_teams_as_json_string(
        self, mock_email, admin_client, admin_user, org_a
    ):
        """Create case with teams as JSON string (covers line 188)."""
        team = Teams.objects.create(
            name="JSON Team Case", org=org_a, created_by=admin_user
        )
        response = admin_client.post(
            CASES_LIST_URL,
            {
                "name": "JSON Team Case",
                "status": "New",
                "priority": "Normal",
                "teams": json.dumps([str(team.id)]),
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        case = Case.objects.get(name="JSON Team Case")
        assert case.teams.count() == 1

    @patch("cases.views.send_email_to_assigned_user")
    def test_create_case_with_assigned_to_as_json_string(
        self, mock_email, admin_client, admin_profile, org_a
    ):
        """Create case with assigned_to as JSON string (covers line 201)."""
        response = admin_client.post(
            CASES_LIST_URL,
            {
                "name": "JSON Assigned Case",
                "status": "New",
                "priority": "Normal",
                "assigned_to": json.dumps([str(admin_profile.id)]),
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        case = Case.objects.get(name="JSON Assigned Case")
        assert case.assigned_to.count() == 1

    @patch("cases.views.send_email_to_assigned_user")
    def test_create_case_with_tags_as_json_string(
        self, mock_email, admin_client, admin_user, org_a
    ):
        """Create case with tags as JSON string (covers line 216)."""
        tag = Tags.objects.create(name="jsoncasetag", slug="jsoncasetag", org=org_a)
        response = admin_client.post(
            CASES_LIST_URL,
            {
                "name": "JSON Tag Case",
                "status": "New",
                "priority": "Normal",
                "tags": json.dumps([str(tag.id)]),
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        case = Case.objects.get(name="JSON Tag Case")
        assert case.tags.count() == 1

    @patch("cases.views.send_email_to_assigned_user")
    def test_create_case_with_attachment(
        self, mock_email, admin_client, admin_user, org_a
    ):
        """Create case with file attachment (covers lines 227-234)."""
        upload_file = SimpleUploadedFile(
            "case_attach.txt", b"case file content", content_type="text/plain"
        )
        response = admin_client.post(
            CASES_LIST_URL,
            {
                "name": "Attach Create Case",
                "status": "New",
                "priority": "Normal",
                "case_attachment": upload_file,
            },
            format="multipart",
        )
        assert response.status_code == status.HTTP_200_OK
        case = Case.objects.get(name="Attach Create Case")
        ct = ContentType.objects.get_for_model(Case)
        assert Attachments.objects.filter(content_type=ct, object_id=case.id).exists()


@pytest.mark.django_db
class TestCaseUpdateCoverage:
    """Tests for case PUT update covering previously uncovered lines
    (JSON string m2m, attachment upload on update, etc.).
    """

    @patch("cases.views.send_email_to_assigned_user")
    def test_update_case_with_contacts_as_json_string(
        self, mock_email, admin_client, admin_user, case_a, org_a
    ):
        """PUT with contacts as JSON string (covers line 318)."""
        contact = Contact.objects.create(
            first_name="UpdJSON",
            last_name="Contact",
            org=org_a,
            created_by=admin_user,
        )
        response = admin_client.put(
            _detail_url(case_a.pk),
            {
                "name": "Bug in login page",
                "status": "New",
                "priority": "High",
                "contacts": json.dumps([str(contact.id)]),
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        case_a.refresh_from_db()
        assert case_a.contacts.count() == 1

    @patch("cases.views.send_email_to_assigned_user")
    def test_update_case_with_teams_as_json_string(
        self, mock_email, admin_client, admin_user, case_a, org_a
    ):
        """PUT with teams as JSON string (covers line 334)."""
        team = Teams.objects.create(
            name="UpdJSON CaseTeam", org=org_a, created_by=admin_user
        )
        response = admin_client.put(
            _detail_url(case_a.pk),
            {
                "name": "Bug in login page",
                "status": "New",
                "priority": "High",
                "teams": json.dumps([str(team.id)]),
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        case_a.refresh_from_db()
        assert case_a.teams.count() == 1

    @patch("cases.views.send_email_to_assigned_user")
    def test_update_case_with_assigned_to_as_json_string(
        self, mock_email, admin_client, admin_profile, case_a, org_a
    ):
        """PUT with assigned_to as JSON string (covers line 348)."""
        response = admin_client.put(
            _detail_url(case_a.pk),
            {
                "name": "Bug in login page",
                "status": "New",
                "priority": "High",
                "assigned_to": json.dumps([str(admin_profile.id)]),
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        case_a.refresh_from_db()
        assert case_a.assigned_to.count() == 1

    @patch("cases.views.send_email_to_assigned_user")
    def test_update_case_with_tags_as_json_string(
        self, mock_email, admin_client, admin_user, case_a, org_a
    ):
        """PUT with tags as JSON string (covers line 364)."""
        tag = Tags.objects.create(
            name="updjsoncasetag", slug="updjsoncasetag", org=org_a
        )
        response = admin_client.put(
            _detail_url(case_a.pk),
            {
                "name": "Bug in login page",
                "status": "New",
                "priority": "High",
                "tags": json.dumps([str(tag.id)]),
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        case_a.refresh_from_db()
        assert case_a.tags.count() == 1

    @patch("cases.views.send_email_to_assigned_user")
    def test_update_case_with_attachment(
        self, mock_email, admin_client, admin_user, case_a, org_a
    ):
        """PUT with case_attachment file (covers lines 375-382)."""
        upload_file = SimpleUploadedFile(
            "update_attach.txt", b"updated content", content_type="text/plain"
        )
        response = admin_client.put(
            _detail_url(case_a.pk),
            {
                "name": "Bug in login page",
                "status": "New",
                "priority": "High",
                "case_attachment": upload_file,
            },
            format="multipart",
        )
        assert response.status_code == status.HTTP_200_OK
        ct = ContentType.objects.get_for_model(Case)
        assert Attachments.objects.filter(content_type=ct, object_id=case_a.id).exists()


@pytest.mark.django_db
class TestCasePatchCoverage:
    """Tests for case PATCH covering previously uncovered lines
    (contacts/teams/assigned_to/tags as JSON strings in patch).
    """

    def test_patch_with_contacts_as_json_string(
        self, admin_client, admin_user, case_a, org_a
    ):
        """PATCH with contacts as JSON string (covers line 671)."""
        contact = Contact.objects.create(
            first_name="PatchJSON",
            last_name="Contact",
            org=org_a,
            created_by=admin_user,
        )
        response = admin_client.patch(
            _detail_url(case_a.pk),
            {"contacts": json.dumps([str(contact.id)])},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        case_a.refresh_from_db()
        assert case_a.contacts.count() == 1

    def test_patch_with_teams_as_json_string(
        self, admin_client, admin_user, case_a, org_a
    ):
        """PATCH with teams as JSON string (covers line 687)."""
        team = Teams.objects.create(
            name="PatchJSON CaseTeam", org=org_a, created_by=admin_user
        )
        response = admin_client.patch(
            _detail_url(case_a.pk),
            {"teams": json.dumps([str(team.id)])},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        case_a.refresh_from_db()
        assert case_a.teams.count() == 1

    def test_patch_with_assigned_to_as_json_string(
        self, admin_client, admin_profile, case_a, org_a
    ):
        """PATCH with assigned_to as JSON string (covers line 703)."""
        response = admin_client.patch(
            _detail_url(case_a.pk),
            {"assigned_to": json.dumps([str(admin_profile.id)])},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        case_a.refresh_from_db()
        assert case_a.assigned_to.count() == 1

    def test_patch_with_tags_as_json_string(
        self, admin_client, admin_user, case_a, org_a
    ):
        """PATCH with tags as JSON string (covers line 719)."""
        tag = Tags.objects.create(
            name="patchjsoncasetag", slug="patchjsoncasetag", org=org_a
        )
        response = admin_client.patch(
            _detail_url(case_a.pk),
            {"tags": json.dumps([str(tag.id)])},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        case_a.refresh_from_db()
        assert case_a.tags.count() == 1

    def test_patch_invalid_returns_400(self, admin_client, case_a):
        """PATCH with invalid data returns 400 (covers line 734-736)."""
        response = admin_client.patch(
            _detail_url(case_a.pk),
            {"status": "NotAValidStatus"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"] is True

    def test_patch_with_contacts_and_teams_cleared(
        self, admin_client, admin_user, case_a, org_a
    ):
        """PATCH with empty contacts and teams clears them."""
        contact = Contact.objects.create(
            first_name="ToClear",
            last_name="Contact",
            org=org_a,
            created_by=admin_user,
        )
        team = Teams.objects.create(
            name="ToClear Team", org=org_a, created_by=admin_user
        )
        case_a.contacts.add(contact)
        case_a.teams.add(team)
        response = admin_client.patch(
            _detail_url(case_a.pk),
            {"contacts": [], "teams": []},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        case_a.refresh_from_db()
        assert case_a.contacts.count() == 0
        assert case_a.teams.count() == 0


@pytest.mark.django_db
class TestCaseDetailPostCoverage:
    """Tests for POST to case detail with attachment (lines 578-585)."""

    def test_post_attachment_to_case(self, admin_client, admin_user, org_a):
        """POST with case_attachment file (covers lines 578-585)."""
        case = Case.objects.create(
            name="Attach Post Case",
            status="New",
            priority="Normal",
            org=org_a,
            created_by=admin_user,
        )
        upload_file = SimpleUploadedFile(
            "case_post_attach.txt", b"attachment content", content_type="text/plain"
        )
        response = admin_client.post(
            _detail_url(case.pk),
            {"case_attachment": upload_file},
            format="multipart",
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "attachments" in data
        assert len(data["attachments"]) == 1

    def test_post_comment_and_attachment(self, admin_client, admin_user, org_a):
        """POST with both comment and attachment on case.

        Note: In multipart mode, the CommentSerializer validation fails because
        it requires additional fields (object_id, org, commented_by). So the
        comment is NOT saved, but the attachment IS saved.
        """
        case = Case.objects.create(
            name="Comment Attach Case",
            status="New",
            priority="Normal",
            org=org_a,
            created_by=admin_user,
        )
        upload_file = SimpleUploadedFile(
            "case_combo.pdf", b"pdf", content_type="application/pdf"
        )
        response = admin_client.post(
            _detail_url(case.pk),
            {"comment": "With attachment", "case_attachment": upload_file},
            format="multipart",
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        # Comment serializer validation fails in multipart mode, so no comment saved
        assert len(data["attachments"]) == 1


@pytest.mark.django_db
class TestCaseCommentPatchCoverage:
    """Additional tests for CaseCommentView PATCH edge cases."""

    def _create_case_with_comment(self, admin_user, admin_profile, org_a):
        case = Case.objects.create(
            name="Patch Comment Case",
            status="New",
            priority="Normal",
            org=org_a,
            created_by=admin_user,
        )
        ct = ContentType.objects.get_for_model(Case)
        comment = Comment.objects.create(
            content_type=ct,
            object_id=case.id,
            comment="Patch coverage comment",
            commented_by=admin_profile,
            org=org_a,
        )
        return case, comment

    def test_patch_comment_non_admin_forbidden(
        self, user_client, admin_user, admin_profile, org_a
    ):
        """Non-admin who didn't create comment gets 403 on PATCH (line 824-830)."""
        _case, comment = self._create_case_with_comment(
            admin_user, admin_profile, org_a
        )
        response = user_client.patch(
            f"/api/cases/comment/{comment.id}/",
            {"comment": "Patched by non-admin"},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_patch_comment_by_commenter(
        self, admin_client, admin_user, admin_profile, org_a
    ):
        """Commenter can PATCH their own comment."""
        _case, comment = self._create_case_with_comment(
            admin_user, admin_profile, org_a
        )
        response = admin_client.patch(
            f"/api/cases/comment/{comment.id}/",
            {"comment": "Patched by commenter"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["message"] == "Comment Updated"


# ---------------------------------------------------------------------------
# Tests for cases/models.py - uncovered model methods
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCaseModelMethods:
    """Tests for Case model methods and properties covering uncovered lines."""

    def test_str_method(self, admin_user, org_a):
        """Test __str__ returns the case name."""
        case = Case.objects.create(
            name="Str Test Case",
            status="New",
            priority="Normal",
            org=org_a,
            created_by=admin_user,
        )
        assert str(case) == "Str Test Case"

    def test_clean_closed_without_date_raises(self, admin_user, org_a):
        """Case.clean() raises ValidationError when status=Closed but no closed_on."""
        case = Case(
            name="Close No Date",
            status="Closed",
            priority="Normal",
            org=org_a,
        )
        with pytest.raises(ValidationError) as exc_info:
            case.clean()
        assert "closed_on" in exc_info.value.message_dict

    def test_clean_closed_with_date_valid(self, admin_user, org_a):
        """Case.clean() passes when status=Closed and closed_on is set."""
        case = Case(
            name="Close With Date",
            status="Closed",
            priority="Normal",
            closed_on="2026-01-01",
            org=org_a,
        )
        # Should not raise
        case.clean()

    def test_save_applies_priority_specific_sla_defaults(self, admin_user, org_a):
        """New case picks up the priority-specific SLA defaults.

        See cases/workflow.py: Urgent gets 1h first-response / 4h resolution.
        Resolution happens in `cases.signals.case_pre_save_sla_targets`, which
        consults the org's EscalationPolicy first; this org has none, so the
        defaults apply. Targets configured per org are covered in
        cases/tests/test_sla_targets.py.
        """
        case = Case.objects.create(
            name="Urgent SLA Case",
            status="New",
            priority="Urgent",
            org=org_a,
            created_by=admin_user,
        )
        assert case.sla_first_response_hours == 1
        assert case.sla_resolution_hours == 4

    def test_save_preserves_custom_sla(self, admin_user, org_a):
        """Custom SLA values (non-default) are preserved on save."""
        case = Case(
            name="Custom SLA Case",
            status="New",
            priority="High",
            org=org_a,
            sla_first_response_hours=10,
            sla_resolution_hours=50,
        )
        case.save()
        assert case.sla_first_response_hours == 10
        assert case.sla_resolution_hours == 50

    def test_is_sla_first_response_breached_when_responded(self, admin_user, org_a):
        """first_response_at is set, so SLA is not breached."""
        case = Case.objects.create(
            name="Responded Case",
            status="New",
            priority="Normal",
            org=org_a,
            created_by=admin_user,
            first_response_at=timezone.now(),
        )
        assert case.is_sla_first_response_breached is False

    def test_is_sla_first_response_breached_when_overdue(self, admin_user, org_a):
        """No first_response_at and past deadline => breached."""
        case = Case.objects.create(
            name="Overdue Response Case",
            status="New",
            priority="Urgent",
            org=org_a,
            created_by=admin_user,
        )
        # Artificially set created_at to the past
        Case.objects.filter(pk=case.pk).update(
            created_at=timezone.now() - timedelta(hours=100)
        )
        case.refresh_from_db()
        assert case.is_sla_first_response_breached is True

    def test_is_sla_first_response_not_breached_within_time(self, admin_user, org_a):
        """No first_response_at but within deadline => not breached."""
        case = Case.objects.create(
            name="Within Time Response Case",
            status="New",
            priority="Low",
            org=org_a,
            created_by=admin_user,
        )
        # Low priority has 24h first response SLA, just created, so not breached
        assert case.is_sla_first_response_breached is False

    def test_is_sla_resolution_breached_when_resolved(self, admin_user, org_a):
        """resolved_at is set, so SLA is not breached."""
        case = Case.objects.create(
            name="Resolved Case",
            status="New",
            priority="Normal",
            org=org_a,
            created_by=admin_user,
            resolved_at=timezone.now(),
        )
        assert case.is_sla_resolution_breached is False

    def test_is_sla_resolution_breached_when_overdue(self, admin_user, org_a):
        """No resolved_at and past deadline => breached."""
        case = Case.objects.create(
            name="Overdue Resolution Case",
            status="New",
            priority="Urgent",
            org=org_a,
            created_by=admin_user,
        )
        Case.objects.filter(pk=case.pk).update(
            created_at=timezone.now() - timedelta(hours=100)
        )
        case.refresh_from_db()
        assert case.is_sla_resolution_breached is True

    def test_is_sla_resolution_not_breached_within_time(self, admin_user, org_a):
        """No resolved_at but within deadline => not breached."""
        case = Case.objects.create(
            name="Within Time Resolution Case",
            status="New",
            priority="Low",
            org=org_a,
            created_by=admin_user,
        )
        assert case.is_sla_resolution_breached is False

    def test_first_response_sla_deadline(self, admin_user, org_a):
        """first_response_sla_deadline returns a datetime."""
        case = Case.objects.create(
            name="Deadline Case",
            status="New",
            priority="Normal",
            org=org_a,
            created_by=admin_user,
        )
        deadline = case.first_response_sla_deadline
        assert deadline is not None
        assert deadline > case.created_at

    def test_first_response_sla_deadline_none_without_created_at(self, org_a):
        """first_response_sla_deadline returns None if created_at is None."""
        case = Case(
            name="No Created At",
            status="New",
            priority="Normal",
            org=org_a,
        )
        # created_at is None before saving
        assert case.first_response_sla_deadline is None

    def test_resolution_sla_deadline(self, admin_user, org_a):
        """resolution_sla_deadline returns a datetime."""
        case = Case.objects.create(
            name="Resolution Deadline Case",
            status="New",
            priority="Normal",
            org=org_a,
            created_by=admin_user,
        )
        deadline = case.resolution_sla_deadline
        assert deadline is not None
        assert deadline > case.created_at

    def test_resolution_sla_deadline_none_without_created_at(self, org_a):
        """resolution_sla_deadline returns None if created_at is None."""
        case = Case(
            name="No Created At Resolution",
            status="New",
            priority="Normal",
            org=org_a,
        )
        assert case.resolution_sla_deadline is None


@pytest.mark.django_db
class TestSolutionModel:
    """Tests for the Solution model methods."""

    def test_str_method(self, admin_user, org_a):
        solution = Solution.objects.create(
            title="KB Article",
            description="Helpful article",
            org=org_a,
            created_by=admin_user,
        )
        assert str(solution) == "KB Article"

    def test_publish_when_approved(self, admin_user, org_a):
        """publish() sets is_published=True when status is approved."""
        solution = Solution.objects.create(
            title="Approved Solution",
            description="Ready",
            status="approved",
            org=org_a,
            created_by=admin_user,
        )
        solution.publish()
        solution.refresh_from_db()
        assert solution.is_published is True

    def test_publish_when_not_approved(self, admin_user, org_a):
        """publish() does nothing when status is not approved."""
        solution = Solution.objects.create(
            title="Draft Solution",
            description="Not ready",
            status="draft",
            org=org_a,
            created_by=admin_user,
        )
        solution.publish()
        solution.refresh_from_db()
        assert solution.is_published is False

    def test_unpublish(self, admin_user, org_a):
        """unpublish() sets is_published=False."""
        solution = Solution.objects.create(
            title="Published Solution",
            description="Published",
            status="approved",
            is_published=True,
            org=org_a,
            created_by=admin_user,
        )
        solution.unpublish()
        solution.refresh_from_db()
        assert solution.is_published is False


@pytest.mark.django_db
class TestCasePipelineAndStageModels:
    """Tests for CasePipeline and CaseStage model methods."""

    def test_pipeline_str(self, admin_user, org_a):
        pipeline = CasePipeline.objects.create(
            name="Support Pipeline",
            org=org_a,
            created_by=admin_user,
        )
        assert str(pipeline) == f"Support Pipeline ({org_a.name})"

    def test_stage_str(self, admin_user, org_a):
        pipeline = CasePipeline.objects.create(
            name="Dev Pipeline",
            org=org_a,
            created_by=admin_user,
        )
        stage = CaseStage.objects.create(
            pipeline=pipeline,
            name="Triage",
            order=1,
            org=org_a,
            created_by=admin_user,
        )
        assert str(stage) == "Dev Pipeline - Triage"

    def test_stage_auto_sets_org_from_pipeline(self, admin_user, org_a):
        """CaseStage.save() auto-sets org from pipeline if not provided."""
        pipeline = CasePipeline.objects.create(
            name="Auto Org Pipeline",
            org=org_a,
            created_by=admin_user,
        )
        stage = CaseStage(
            pipeline=pipeline,
            name="Auto Org Stage",
            order=1,
        )
        stage.save()
        assert stage.org_id == org_a.id
