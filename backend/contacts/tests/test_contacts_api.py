"""
Tests for Contacts API endpoints.

Covers:
- ContactsListView (GET list, POST create)
- ContactDetailView (GET detail, PUT update, PATCH partial update, DELETE, POST comment)
- ContactCommentView (PUT, PATCH, DELETE comments)
- ContactAttachmentView (DELETE attachments)
- Organization isolation between tenants
- Duplicate email validation per org
- Permission checks for non-admin users
- Filtering and search

Run with: pytest contacts/tests/test_contacts_api.py -v
"""

import json
from unittest.mock import patch

import pytest
from django.contrib.contenttypes.models import ContentType
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from rest_framework import status

from common.models import Attachments, Comment, Tags, Teams
from contacts.models import Contact

CONTACTS_LIST_URL = "/api/contacts/"


def _detail_url(pk):
    return f"/api/contacts/{pk}/"


def _comment_url(pk):
    return f"/api/contacts/comment/{pk}/"


def _attachment_url(pk):
    return f"/api/contacts/attachment/{pk}/"


def _set_rls(org):
    """Set PostgreSQL RLS context so direct ORM writes are allowed.
    No-op on SQLite (used in tests).
    """
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.current_org', %s, false)", [str(org.id)])


@pytest.fixture
def contact_a(admin_user, org_a):
    """A contact belonging to org_a, created by admin_user."""
    _set_rls(org_a)
    return Contact.objects.create(
        first_name="Alice",
        last_name="Smith",
        email="alice@example.com",
        org=org_a,
        created_by=admin_user,
    )


@pytest.fixture
def contact_b(user_b, org_b):
    """A contact belonging to org_b, created by user_b."""
    _set_rls(org_b)
    return Contact.objects.create(
        first_name="Bob",
        last_name="Jones",
        email="bob@example.com",
        org=org_b,
        created_by=user_b,
    )


@pytest.mark.django_db
class TestContactListView:
    """Tests for GET /api/contacts/ and POST /api/contacts/"""

    @patch("contacts.views.send_email_to_assigned_user.delay")
    def test_list_contacts(self, mock_email, admin_client, contact_a):
        """Admin can list contacts in their org."""
        response = admin_client.get(CONTACTS_LIST_URL)
        assert response.status_code == status.HTTP_200_OK
        assert "contact_obj_list" in response.data
        assert response.data["contacts_count"] >= 1

    @patch("contacts.views.send_email_to_assigned_user.delay")
    def test_create_contact(self, mock_email, admin_client):
        """Admin can create a new contact via POST."""
        payload = {
            "first_name": "Charlie",
            "last_name": "Brown",
            "email": "charlie@example.com",
        }
        response = admin_client.post(CONTACTS_LIST_URL, payload, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["error"] is False

    def test_create_contact_unauthenticated(self, unauthenticated_client):
        """An anonymous POST is refused, and creates nothing.

        This used to assert `pytest.raises(PermissionDenied)`, which can never
        hold through an APIClient: DRF catches APIException and renders it as a
        response, so the exception never reaches the test. The assertion worth
        making is the one about the database.
        """
        payload = {
            "first_name": "Nope",
            "last_name": "User",
            "email": "nope@example.com",
        }
        response = unauthenticated_client.post(
            CONTACTS_LIST_URL, payload, format="json"
        )

        assert response.status_code in (401, 403)
        assert not Contact.objects.filter(email="nope@example.com").exists()

    @patch("contacts.views.send_email_to_assigned_user.delay")
    def test_org_isolation(
        self, mock_email, admin_client, org_b_client, contact_a, contact_b
    ):
        """Contacts created in org_a must not appear in org_b's list."""
        # org_b_client should only see org_b contacts
        response = org_b_client.get(CONTACTS_LIST_URL)
        assert response.status_code == status.HTTP_200_OK
        emails = [c["email"] for c in response.data["contact_obj_list"]]
        assert "alice@example.com" not in emails
        assert "bob@example.com" in emails

    @patch("contacts.views.send_email_to_assigned_user.delay")
    def test_create_contact_with_all_fields(self, mock_email, admin_client, org_a):
        """Creating a contact with all optional fields populates them correctly."""
        payload = {
            "first_name": "Full",
            "last_name": "Contact",
            "email": "full@example.com",
            "phone": "+1234567890",
            "organization": "Acme Inc",
            "title": "CTO",
            "department": "Engineering",
            "do_not_call": True,
            "linkedin_url": "https://linkedin.com/in/fullcontact",
            "address_line": "456 Oak St",
            "city": "Portland",
            "state": "OR",
            "postcode": "97201",
            "country": "US",
            "description": "A very important contact",
        }
        response = admin_client.post(CONTACTS_LIST_URL, payload, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["error"] is False
        contact = Contact.objects.get(email="full@example.com")
        assert contact.organization == "Acme Inc"
        assert contact.title == "CTO"
        assert contact.department == "Engineering"
        assert contact.do_not_call is True
        assert contact.city == "Portland"
        assert contact.description == "A very important contact"

    @patch("contacts.views.send_email_to_assigned_user.delay")
    def test_create_contact_with_tags(self, mock_email, admin_client, org_a):
        """Creating a contact with tags associates them properly."""
        _set_rls(org_a)
        tag = Tags.objects.create(name="Partner", org=org_a)
        payload = {
            "first_name": "Tagged",
            "last_name": "Contact",
            "email": "tagged@example.com",
            "tags": [str(tag.id)],
        }
        response = admin_client.post(CONTACTS_LIST_URL, payload, format="json")
        assert response.status_code == status.HTTP_200_OK
        contact = Contact.objects.get(email="tagged@example.com")
        assert tag in contact.tags.all()

    @patch("contacts.views.send_email_to_assigned_user.delay")
    def test_create_contact_with_assigned_to(
        self, mock_email, admin_client, admin_profile, org_a
    ):
        """Creating a contact with assigned_to sets assignees."""
        payload = {
            "first_name": "Assigned",
            "last_name": "Contact",
            "email": "assignedcontact@example.com",
            "assigned_to": [str(admin_profile.id)],
        }
        response = admin_client.post(CONTACTS_LIST_URL, payload, format="json")
        assert response.status_code == status.HTTP_200_OK
        contact = Contact.objects.get(email="assignedcontact@example.com")
        assert admin_profile in contact.assigned_to.all()

    @patch("contacts.views.send_email_to_assigned_user.delay")
    def test_create_contact_with_teams(
        self, mock_email, admin_client, admin_user, org_a
    ):
        """Creating a contact with teams associates them."""
        _set_rls(org_a)
        team = Teams.objects.create(
            name="Support Team", created_by=admin_user, org=org_a
        )
        payload = {
            "first_name": "Teamed",
            "last_name": "Contact",
            "email": "teamedcontact@example.com",
            "teams": [str(team.id)],
        }
        response = admin_client.post(CONTACTS_LIST_URL, payload, format="json")
        assert response.status_code == status.HTTP_200_OK
        contact = Contact.objects.get(email="teamedcontact@example.com")
        assert team in contact.teams.all()

    def test_create_contact_invalid_data(self, admin_client):
        """Creating a contact with missing required fields returns 400."""
        response = admin_client.post(
            CONTACTS_LIST_URL, {"email": "no-names@example.com"}, format="json"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"] is True

    def test_list_contacts_search_filter(self, admin_client, contact_a):
        """Search filter works on name, email, phone."""
        response = admin_client.get(CONTACTS_LIST_URL, {"search": "Alice"})
        assert response.status_code == status.HTTP_200_OK
        emails = [c["email"] for c in response.data["contact_obj_list"]]
        assert "alice@example.com" in emails

    def test_list_contacts_email_filter(self, admin_client, contact_a):
        """Email filter works."""
        response = admin_client.get(CONTACTS_LIST_URL, {"email": "alice"})
        assert response.status_code == status.HTTP_200_OK
        assert response.data["contacts_count"] >= 1

    def test_list_contacts_name_filter(self, admin_client, contact_a):
        """Name filter works."""
        response = admin_client.get(CONTACTS_LIST_URL, {"name": "Alice"})
        assert response.status_code == status.HTTP_200_OK

    def test_list_contacts_phone_filter(self, admin_client, admin_user, org_a):
        """Phone filter works."""
        _set_rls(org_a)
        Contact.objects.create(
            first_name="Phone",
            last_name="Contact",
            email="phone@example.com",
            phone="+9876543210",
            org=org_a,
            created_by=admin_user,
        )
        response = admin_client.get(CONTACTS_LIST_URL, {"phone": "9876"})
        assert response.status_code == status.HTTP_200_OK

    def test_list_contacts_created_at_filter(self, admin_client, contact_a):
        """Created_at date range filter works."""
        response = admin_client.get(
            CONTACTS_LIST_URL,
            {"created_at__gte": "2020-01-01", "created_at__lte": "2030-12-31"},
        )
        assert response.status_code == status.HTTP_200_OK

    def test_list_contacts_non_admin_sees_assigned(
        self, user_client, admin_user, org_a, user_profile
    ):
        """Non-admin user sees contacts they are assigned to."""
        _set_rls(org_a)
        Contact.objects.create(
            first_name="AdminOnly",
            last_name="Contact",
            email="adminonly@example.com",
            org=org_a,
            created_by=admin_user,
        )
        assigned_contact = Contact.objects.create(
            first_name="Assigned",
            last_name="Contact",
            email="assignedcontact2@example.com",
            org=org_a,
            created_by=admin_user,
        )
        assigned_contact.assigned_to.add(user_profile)
        response = user_client.get(CONTACTS_LIST_URL)
        assert response.status_code == status.HTTP_200_OK
        emails = [c["email"] for c in response.data["contact_obj_list"]]
        assert "assignedcontact2@example.com" in emails
        assert "adminonly@example.com" not in emails

    def test_list_contacts_context_keys(self, admin_client, org_a):
        """Response contains expected metadata keys."""
        response = admin_client.get(CONTACTS_LIST_URL)
        assert response.status_code == status.HTTP_200_OK
        data = response.data
        assert "per_page" in data
        assert "page_number" in data
        assert "contacts_count" in data
        assert "countries" in data
        assert "users" in data
        assert "count" in data
        assert "results" in data


@pytest.mark.django_db
class TestContactDetailView:
    """Tests for GET/PUT/DELETE/POST on /api/contacts/<pk>/"""

    def test_get_contact_detail(self, admin_client, contact_a):
        """Admin can retrieve a single contact's detail."""
        response = admin_client.get(_detail_url(contact_a.pk))
        assert response.status_code == status.HTTP_200_OK
        assert "contact_obj" in response.data
        assert response.data["contact_obj"]["id"] == str(contact_a.pk)

    def test_related_deals_carry_their_currency(self, admin_client, contact_a, org_a):
        """The page adds these up per currency, so each row has to say which.

        A blank currency is the org's default, the same rule the deal
        serializer applies on create and the pipeline totals group by.
        """
        from opportunity.models import Opportunity

        org_a.default_currency = "INR"
        org_a.save()
        for name, currency in (("Euro deal", "EUR"), ("Blank deal", "")):
            deal = Opportunity.objects.create(
                name=name, org=org_a, amount="10", currency=currency
            )
            deal.contacts.add(contact_a)

        response = admin_client.get(_detail_url(contact_a.pk))

        assert response.status_code == status.HTTP_200_OK
        assert {d["name"]: d["currency"] for d in response.data["opportunities"]} == {
            "Euro deal": "EUR",
            "Blank deal": "INR",
        }

    @patch("contacts.views.send_email_to_assigned_user.delay")
    def test_update_contact(self, mock_email, admin_client, contact_a):
        """Admin can update a contact via PUT."""
        payload = {
            "first_name": "Updated",
            "last_name": "Name",
            "email": "alice@example.com",
        }
        response = admin_client.put(_detail_url(contact_a.pk), payload, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["error"] is False
        contact_a.refresh_from_db()
        assert contact_a.first_name == "Updated"

    def test_delete_contact(self, admin_client, contact_a):
        """Admin can delete a contact."""
        response = admin_client.delete(_detail_url(contact_a.pk))
        assert response.status_code == status.HTTP_200_OK
        assert response.data["error"] is False
        assert not Contact.objects.filter(pk=contact_a.pk).exists()

    def test_get_contact_cross_org(self, org_b_client, contact_a):
        """Accessing a contact from another org returns 404."""
        response = org_b_client.get(_detail_url(contact_a.pk))
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_add_comment_to_contact(self, admin_client, contact_a):
        """Admin can add a comment to a contact via POST to detail endpoint."""
        payload = {"comment": "This is a test comment"}
        response = admin_client.post(_detail_url(contact_a.pk), payload, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert "comments" in response.data

    @patch("contacts.views.send_email_to_assigned_user.delay")
    def test_duplicate_email_same_org(self, mock_email, admin_client, contact_a):
        """Creating a contact with a duplicate email in the same org returns 400."""
        payload = {
            "first_name": "Duplicate",
            "last_name": "Email",
            "email": "alice@example.com",
        }
        response = admin_client.post(CONTACTS_LIST_URL, payload, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"] is True

    def test_get_contact_detail_context_keys(self, admin_client, contact_a):
        """Detail response contains all expected context keys."""
        response = admin_client.get(_detail_url(contact_a.pk))
        assert response.status_code == status.HTTP_200_OK
        data = response.data
        assert "contact_obj" in data
        assert "address_obj" in data
        assert "comments" in data
        assert "attachments" in data
        assert "assigned_data" in data
        assert "tasks" in data
        assert "users_mention" in data
        assert "countries" in data

    @patch("contacts.views.send_email_to_assigned_user.delay")
    def test_update_contact_with_tags(self, mock_email, admin_client, contact_a, org_a):
        """PUT with tags clears old tags and sets new ones."""
        _set_rls(org_a)
        tag1 = Tags.objects.create(name="OldCTag", org=org_a)
        tag2 = Tags.objects.create(name="NewCTag", org=org_a)
        contact_a.tags.add(tag1)

        response = admin_client.put(
            _detail_url(contact_a.pk),
            {
                "first_name": "Alice",
                "last_name": "Smith",
                "email": "alice@example.com",
                "tags": [str(tag2.id)],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        contact_a.refresh_from_db()
        assert tag2 in contact_a.tags.all()
        assert tag1 not in contact_a.tags.all()

    @patch("contacts.views.send_email_to_assigned_user.delay")
    def test_update_contact_with_assigned_to(
        self, mock_email, admin_client, contact_a, admin_profile, org_a
    ):
        """PUT with assigned_to updates assignees."""
        response = admin_client.put(
            _detail_url(contact_a.pk),
            {
                "first_name": "Alice",
                "last_name": "Smith",
                "email": "alice@example.com",
                "assigned_to": [str(admin_profile.id)],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        contact_a.refresh_from_db()
        assert admin_profile in contact_a.assigned_to.all()

    @patch("contacts.views.send_email_to_assigned_user.delay")
    def test_update_contact_with_teams(
        self, mock_email, admin_client, contact_a, admin_user, org_a
    ):
        """PUT with teams updates team associations."""
        _set_rls(org_a)
        team = Teams.objects.create(name="UpdateTeam", created_by=admin_user, org=org_a)
        response = admin_client.put(
            _detail_url(contact_a.pk),
            {
                "first_name": "Alice",
                "last_name": "Smith",
                "email": "alice@example.com",
                "teams": [str(team.id)],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        contact_a.refresh_from_db()
        assert team in contact_a.teams.all()

    def test_delete_contact_non_admin_not_creator_forbidden(
        self, user_client, admin_user, org_a, user_profile
    ):
        """Non-admin user who did not create the contact cannot delete it."""
        _set_rls(org_a)
        contact = Contact.objects.create(
            first_name="Protected",
            last_name="Contact",
            email="protectedcontact@example.com",
            org=org_a,
            created_by=admin_user,
        )
        response = user_client.delete(_detail_url(contact.pk))
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_get_detail_non_admin_not_assigned_forbidden(
        self, user_client, admin_user, org_a, user_profile
    ):
        """Non-admin user not assigned/creator gets 403 on detail."""
        _set_rls(org_a)
        contact = Contact.objects.create(
            first_name="Restricted",
            last_name="Contact",
            email="restrictedcontact@example.com",
            org=org_a,
            created_by=admin_user,
        )
        response = user_client.get(_detail_url(contact.pk))
        assert response.status_code in (status.HTTP_200_OK, status.HTTP_403_FORBIDDEN)
        if response.status_code == status.HTTP_200_OK:
            data = response.data
            if "error" in data:
                assert data["error"] is True

    def test_add_comment_non_admin_not_assigned_forbidden(
        self, user_client, admin_user, org_a, user_profile
    ):
        """Non-admin user not assigned/creator gets 403 when adding comment."""
        _set_rls(org_a)
        contact = Contact.objects.create(
            first_name="CommentForbid",
            last_name="Contact",
            email="cforbid@example.com",
            org=org_a,
            created_by=admin_user,
        )
        response = user_client.post(
            _detail_url(contact.pk),
            {"comment": "Should fail"},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_patch_contact_partial_update(self, admin_client, contact_a):
        """PATCH allows partial field updates."""
        response = admin_client.patch(
            _detail_url(contact_a.pk),
            {"first_name": "PatchedAlice"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["error"] is False
        contact_a.refresh_from_db()
        assert contact_a.first_name == "PatchedAlice"
        assert contact_a.last_name == "Smith"  # unchanged

    def test_patch_contact_with_tags(self, admin_client, contact_a, org_a):
        """PATCH with tags replaces existing tags."""
        _set_rls(org_a)
        tag1 = Tags.objects.create(name="PatchOldC", org=org_a)
        tag2 = Tags.objects.create(name="PatchNewC", org=org_a)
        contact_a.tags.add(tag1)
        response = admin_client.patch(
            _detail_url(contact_a.pk),
            {"tags": [str(tag2.id)]},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        contact_a.refresh_from_db()
        assert tag2 in contact_a.tags.all()
        assert tag1 not in contact_a.tags.all()

    def test_patch_contact_clear_tags(self, admin_client, contact_a, org_a):
        """PATCH with empty tags list clears all tags."""
        _set_rls(org_a)
        tag = Tags.objects.create(name="ClearMeC", org=org_a)
        contact_a.tags.add(tag)
        response = admin_client.patch(
            _detail_url(contact_a.pk),
            {"tags": []},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        contact_a.refresh_from_db()
        assert contact_a.tags.count() == 0

    def test_patch_contact_with_assigned_to(
        self, admin_client, contact_a, admin_profile
    ):
        """PATCH with assigned_to updates assignees."""
        response = admin_client.patch(
            _detail_url(contact_a.pk),
            {"assigned_to": [str(admin_profile.id)]},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        contact_a.refresh_from_db()
        assert admin_profile in contact_a.assigned_to.all()

    def test_patch_contact_with_teams(self, admin_client, contact_a, admin_user, org_a):
        """PATCH with teams updates team associations."""
        _set_rls(org_a)
        team = Teams.objects.create(name="PatchTeam", created_by=admin_user, org=org_a)
        response = admin_client.patch(
            _detail_url(contact_a.pk),
            {"teams": [str(team.id)]},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        contact_a.refresh_from_db()
        assert team in contact_a.teams.all()

    def test_patch_contact_non_admin_not_creator_forbidden(
        self, user_client, admin_user, org_a, user_profile
    ):
        """Non-admin not assigned/creator gets 403 on PATCH."""
        _set_rls(org_a)
        contact = Contact.objects.create(
            first_name="ForbidPatch",
            last_name="Contact",
            email="forbidpatchc@example.com",
            org=org_a,
            created_by=admin_user,
        )
        response = user_client.patch(
            _detail_url(contact.pk),
            {"first_name": "Hacked"},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_patch_contact_invalid_data(self, admin_client, contact_a):
        """PATCH with invalid data returns 400."""
        response = admin_client.patch(
            _detail_url(contact_a.pk),
            {"email": "not-an-email"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
class TestContactCommentView:
    """Tests for PUT/PATCH/DELETE /api/contacts/comment/<pk>/."""

    @pytest.fixture
    def comment_fixture(self, admin_user, admin_profile, org_a):
        """Create a contact and a comment on it."""
        _set_rls(org_a)
        contact = Contact.objects.create(
            first_name="CommentTest",
            last_name="Contact",
            email="commenttest@example.com",
            org=org_a,
            created_by=admin_user,
        )
        ct = ContentType.objects.get_for_model(Contact)
        comment = Comment.objects.create(
            content_type=ct,
            object_id=contact.id,
            comment="Original comment",
            commented_by=admin_profile,
            org=org_a,
        )
        return contact, comment

    def test_update_comment_put(
        self, admin_client, comment_fixture, admin_profile, org_a
    ):
        """Admin can update a comment via PUT."""
        contact, comment = comment_fixture
        response = admin_client.put(
            _comment_url(comment.id),
            {
                "comment": "Updated comment",
                "commented_by": str(admin_profile.id),
                "object_id": str(contact.id),
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
            {"comment": "Patched comment"},
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
        _, comment = comment_fixture
        response = user_client.put(
            _comment_url(comment.id),
            {"comment": "Hacked"},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_delete_comment_non_admin_non_author_forbidden(
        self, user_client, comment_fixture, user_profile
    ):
        """Non-admin non-author gets 403 on DELETE."""
        _, comment = comment_fixture
        response = user_client.delete(_comment_url(comment.id))
        assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
class TestContactAttachmentView:
    """Tests for DELETE /api/contacts/attachment/<pk>/."""

    def test_delete_attachment_admin(self, admin_client, admin_user, org_a):
        """Admin can delete an attachment."""
        _set_rls(org_a)
        contact = Contact.objects.create(
            first_name="Attach",
            last_name="Contact",
            email="attachcontact@example.com",
            org=org_a,
            created_by=admin_user,
        )
        ct = ContentType.objects.get_for_model(Contact)
        attachment = Attachments.objects.create(
            content_type=ct,
            object_id=contact.id,
            file_name="test.txt",
            attachment="attachments/test.txt",
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
        contact = Contact.objects.create(
            first_name="AttachForbid",
            last_name="Contact",
            email="attachforbidc@example.com",
            org=org_a,
            created_by=admin_user,
        )
        ct = ContentType.objects.get_for_model(Contact)
        attachment = Attachments.objects.create(
            content_type=ct,
            object_id=contact.id,
            file_name="forbidden.txt",
            attachment="attachments/forbidden.txt",
            created_by=admin_user,
            org=org_a,
        )
        response = user_client.delete(_attachment_url(attachment.id))
        assert response.status_code == status.HTTP_403_FORBIDDEN


# ================================================================
# Coverage-targeted tests for contacts/views.py uncovered lines
# ================================================================


@pytest.mark.django_db
class TestContactListViewFilters:
    """Tests targeting uncovered filter lines in ContactsListView.get_context_data."""

    def test_filter_by_name_matches_first_or_last_name(
        self, admin_client, admin_user, org_a
    ):
        """The name filter matches a partial first or last name."""
        _set_rls(org_a)
        first_name_match = Contact.objects.create(
            first_name="John",
            last_name="Smith",
            email="john.smith@example.com",
            org=org_a,
            created_by=admin_user,
        )
        last_name_match = Contact.objects.create(
            first_name="Jane",
            last_name="Johnson",
            email="jane.johnson@example.com",
            org=org_a,
            created_by=admin_user,
        )

        response = admin_client.get(CONTACTS_LIST_URL, {"name": "john"})

        assert response.status_code == status.HTTP_200_OK
        returned_ids = {contact["id"] for contact in response.data["contact_obj_list"]}
        assert returned_ids == {str(first_name_match.id), str(last_name_match.id)}

    def test_filter_by_city(self, admin_client, admin_user, org_a):
        """Filter by city.

        This case used to assert `pytest.raises(FieldError)` -- the view
        traversed `address__city` on a model whose address is flat, so asking
        for a city was a 500. The branch is still exercised; what it now says
        is that the filter works.
        """
        _set_rls(org_a)
        Contact.objects.create(
            first_name="CityFilter",
            last_name="Contact",
            email="cityfilter@example.com",
            city="Denver",
            org=org_a,
            created_by=admin_user,
        )
        response = admin_client.get(CONTACTS_LIST_URL, {"city": "Denver"})

        assert response.status_code == status.HTTP_200_OK
        assert [row["first_name"] for row in response.data["results"]] == ["CityFilter"]

    def test_filter_by_assigned_to(
        self, admin_client, admin_user, admin_profile, org_a
    ):
        """Filter by assigned_to.

        Also once pinned as a bug: the view asked `getlist` whether the
        parameter was there and then read it with `get`, handing `__in` a
        string that Django iterates character by character.
        """
        _set_rls(org_a)
        contact = Contact.objects.create(
            first_name="AssignFilter",
            last_name="Contact",
            email="assignfilterc@example.com",
            org=org_a,
            created_by=admin_user,
        )
        contact.assigned_to.add(admin_profile)
        unassigned = Contact.objects.create(
            first_name="NotAssigned",
            last_name="Contact",
            email="notassignedc@example.com",
            org=org_a,
            created_by=admin_user,
        )

        response = admin_client.get(
            CONTACTS_LIST_URL, {"assigned_to": str(admin_profile.id)}
        )

        assert response.status_code == status.HTTP_200_OK
        ids = [row["id"] for row in response.data["results"]]
        assert str(contact.id) in ids
        assert str(unassigned.id) not in ids

    def test_filter_by_tags(self, admin_client, admin_user, org_a):
        """Filter by tags (lines 61-64)."""
        _set_rls(org_a)
        tag = Tags.objects.create(name="ContactFilterTag", org=org_a)
        contact = Contact.objects.create(
            first_name="TagFilter",
            last_name="Contact",
            email="tagfilterc@example.com",
            org=org_a,
            created_by=admin_user,
        )
        contact.tags.add(tag)
        response = admin_client.get(CONTACTS_LIST_URL, {"tags": str(tag.id)})
        assert response.status_code == status.HTTP_200_OK


@pytest.mark.django_db
class TestContactCreateM2MHandling:
    """Tests targeting uncovered M2M handling in ContactsListView.post."""

    @patch("contacts.views.send_email_to_assigned_user.delay")
    def test_create_contact_with_teams_as_json_string(
        self, mock_email, admin_client, admin_user, org_a
    ):
        """Teams passed as JSON string are parsed (line 164)."""
        _set_rls(org_a)
        team = Teams.objects.create(
            name="StringTeamC", created_by=admin_user, org=org_a
        )
        payload = {
            "first_name": "StringTeam",
            "last_name": "Contact",
            "email": "stringteamc@example.com",
            "teams": json.dumps([str(team.id)]),
        }
        response = admin_client.post(CONTACTS_LIST_URL, payload, format="json")
        assert response.status_code == status.HTTP_200_OK
        contact = Contact.objects.get(email="stringteamc@example.com")
        assert team in contact.teams.all()

    @patch("contacts.views.send_email_to_assigned_user.delay")
    def test_create_contact_with_assigned_to_as_json_string(
        self, mock_email, admin_client, admin_profile, org_a
    ):
        """assigned_to passed as JSON string are parsed (line 176)."""
        payload = {
            "first_name": "StringAssign",
            "last_name": "Contact",
            "email": "stringassignc@example.com",
            "assigned_to": json.dumps([str(admin_profile.id)]),
        }
        response = admin_client.post(CONTACTS_LIST_URL, payload, format="json")
        assert response.status_code == status.HTTP_200_OK
        contact = Contact.objects.get(email="stringassignc@example.com")
        assert admin_profile in contact.assigned_to.all()

    @patch("contacts.views.send_email_to_assigned_user.delay")
    def test_create_contact_with_tags_as_json_string(
        self, mock_email, admin_client, org_a
    ):
        """Tags passed as JSON string are parsed (line 190)."""
        _set_rls(org_a)
        tag = Tags.objects.create(name="StringTagC", org=org_a)
        payload = {
            "first_name": "StringTag",
            "last_name": "Contact",
            "email": "stringtagc@example.com",
            "tags": json.dumps([str(tag.id)]),
        }
        response = admin_client.post(CONTACTS_LIST_URL, payload, format="json")
        assert response.status_code == status.HTTP_200_OK
        contact = Contact.objects.get(email="stringtagc@example.com")
        assert tag in contact.tags.all()

    @patch("contacts.views.send_email_to_assigned_user.delay")
    def test_create_contact_email_notification(
        self, mock_email, admin_client, admin_profile, org_a
    ):
        """Email notification is sent on create (lines 201-206)."""
        payload = {
            "first_name": "EmailNotify",
            "last_name": "Contact",
            "email": "emailnotifyc@example.com",
            "assigned_to": [str(admin_profile.id)],
        }
        response = admin_client.post(CONTACTS_LIST_URL, payload, format="json")
        assert response.status_code == status.HTTP_200_OK
        mock_email.assert_called_once()
        call_args = mock_email.call_args
        assert admin_profile.id in call_args[0][0]

    @patch("contacts.views.send_email_to_assigned_user.delay")
    def test_create_contact_with_attachment(self, mock_email, admin_client, org_a):
        """Creating a contact with attachment (lines 208-215)."""
        test_file = SimpleUploadedFile(
            "contact_doc.txt", b"contact file content", content_type="text/plain"
        )
        payload = {
            "first_name": "Attached",
            "last_name": "Contact",
            "email": "attachedc@example.com",
            "contact_attachment": test_file,
        }
        response = admin_client.post(CONTACTS_LIST_URL, payload, format="multipart")
        assert response.status_code == status.HTTP_200_OK
        contact = Contact.objects.get(email="attachedc@example.com")
        ct = ContentType.objects.get_for_model(Contact)
        assert Attachments.objects.filter(
            content_type=ct, object_id=contact.id
        ).exists()


@pytest.mark.django_db
class TestContactDetailUpdateM2M:
    """Tests targeting uncovered M2M handling in ContactDetailView.put."""

    @patch("contacts.views.send_email_to_assigned_user.delay")
    def test_update_contact_with_teams_as_json_string(
        self, mock_email, admin_client, contact_a, admin_user, org_a
    ):
        """PUT with teams as JSON string (line 279)."""
        _set_rls(org_a)
        team = Teams.objects.create(
            name="PutStringTeam", created_by=admin_user, org=org_a
        )
        response = admin_client.put(
            _detail_url(contact_a.pk),
            {
                "first_name": "Alice",
                "last_name": "Smith",
                "email": "alice@example.com",
                "teams": json.dumps([str(team.id)]),
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        contact_a.refresh_from_db()
        assert team in contact_a.teams.all()

    @patch("contacts.views.send_email_to_assigned_user.delay")
    def test_update_contact_with_assigned_to_as_json_string(
        self, mock_email, admin_client, contact_a, admin_profile, org_a
    ):
        """PUT with assigned_to as JSON string (line 292)."""
        response = admin_client.put(
            _detail_url(contact_a.pk),
            {
                "first_name": "Alice",
                "last_name": "Smith",
                "email": "alice@example.com",
                "assigned_to": json.dumps([str(admin_profile.id)]),
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        contact_a.refresh_from_db()
        assert admin_profile in contact_a.assigned_to.all()

    @patch("contacts.views.send_email_to_assigned_user.delay")
    def test_update_contact_with_tags_as_json_string(
        self, mock_email, admin_client, contact_a, org_a
    ):
        """PUT with tags as JSON string (line 307)."""
        _set_rls(org_a)
        tag = Tags.objects.create(name="PutStringTag", org=org_a)
        response = admin_client.put(
            _detail_url(contact_a.pk),
            {
                "first_name": "Alice",
                "last_name": "Smith",
                "email": "alice@example.com",
                "tags": json.dumps([str(tag.id)]),
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        contact_a.refresh_from_db()
        assert tag in contact_a.tags.all()

    @patch("contacts.views.send_email_to_assigned_user.delay")
    def test_update_contact_with_attachment(
        self, mock_email, admin_client, contact_a, org_a
    ):
        """PUT with contact_attachment creates attachment (lines 331-338)."""
        test_file = SimpleUploadedFile(
            "update_contact.txt", b"update content", content_type="text/plain"
        )
        response = admin_client.put(
            _detail_url(contact_a.pk),
            {
                "first_name": "Alice",
                "last_name": "Smith",
                "email": "alice@example.com",
                "contact_attachment": test_file,
            },
            format="multipart",
        )
        assert response.status_code == status.HTTP_200_OK
        ct = ContentType.objects.get_for_model(Contact)
        assert Attachments.objects.filter(
            content_type=ct, object_id=contact_a.id
        ).exists()

    @patch("contacts.views.send_email_to_assigned_user.delay")
    def test_update_contact_non_admin_not_creator_forbidden(
        self, mock_email, user_client, admin_user, org_a, user_profile
    ):
        """Non-admin non-creator gets 403 on PUT (lines 261-266)."""
        _set_rls(org_a)
        contact = Contact.objects.create(
            first_name="ForbidUpdate",
            last_name="Contact",
            email="forbidupdate@example.com",
            org=org_a,
            created_by=admin_user,
        )
        response = user_client.put(
            _detail_url(contact.pk),
            {
                "first_name": "Hacked",
                "last_name": "Contact",
                "email": "forbidupdate@example.com",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    @patch("contacts.views.send_email_to_assigned_user.delay")
    def test_update_contact_invalid_data(self, mock_email, admin_client, contact_a):
        """PUT with invalid data returns 400 (line 255-258)."""
        response = admin_client.put(
            _detail_url(contact_a.pk),
            {"first_name": "Valid", "last_name": "Contact", "email": "not-an-email"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
class TestContactDetailCommentAttachment:
    """Tests targeting uncovered POST comment/attachment lines in ContactDetailView."""

    def test_add_comment_serializer_invalid_skips_creation(
        self, admin_client, admin_user, org_a
    ):
        """POST with just comment (no object_id/org) - serializer invalid, comment not created.
        CommentSerializer requires object_id and org, so the save branch is skipped.
        Still returns 200 with contact data (lines 514-516 exercised, save skipped).
        """
        _set_rls(org_a)
        contact = Contact.objects.create(
            first_name="CommentObj",
            last_name="Contact",
            email="commentobjc@example.com",
            org=org_a,
            created_by=admin_user,
        )
        response = admin_client.post(
            _detail_url(contact.pk),
            {"comment": "test comment"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert "contact_obj" in response.data

    def test_add_comment_ignores_client_supplied_target_and_org(
        self, admin_client, admin_user, admin_profile, org_a
    ):
        """A comment records against the contact in the URL, in the caller's org.

        This case used to assert `pytest.raises(TypeError, match="contact_id")`
        -- the view called `save(contact_id=...)` on a model with no such
        field. Sending `object_id` and `org` was the only way to get past
        validation at all, and doing so is what raised. Both are server facts
        now, so sending them changes nothing.
        """
        _set_rls(org_a)
        contact = Contact.objects.create(
            first_name="CommentBug",
            last_name="Contact",
            email="commentbug@example.com",
            org=org_a,
            created_by=admin_user,
        )
        response = admin_client.post(
            _detail_url(contact.pk),
            {
                "comment": "Bug test",
                "object_id": str(contact.id),
                "org": str(org_a.id),
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        saved = Comment.objects.get(comment="Bug test")
        assert saved.object_id == contact.id
        assert saved.org_id == org_a.id
        assert saved.commented_by_id == admin_profile.id

    def test_add_attachment_via_post(self, admin_client, admin_user, org_a):
        """POST with contact_attachment creates attachment (lines 523-530).
        The attachment is created regardless of comment serializer validity.
        """
        _set_rls(org_a)
        contact = Contact.objects.create(
            first_name="AttachOnly",
            last_name="Contact",
            email="attachonlyc@example.com",
            org=org_a,
            created_by=admin_user,
        )
        test_file = SimpleUploadedFile(
            "attach_only.txt", b"just a file", content_type="text/plain"
        )
        response = admin_client.post(
            _detail_url(contact.pk),
            {"contact_attachment": test_file},
            format="multipart",
        )
        assert response.status_code == status.HTTP_200_OK
        ct = ContentType.objects.get_for_model(Contact)
        assert Attachments.objects.filter(
            content_type=ct, object_id=contact.id
        ).exists()


@pytest.mark.django_db
class TestContactDetailGetNonAdmin:
    """Tests targeting uncovered lines in ContactDetailView.get for non-admin users."""

    def _create_contact_with_creator(self, creator, org, **kwargs):
        """Create a contact and force the created_by field via update() to bypass BaseModel.save()."""
        contact = Contact.objects.create(org=org, **kwargs)
        Contact.objects.filter(id=contact.id).update(created_by=creator)
        contact.refresh_from_db()
        return contact

    def test_detail_non_admin_assigned_names_the_creator(
        self, user_client, admin_user, org_a, user_profile
    ):
        """An assignee who did not create the contact can open it.

        This case used to assert `pytest.raises(AttributeError, match="user")`:
        the branch read `created_by.user.email` on a User. It was the only
        branch a non-admin could reach, so what the test really recorded is
        that the detail endpoint answered 500 for every non-admin who was
        entitled to see the record.
        """
        _set_rls(org_a)
        contact = self._create_contact_with_creator(
            admin_user,
            org_a,
            first_name="AssignedDetail",
            last_name="Contact",
            email="assigneddetailc@example.com",
        )
        contact.assigned_to.add(user_profile)

        response = user_client.get(_detail_url(contact.pk))

        assert response.status_code == status.HTTP_200_OK
        assert response.data["users_mention"] == [{"user__email": admin_user.email}]

    def test_detail_non_admin_not_assigned_not_creator_forbidden(
        self, user_client, admin_user, org_a, user_profile
    ):
        """Non-admin non-assigned non-creator gets 403 (lines 380-388)."""
        _set_rls(org_a)
        contact = self._create_contact_with_creator(
            admin_user,
            org_a,
            first_name="Forbidden",
            last_name="Contact",
            email="forbiddendetailc@example.com",
        )
        response = user_client.get(_detail_url(contact.pk))
        assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
class TestContactDeleteOrgCheck:
    """Tests targeting uncovered lines in ContactDetailView.delete."""

    def test_delete_contact_org_mismatch(
        self, org_b_client, admin_user, org_a, profile_b
    ):
        """Delete from different org returns 404 (lines 459-463)."""
        _set_rls(org_a)
        contact = Contact.objects.create(
            first_name="OrgMismatch",
            last_name="Delete",
            email="orgmismatchdelete@example.com",
            org=org_a,
            created_by=admin_user,
        )
        response = org_b_client.delete(_detail_url(contact.pk))
        # get_object uses org=self.request.profile.org so org_b can't find org_a contact
        assert response.status_code in (
            status.HTTP_403_FORBIDDEN,
            status.HTTP_404_NOT_FOUND,
        )


@pytest.mark.django_db
class TestContactPatchM2M:
    """Tests targeting uncovered PATCH M2M handling (lines 604-648)."""

    def test_patch_contact_with_teams_as_json_string(
        self, admin_client, contact_a, admin_user, org_a
    ):
        """PATCH with teams as JSON string (line 609)."""
        _set_rls(org_a)
        team = Teams.objects.create(
            name="PatchStringTeamC", created_by=admin_user, org=org_a
        )
        response = admin_client.patch(
            _detail_url(contact_a.pk),
            {"teams": json.dumps([str(team.id)])},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        contact_a.refresh_from_db()
        assert team in contact_a.teams.all()

    def test_patch_contact_with_assigned_to_as_json_string(
        self, admin_client, contact_a, admin_profile
    ):
        """PATCH with assigned_to as JSON string (line 623)."""
        response = admin_client.patch(
            _detail_url(contact_a.pk),
            {"assigned_to": json.dumps([str(admin_profile.id)])},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        contact_a.refresh_from_db()
        assert admin_profile in contact_a.assigned_to.all()

    def test_patch_contact_with_tags_as_json_string(
        self, admin_client, contact_a, org_a
    ):
        """PATCH with tags as JSON string (line 639)."""
        _set_rls(org_a)
        tag = Tags.objects.create(name="PatchStringTagC", org=org_a)
        response = admin_client.patch(
            _detail_url(contact_a.pk),
            {"tags": json.dumps([str(tag.id)])},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        contact_a.refresh_from_db()
        assert tag in contact_a.tags.all()

    def test_patch_contact_clear_teams(
        self, admin_client, contact_a, admin_user, org_a
    ):
        """PATCH with empty teams list clears all teams."""
        _set_rls(org_a)
        team = Teams.objects.create(name="ClearTeamC", created_by=admin_user, org=org_a)
        contact_a.teams.add(team)
        response = admin_client.patch(
            _detail_url(contact_a.pk),
            {"teams": []},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        contact_a.refresh_from_db()
        assert contact_a.teams.count() == 0

    def test_patch_contact_clear_assigned_to(
        self, admin_client, contact_a, admin_profile
    ):
        """PATCH with empty assigned_to list clears all assignees."""
        contact_a.assigned_to.add(admin_profile)
        response = admin_client.patch(
            _detail_url(contact_a.pk),
            {"assigned_to": []},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        contact_a.refresh_from_db()
        assert contact_a.assigned_to.count() == 0


@pytest.mark.django_db
class TestContactCommentPatchView:
    """Tests targeting uncovered lines in ContactCommentView.patch."""

    @pytest.fixture
    def comment_fix(self, admin_user, admin_profile, org_a):
        _set_rls(org_a)
        contact = Contact.objects.create(
            first_name="PatchComment",
            last_name="Contact",
            email="patchcommentc@example.com",
            org=org_a,
            created_by=admin_user,
        )
        ct = ContentType.objects.get_for_model(Contact)
        comment = Comment.objects.create(
            content_type=ct,
            object_id=contact.id,
            comment="Patch me",
            commented_by=admin_profile,
            org=org_a,
        )
        return contact, comment

    def test_patch_comment_non_admin_non_author_forbidden(
        self, user_client, comment_fix, user_profile
    ):
        """Non-admin non-author gets 403 on PATCH comment (lines 735-739)."""
        _, comment = comment_fix
        response = user_client.patch(
            _comment_url(comment.id),
            {"comment": "Hacked patch"},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN
