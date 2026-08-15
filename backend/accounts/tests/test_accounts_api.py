from unittest.mock import patch

import pytest
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile

from accounts.models import Account, AccountEmail, AccountEmailLog
from common.models import Attachments, Comment, Tags, Teams
from contacts.models import Contact
from invoices.models import Estimate, Invoice, RecurringInvoice


@pytest.mark.django_db
class TestAccountListView:
    """Tests for GET /api/accounts/ and POST /api/accounts/."""

    def test_list_accounts(self, admin_client, org_a):
        Account.objects.create(name="Test Account", org=org_a)
        response = admin_client.get("/api/accounts/")
        assert response.status_code == 200
        data = response.json()
        assert "active_accounts" in data

    def test_create_account(self, admin_client, org_a):
        response = admin_client.post("/api/accounts/", {"name": "New Account"})
        assert response.status_code == 200
        data = response.json()
        assert data["error"] is False
        assert Account.objects.filter(name="New Account", org=org_a).exists()

    def test_create_account_with_tags(self, admin_client, org_a):
        tag = Tags.objects.create(name="VIP", org=org_a)
        response = admin_client.post(
            "/api/accounts/",
            {"name": "Tagged Account", "tags": [str(tag.id)]},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["error"] is False

    def test_create_account_invalid_data(self, admin_client):
        response = admin_client.post("/api/accounts/", {})
        assert response.status_code == 400
        data = response.json()
        assert data["error"] is True

    def test_create_account_unauthenticated(self, unauthenticated_client):
        """An anonymous POST is refused, and creates nothing.

        This used to assert `pytest.raises(PermissionDenied)`, which can never
        hold through an APIClient: DRF catches APIException and renders it as a
        response, so the exception never reaches the test. The assertion worth
        making is the one about the database.
        """
        response = unauthenticated_client.post("/api/accounts/", {"name": "Test"})

        assert response.status_code in (401, 403)
        assert not Account.objects.filter(name="Test").exists()

    def test_create_account_with_all_fields(self, admin_client, org_a, admin_profile):
        """Test creating an account with all optional fields populated."""
        tag = Tags.objects.create(name="Enterprise", org=org_a)
        response = admin_client.post(
            "/api/accounts/",
            {
                "name": "Full Account",
                "email": "full@example.com",
                "phone": "+1234567890",
                "website": "https://example.com",
                "industry": "TECHNOLOGY",
                "description": "A full account description",
                "address_line": "123 Main St",
                "city": "Springfield",
                "state": "IL",
                "postcode": "62701",
                "country": "US",
                "tags": [str(tag.id)],
                "assigned_to": [str(admin_profile.id)],
            },
            format="json",
        )
        assert response.status_code == 200
        data = response.json()
        assert data["error"] is False
        account = Account.objects.get(name="Full Account")
        assert account.email == "full@example.com"
        assert account.city == "Springfield"
        assert account.tags.count() == 1
        assert account.assigned_to.count() == 1

    def test_create_account_duplicate_name(self, admin_client, org_a):
        """Test that creating an account with a duplicate name fails."""
        Account.objects.create(name="Duplicate Test", org=org_a)
        response = admin_client.post(
            "/api/accounts/",
            {"name": "Duplicate Test"},
            format="json",
        )
        assert response.status_code == 400
        assert response.json()["error"] is True

    def test_list_accounts_with_filters(self, admin_client, org_a):
        """Test listing accounts with query parameter filters."""
        Account.objects.create(
            name="Alpha Corp", city="Chicago", industry="TECHNOLOGY", org=org_a
        )
        Account.objects.create(
            name="Beta Inc", city="Boston", industry="FINANCE", org=org_a
        )

        # Filter by name
        response = admin_client.get("/api/accounts/?name=Alpha")
        assert response.status_code == 200
        data = response.json()
        active = data["active_accounts"]["open_accounts"]
        assert len(active) == 1
        assert active[0]["name"] == "Alpha Corp"

        # Filter by city
        response = admin_client.get("/api/accounts/?city=Boston")
        assert response.status_code == 200
        data = response.json()
        active = data["active_accounts"]["open_accounts"]
        assert len(active) == 1

        # Filter by industry
        response = admin_client.get("/api/accounts/?industry=TECHNOLOGY")
        assert response.status_code == 200
        data = response.json()
        active = data["active_accounts"]["open_accounts"]
        assert len(active) == 1

        # Search by name
        response = admin_client.get("/api/accounts/?search=Beta")
        assert response.status_code == 200
        data = response.json()
        active = data["active_accounts"]["open_accounts"]
        assert len(active) == 1

    def test_list_accounts_context_keys(self, admin_client, org_a):
        """Test that the list response includes all expected context keys."""
        response = admin_client.get("/api/accounts/")
        assert response.status_code == 200
        data = response.json()
        assert "active_accounts" in data
        assert "closed_accounts" in data
        assert "contacts" in data
        assert "teams" in data
        assert "countries" in data
        assert "industries" in data
        assert "tags" in data
        assert "users" in data
        assert "leads" in data
        assert "status" in data

    def test_list_accounts_inactive(self, admin_client, org_a):
        """Test that inactive accounts appear in closed_accounts."""
        Account.objects.create(name="Active One", is_active=True, org=org_a)
        Account.objects.create(name="Inactive One", is_active=False, org=org_a)
        response = admin_client.get("/api/accounts/")
        assert response.status_code == 200
        data = response.json()
        assert data["active_accounts"]["open_accounts_count"] == 1
        assert data["closed_accounts"]["close_accounts_count"] == 1

    def test_list_accounts_non_admin_sees_own(
        self, user_client, org_a, regular_user, user_profile, admin_user
    ):
        """Non-admin user sees only accounts they created or are assigned to."""
        admin_account = Account.objects.create(name="Admin Account", org=org_a)
        # Manually set created_by since crum overrides it in tests
        Account.objects.filter(id=admin_account.id).update(created_by=admin_user)
        user_account = Account.objects.create(name="User Account", org=org_a)
        Account.objects.filter(id=user_account.id).update(created_by=regular_user)
        response = user_client.get("/api/accounts/")
        assert response.status_code == 200
        data = response.json()
        active = data["active_accounts"]["open_accounts"]
        names = [a["name"] for a in active]
        assert "User Account" in names
        assert "Admin Account" not in names

    def test_create_account_with_teams(self, admin_client, org_a):
        """Test creating an account with teams assignment."""
        team = Teams.objects.create(name="Sales Team", description="Sales", org=org_a)
        response = admin_client.post(
            "/api/accounts/",
            {
                "name": "Team Account",
                "teams": [str(team.id)],
            },
            format="json",
        )
        assert response.status_code == 200
        assert response.json()["error"] is False


@pytest.mark.django_db
class TestAccountDetailView:
    """Tests for GET/PUT/DELETE /api/accounts/<pk>/."""

    def test_get_account_detail(self, admin_client, org_a):
        account = Account.objects.create(name="Detail Account", org=org_a)
        response = admin_client.get(f"/api/accounts/{account.id}/")
        assert response.status_code == 200
        data = response.json()
        assert "account_obj" in data

    def test_update_account(self, admin_client, org_a):
        account = Account.objects.create(name="Old Name", org=org_a)
        response = admin_client.put(
            f"/api/accounts/{account.id}/",
            {"name": "New Name"},
            format="json",
        )
        assert response.status_code == 200
        data = response.json()
        assert data["error"] is False

    def test_delete_account(self, admin_client, org_a):
        account = Account.objects.create(name="Delete Me", org=org_a)
        response = admin_client.delete(f"/api/accounts/{account.id}/")
        assert response.status_code == 200
        assert not Account.objects.filter(id=account.id).exists()

    @pytest.mark.parametrize(
        ("model", "extra"),
        [
            (Invoice, {"invoice_number": "INV-1"}),
            (Estimate, {"estimate_number": "EST-1"}),
            (RecurringInvoice, {"title": "Monthly retainer"}),
        ],
        ids=["invoice", "estimate", "recurring-invoice"],
    )
    def test_delete_account_with_billing_records_is_refused(
        self, admin_client, org_a, model, extra
    ):
        """Each PROTECT relation answers 409, and the message covers all three.

        Unguarded, the ProtectedError left the view as a 500. The message is
        asserted on because it is the only thing telling an admin which list to
        go and empty, and it named two of the three relations for a while.
        """
        account = Account.objects.create(name="Has Billing", org=org_a)
        model.objects.create(org=org_a, account=account, **extra)

        response = admin_client.delete(f"/api/accounts/{account.id}/")

        assert response.status_code == 409
        body = response.json()
        assert body["error"] is True
        assert body["errors"] == (
            "This account can't be deleted while it still has invoices, "
            "estimates or recurring invoices linked to it."
        )
        assert Account.objects.filter(id=account.id).exists()

    def test_get_account_cross_org(self, org_b_client, org_a):
        account = Account.objects.create(name="Org A Account", org=org_a)
        response = org_b_client.get(f"/api/accounts/{account.id}/")
        assert response.status_code == 404

    def test_get_account_detail_full_context(self, admin_client, org_a, admin_profile):
        """Test that account detail returns full context data."""
        account = Account.objects.create(
            name="Context Account", org=org_a, created_by=admin_profile.user
        )
        response = admin_client.get(f"/api/accounts/{account.id}/")
        assert response.status_code == 200
        data = response.json()
        assert "account_obj" in data
        assert "attachments" in data
        assert "comments" in data
        assert "contacts" in data
        assert "opportunity_list" in data
        assert "users" in data
        assert "teams" in data
        assert "stages" in data
        assert "sources" in data
        assert "countries" in data
        assert "comment_permission" in data
        assert "users_mention" in data
        assert "leads" in data

    def test_get_account_detail_comment_permission_for_creator(
        self, admin_client, org_a, admin_profile
    ):
        """Account creator should have comment_permission = True."""
        account = Account.objects.create(
            name="Creator Account", org=org_a, created_by=admin_profile.user
        )
        response = admin_client.get(f"/api/accounts/{account.id}/")
        assert response.status_code == 200
        assert response.json()["comment_permission"] is True

    def test_update_account_with_tags_and_assigned_to(
        self, admin_client, org_a, admin_profile
    ):
        """Test updating an account with tags and assigned_to."""
        account = Account.objects.create(name="Update Me", org=org_a)
        tag = Tags.objects.create(name="Updated Tag", org=org_a)
        response = admin_client.put(
            f"/api/accounts/{account.id}/",
            {
                "name": "Update Me",
                "tags": [str(tag.id)],
                "assigned_to": [str(admin_profile.id)],
            },
            format="json",
        )
        assert response.status_code == 200
        assert response.json()["error"] is False
        account.refresh_from_db()
        assert account.tags.count() == 1
        assert account.assigned_to.count() == 1

    def test_update_account_clear_tags(self, admin_client, org_a):
        """Test updating an account to clear tags."""
        account = Account.objects.create(name="Clear Tags", org=org_a)
        tag = Tags.objects.create(name="To Remove", org=org_a)
        account.tags.add(tag)
        response = admin_client.put(
            f"/api/accounts/{account.id}/",
            {"name": "Clear Tags"},
            format="json",
        )
        assert response.status_code == 200
        account.refresh_from_db()
        assert account.tags.count() == 0

    def test_update_account_with_contacts(self, admin_client, org_a):
        """Test updating an account with contacts."""
        account = Account.objects.create(name="Contact Account", org=org_a)
        contact = Contact.objects.create(first_name="John", last_name="Doe", org=org_a)
        response = admin_client.put(
            f"/api/accounts/{account.id}/",
            {
                "name": "Contact Account",
                "contacts": [str(contact.id)],
            },
            format="json",
        )
        assert response.status_code == 200
        assert response.json()["error"] is False
        assert account.contacts.count() == 1

    def test_update_account_with_teams(self, admin_client, org_a):
        """Test updating an account with teams."""
        account = Account.objects.create(name="Teams Account", org=org_a)
        team = Teams.objects.create(name="Dev Team", description="Dev", org=org_a)
        response = admin_client.put(
            f"/api/accounts/{account.id}/",
            {
                "name": "Teams Account",
                "teams": [str(team.id)],
            },
            format="json",
        )
        assert response.status_code == 200
        assert account.teams.count() == 1

    def test_update_account_invalid_serializer(self, admin_client, org_a):
        """Test updating an account with invalid data returns 400."""
        account = Account.objects.create(name="Invalid Update", org=org_a)
        # Create another account to trigger unique constraint
        Account.objects.create(name="Existing Account", org=org_a)
        response = admin_client.put(
            f"/api/accounts/{account.id}/",
            {"name": "Existing Account"},
            format="json",
        )
        assert response.status_code == 400
        assert response.json()["error"] is True

    def test_delete_account_non_admin_own(self, user_client, org_a, regular_user):
        """Non-admin user can delete their own account."""
        account = Account.objects.create(name="My Account", org=org_a)
        # Manually set created_by since crum overrides it in tests
        Account.objects.filter(id=account.id).update(created_by=regular_user)
        response = user_client.delete(f"/api/accounts/{account.id}/")
        assert response.status_code == 200

    def test_delete_account_non_admin_others(
        self, user_client, org_a, admin_user, user_profile
    ):
        """Non-admin user cannot delete someone else's account."""
        account = Account.objects.create(
            name="Not My Account", org=org_a, created_by=admin_user
        )
        response = user_client.delete(f"/api/accounts/{account.id}/")
        assert response.status_code == 403

    def test_get_account_non_admin_not_assigned(
        self, user_client, org_a, admin_user, user_profile
    ):
        """Non-admin user not assigned to account gets 403."""
        account = Account.objects.create(
            name="Private Account", org=org_a, created_by=admin_user
        )
        response = user_client.get(f"/api/accounts/{account.id}/")
        assert response.status_code == 403

    def test_get_account_non_admin_assigned(
        self, user_client, org_a, admin_user, user_profile
    ):
        """Non-admin user assigned to account can see it."""
        account = Account.objects.create(
            name="Assigned Account", org=org_a, created_by=admin_user
        )
        account.assigned_to.add(user_profile)
        response = user_client.get(f"/api/accounts/{account.id}/")
        assert response.status_code == 200

    def test_update_account_non_admin_not_assigned(
        self, user_client, org_a, admin_user, user_profile
    ):
        """Non-admin user not assigned to account cannot update it."""
        account = Account.objects.create(
            name="Not Assigned", org=org_a, created_by=admin_user
        )
        response = user_client.put(
            f"/api/accounts/{account.id}/",
            {"name": "Not Assigned"},
            format="json",
        )
        assert response.status_code == 403

    def test_patch_account(self, admin_client, org_a):
        """Test partial update via PATCH."""
        account = Account.objects.create(name="Patch Me", city="OldCity", org=org_a)
        response = admin_client.patch(
            f"/api/accounts/{account.id}/",
            {"city": "NewCity"},
            format="json",
        )
        assert response.status_code == 200
        assert response.json()["error"] is False
        account.refresh_from_db()
        assert account.city == "NewCity"

    def test_patch_account_m2m_fields(self, admin_client, org_a, admin_profile):
        """Test PATCH with M2M fields (tags, assigned_to, teams, contacts)."""
        account = Account.objects.create(name="Patch M2M", org=org_a)
        tag = Tags.objects.create(name="Patch Tag", org=org_a)
        team = Teams.objects.create(name="Patch Team", description="PT", org=org_a)
        contact = Contact.objects.create(
            first_name="Patch", last_name="Contact", org=org_a
        )
        response = admin_client.patch(
            f"/api/accounts/{account.id}/",
            {
                "tags": [str(tag.id)],
                "assigned_to": [str(admin_profile.id)],
                "teams": [str(team.id)],
                "contacts": [str(contact.id)],
            },
            format="json",
        )
        assert response.status_code == 200
        assert account.tags.count() == 1
        assert account.assigned_to.count() == 1
        assert account.teams.count() == 1
        assert account.contacts.count() == 1

    def test_patch_account_clear_m2m(self, admin_client, org_a, admin_profile):
        """Test PATCH to clear M2M fields by sending empty lists."""
        account = Account.objects.create(name="Patch Clear", org=org_a)
        tag = Tags.objects.create(name="ClearTag", org=org_a)
        account.tags.add(tag)
        account.assigned_to.add(admin_profile)
        response = admin_client.patch(
            f"/api/accounts/{account.id}/",
            {"tags": [], "assigned_to": []},
            format="json",
        )
        assert response.status_code == 200
        assert account.tags.count() == 0
        assert account.assigned_to.count() == 0

    def test_patch_account_non_admin_forbidden(self, user_client, org_a, admin_user):
        """Non-admin user not assigned gets 403 on PATCH."""
        account = Account.objects.create(
            name="Patch Forbidden", org=org_a, created_by=admin_user
        )
        response = user_client.patch(
            f"/api/accounts/{account.id}/",
            {"city": "Nope"},
            format="json",
        )
        assert response.status_code == 403

    def test_add_comment_via_post(self, admin_client, org_a, admin_profile):
        """Test adding a comment to an account via POST on account detail."""
        account = Account.objects.create(
            name="Comment Post Account",
            org=org_a,
            created_by=admin_profile.user,
        )
        response = admin_client.post(
            f"/api/accounts/{account.id}/",
            {"comment": "This is a new comment"},
            format="json",
        )
        assert response.status_code == 200
        data = response.json()
        assert "comments" in data
        assert "attachments" in data
        assert "account_obj" in data

    def test_add_comment_non_admin_not_assigned(self, user_client, org_a, admin_user):
        """Non-admin user not assigned cannot add comment."""
        account = Account.objects.create(
            name="No Comment Account",
            org=org_a,
            created_by=admin_user,
        )
        response = user_client.post(
            f"/api/accounts/{account.id}/",
            {"comment": "Not allowed"},
            format="json",
        )
        assert response.status_code == 403


@pytest.mark.django_db
class TestAccountCommentView:
    """Tests for PUT/PATCH/DELETE /api/accounts/comment/<pk>/."""

    def _create_comment(self, account, profile, org):
        ct = ContentType.objects.get_for_model(Account)
        return Comment.objects.create(
            content_type=ct,
            object_id=account.id,
            comment="Test comment",
            commented_by=profile,
            org=org,
            created_by=profile.user,
            updated_by=profile.user,
        )

    def test_edit_comment(self, admin_client, admin_profile, org_a):
        account = Account.objects.create(name="Comment Account", org=org_a)
        comment = self._create_comment(account, admin_profile, org_a)
        response = admin_client.put(
            f"/api/accounts/comment/{comment.id}/",
            {"comment": "Updated"},
            format="json",
        )
        assert response.status_code == 200

    def test_delete_comment(self, admin_client, admin_profile, org_a):
        account = Account.objects.create(name="Comment Account", org=org_a)
        comment = self._create_comment(account, admin_profile, org_a)
        response = admin_client.delete(f"/api/accounts/comment/{comment.id}/")
        assert response.status_code == 200

    def test_edit_comment_no_permission(
        self, user_client, admin_profile, org_a, user_profile
    ):
        """Non-admin user who didn't create the comment cannot edit."""
        account = Account.objects.create(name="Comment Perm Account", org=org_a)
        comment = self._create_comment(account, admin_profile, org_a)
        response = user_client.put(
            f"/api/accounts/comment/{comment.id}/",
            {"comment": "Unauthorized Edit"},
            format="json",
        )
        assert response.status_code == 403

    def test_delete_comment_no_permission(
        self, user_client, admin_profile, org_a, user_profile
    ):
        """Non-admin user who didn't create the comment cannot delete."""
        account = Account.objects.create(name="Comment Del Perm", org=org_a)
        comment = self._create_comment(account, admin_profile, org_a)
        response = user_client.delete(f"/api/accounts/comment/{comment.id}/")
        assert response.status_code == 403

    def test_edit_comment_without_comment_text_is_400(
        self, admin_client, admin_profile, org_a
    ):
        """A missing `comment` is a bad request, not a permission problem.

        This asserted `status_code in (200, 403)`, which cannot fail whichever
        way the handler branched, and the comment underneath described the
        defect as the design: `if data.get("comment")` sat inside the
        authorization branch, so an empty body fell past it onto the 403 and
        the author was told they may not edit their own comment. This PUT was
        also the one built on a partial serializer, so simply dropping the
        guard would have answered 200 and saved nothing.
        """
        account = Account.objects.create(name="No Comment Text", org=org_a)
        comment = self._create_comment(account, admin_profile, org_a)
        response = admin_client.put(
            f"/api/accounts/comment/{comment.id}/",
            {},
            format="json",
        )
        assert response.status_code == 400
        assert "comment" in response.json()["errors"]

    def test_edit_comment_with_blank_text_is_400(
        self, admin_client, admin_profile, org_a
    ):
        """The other half of the same guard: `""` is falsy too."""
        account = Account.objects.create(name="Blank Comment Text", org=org_a)
        comment = self._create_comment(account, admin_profile, org_a)
        response = admin_client.put(
            f"/api/accounts/comment/{comment.id}/",
            {"comment": ""},
            format="json",
        )
        assert response.status_code == 400
        comment.refresh_from_db()
        assert comment.comment != ""

    def test_stranger_still_gets_403_for_an_empty_body(
        self, user_client, admin_profile, org_a, user_profile
    ):
        """Authorization is still checked before the body is."""
        account = Account.objects.create(name="Empty Body Perm", org=org_a)
        comment = self._create_comment(account, admin_profile, org_a)
        response = user_client.put(
            f"/api/accounts/comment/{comment.id}/",
            {},
            format="json",
        )
        assert response.status_code == 403

    def test_patch_comment(self, admin_client, admin_profile, org_a):
        """Test partial update of a comment via PATCH."""
        account = Account.objects.create(name="Patch Comment Account", org=org_a)
        comment = self._create_comment(account, admin_profile, org_a)
        response = admin_client.patch(
            f"/api/accounts/comment/{comment.id}/",
            {"comment": "Patched comment"},
            format="json",
        )
        assert response.status_code == 200
        assert response.json()["error"] is False

    def test_patch_comment_no_permission(
        self, user_client, admin_profile, org_a, user_profile
    ):
        """Non-admin user who didn't create the comment cannot PATCH."""
        account = Account.objects.create(name="Patch Perm Account", org=org_a)
        comment = self._create_comment(account, admin_profile, org_a)
        response = user_client.patch(
            f"/api/accounts/comment/{comment.id}/",
            {"comment": "Nope"},
            format="json",
        )
        assert response.status_code == 403

    def test_own_comment_can_edit(self, user_client, org_a, user_profile):
        """User who created the comment can edit it."""
        account = Account.objects.create(name="Own Comment Account", org=org_a)
        comment = self._create_comment(account, user_profile, org_a)
        response = user_client.put(
            f"/api/accounts/comment/{comment.id}/",
            {"comment": "Updated by author"},
            format="json",
        )
        assert response.status_code == 200

    def test_own_comment_can_delete(self, user_client, org_a, user_profile):
        """User who created the comment can delete it."""
        account = Account.objects.create(name="Own Del Comment Account", org=org_a)
        comment = self._create_comment(account, user_profile, org_a)
        response = user_client.delete(f"/api/accounts/comment/{comment.id}/")
        assert response.status_code == 200


@pytest.mark.django_db
class TestAccountAttachmentView:
    """Tests for DELETE /api/accounts/attachment/<pk>/."""

    def _create_attachment(self, account, user, org):
        ct = ContentType.objects.get_for_model(Account)
        return Attachments.objects.create(
            content_type=ct,
            object_id=account.id,
            file_name="test.txt",
            attachment=SimpleUploadedFile("test.txt", b"content"),
            org=org,
            created_by=user,
        )

    def test_delete_attachment(self, admin_client, admin_profile, org_a):
        account = Account.objects.create(name="Attachment Account", org=org_a)
        attachment = self._create_attachment(account, admin_profile.user, org_a)
        response = admin_client.delete(f"/api/accounts/attachment/{attachment.id}/")
        assert response.status_code == 200

    def test_delete_attachment_no_permission(
        self, user_client, admin_profile, org_a, user_profile
    ):
        """Non-admin who didn't create the attachment cannot delete."""
        account = Account.objects.create(name="Att Perm Account", org=org_a)
        attachment = self._create_attachment(account, admin_profile.user, org_a)
        response = user_client.delete(f"/api/accounts/attachment/{attachment.id}/")
        assert response.status_code == 403

    def test_delete_attachment_success_message(
        self, admin_client, admin_profile, org_a
    ):
        """Verify that successful attachment deletion returns proper message."""
        account = Account.objects.create(name="Att Msg Account", org=org_a)
        attachment = self._create_attachment(account, admin_profile.user, org_a)
        response = admin_client.delete(f"/api/accounts/attachment/{attachment.id}/")
        assert response.status_code == 200
        data = response.json()
        assert data["error"] is False
        assert "Deleted" in data["message"]


# ---------------------------------------------------------------------------
# Additional coverage tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAccountListFilters:
    """Cover filter parameters that were previously untested in AccountsListView."""

    def test_filter_by_assigned_to(self, admin_client, org_a, admin_profile):
        """Filter accounts by assigned_to profile id."""
        acct = Account.objects.create(name="Assigned Acct", org=org_a)
        acct.assigned_to.add(admin_profile)
        Account.objects.create(name="Unassigned Acct", org=org_a)

        response = admin_client.get(f"/api/accounts/?assigned_to={admin_profile.id}")
        assert response.status_code == 200
        names = [a["name"] for a in response.json()["active_accounts"]["open_accounts"]]
        assert "Assigned Acct" in names

    def test_filter_by_tags(self, admin_client, org_a):
        """?tags=<uuid> returns only accounts carrying that tag.

        Regression: previously the filter used `tags__in=params.get("tags")`,
        iterating the UUID string char-by-char. Now uses
        `tags__id__in=params.getlist("tags")`.
        """
        tag_vip = Tags.objects.create(name="VIP", org=org_a)
        tag_cold = Tags.objects.create(name="Cold", org=org_a)
        tagged = Account.objects.create(name="Tagged Acct", org=org_a)
        tagged.tags.add(tag_vip)
        other = Account.objects.create(name="Other Acct", org=org_a)
        other.tags.add(tag_cold)
        Account.objects.create(name="Untagged Acct", org=org_a)

        response = admin_client.get(f"/api/accounts/?tags={tag_vip.id}")
        assert response.status_code == 200
        names = {a["name"] for a in response.json()["active_accounts"]["open_accounts"]}
        assert names == {"Tagged Acct"}

    def test_filter_by_created_at_range(self, admin_client, org_a):
        """Filter accounts by created_at date range."""
        Account.objects.create(name="Date Range Acct", org=org_a)
        response = admin_client.get(
            "/api/accounts/?created_at__gte=2020-01-01&created_at__lte=2030-12-31"
        )
        assert response.status_code == 200
        names = [a["name"] for a in response.json()["active_accounts"]["open_accounts"]]
        assert "Date Range Acct" in names


@pytest.mark.django_db
class TestAccountCreateWithEmail:
    """Test create path with mocked email notification."""

    @patch("accounts.views.send_email_to_assigned_user.delay")
    def test_create_account_sends_email_to_assigned(
        self, mock_email, admin_client, org_a, admin_profile
    ):
        """Creating an account with assigned_to calls send_email_to_assigned_user."""
        response = admin_client.post(
            "/api/accounts/",
            {
                "name": "Email Test Account",
                "assigned_to": [str(admin_profile.id)],
            },
            format="json",
        )
        assert response.status_code == 200
        assert response.json()["error"] is False
        mock_email.assert_called_once()
        args = mock_email.call_args[0]
        assert admin_profile.id in args[0]  # recipients list

    @patch("accounts.views.send_email_to_assigned_user.delay")
    def test_create_account_with_contacts(self, mock_email, admin_client, org_a):
        """Creating an account with contacts m2m."""
        contact = Contact.objects.create(first_name="Jane", last_name="Doe", org=org_a)
        response = admin_client.post(
            "/api/accounts/",
            {
                "name": "Contact Create Acct",
                "contacts": [str(contact.id)],
            },
            format="json",
        )
        assert response.status_code == 200
        acct = Account.objects.get(name="Contact Create Acct")
        assert contact in acct.contacts.all()

    @patch("accounts.views.send_email_to_assigned_user.delay")
    def test_create_account_no_assigned_to_still_calls_email(
        self, mock_email, admin_client, org_a
    ):
        """Email task is called even with empty recipients list."""
        response = admin_client.post(
            "/api/accounts/", {"name": "No Assign Acct"}, format="json"
        )
        assert response.status_code == 200
        mock_email.assert_called_once()
        args = mock_email.call_args[0]
        assert args[0] == []  # empty recipients


@pytest.mark.django_db
class TestAccountUpdateWithEmail:
    """Test update (PUT) path with mocked email notification."""

    @patch("accounts.views.send_email_to_assigned_user.delay")
    def test_update_account_sends_email_on_new_assignee(
        self, mock_email, admin_client, org_a, admin_profile
    ):
        """PUT with new assigned_to triggers email for new recipients only."""
        account = Account.objects.create(name="Update Email Acct", org=org_a)
        response = admin_client.put(
            f"/api/accounts/{account.id}/",
            {
                "name": "Update Email Acct",
                "assigned_to": [str(admin_profile.id)],
            },
            format="json",
        )
        assert response.status_code == 200
        mock_email.assert_called_once()
        args = mock_email.call_args[0]
        assert admin_profile.id in args[0]

    @patch("accounts.views.send_email_to_assigned_user.delay")
    def test_update_account_with_contacts_as_json_list(
        self, mock_email, admin_client, org_a
    ):
        """PUT with contacts as a plain list of IDs."""
        account = Account.objects.create(name="JSON Contacts Acct", org=org_a)
        contact = Contact.objects.create(first_name="Bob", last_name="Smith", org=org_a)
        response = admin_client.put(
            f"/api/accounts/{account.id}/",
            {
                "name": "JSON Contacts Acct",
                "contacts": [str(contact.id)],
            },
            format="json",
        )
        assert response.status_code == 200
        account.refresh_from_db()
        assert contact in account.contacts.all()


@pytest.mark.django_db
class TestAccountDetailUsersMention:
    """Cover the users_mention branches in the GET detail view."""

    def test_users_mention_no_created_by(self, user_client, org_a, user_profile):
        """Non-admin assigned to account with no created_by gets empty users_mention."""
        account = Account.objects.create(name="No Creator Acct", org=org_a)
        account.assigned_to.add(user_profile)
        response = user_client.get(f"/api/accounts/{account.id}/")
        assert response.status_code == 200
        data = response.json()
        assert "users_mention" in data
        assert data["users_mention"] == []


@pytest.mark.django_db
class TestAccountEmailModel:
    """Cover AccountEmail and AccountEmailLog model save/str methods."""

    def test_account_email_str(self, org_a):
        account = Account.objects.create(name="Email Parent", org=org_a)
        email = AccountEmail.objects.create(
            from_account=account,
            message_subject="Test Subject",
            from_email="test@example.com",
            org=org_a,
        )
        assert str(email) == "Test Subject"

    def test_account_email_save_infers_org_from_account(self, org_a):
        """AccountEmail.save() should infer org from from_account if not set."""
        account = Account.objects.create(name="Org Infer Acct", org=org_a)
        email = AccountEmail(
            from_account=account,
            message_subject="Inferred Org",
            from_email="test@example.com",
        )
        email.save()
        assert email.org_id == org_a.id

    def test_account_email_save_raises_without_org(self):
        """AccountEmail.save() raises ValidationError when org cannot be determined."""
        email = AccountEmail(
            message_subject="No Org",
            from_email="test@example.com",
        )
        with pytest.raises(ValidationError, match="Organization is required"):
            email.save()

    def test_account_email_log_str(self, org_a):
        account = Account.objects.create(name="Log Acct", org=org_a)
        email = AccountEmail.objects.create(
            from_account=account,
            message_subject="Log Subject",
            from_email="test@example.com",
            org=org_a,
        )
        contact = Contact.objects.create(
            first_name="Log", last_name="Contact", org=org_a
        )
        log = AccountEmailLog.objects.create(
            email=email, contact=contact, is_sent=True, org=org_a
        )
        assert str(log) == "Log Subject"

    def test_account_email_log_save_infers_org_from_email(self, org_a):
        """AccountEmailLog.save() should infer org from email if not set."""
        account = Account.objects.create(name="Log Infer Acct", org=org_a)
        email = AccountEmail.objects.create(
            from_account=account,
            message_subject="Log Infer",
            from_email="test@example.com",
            org=org_a,
        )
        contact = Contact.objects.create(first_name="Log", last_name="Infer", org=org_a)
        log = AccountEmailLog(email=email, contact=contact, is_sent=False)
        log.save()
        assert log.org_id == org_a.id

    def test_account_email_log_save_infers_org_from_contact(self, org_a):
        """AccountEmailLog.save() should infer org from contact if email has no org."""
        contact = Contact.objects.create(
            first_name="Contact", last_name="Org", org=org_a
        )
        log = AccountEmailLog(contact=contact, is_sent=False)
        log.save()
        assert log.org_id == org_a.id

    def test_account_email_log_save_raises_without_org(self):
        """AccountEmailLog.save() raises ValidationError when org can't be determined."""
        log = AccountEmailLog(is_sent=False)
        with pytest.raises(ValidationError, match="Organization is required"):
            log.save()


@pytest.mark.django_db
class TestAccountModel:
    """Cover Account model __str__ method."""

    def test_account_str(self, org_a):
        account = Account.objects.create(name="Str Test Acct", org=org_a)
        assert str(account) == "Str Test Acct"


@pytest.mark.django_db
class TestAccountSerializerValidation:
    """Cover serializer validation branches."""

    def test_validate_name_update_same_name(self, admin_client, org_a):
        """Updating an account to keep the same name should succeed."""
        account = Account.objects.create(name="Same Name Acct", org=org_a)
        response = admin_client.put(
            f"/api/accounts/{account.id}/",
            {"name": "Same Name Acct"},
            format="json",
        )
        assert response.status_code == 200

    def test_validate_name_update_to_existing_name(self, admin_client, org_a):
        """Updating an account to another existing name should fail."""
        Account.objects.create(name="Existing Name", org=org_a)
        account = Account.objects.create(name="Other Name", org=org_a)
        response = admin_client.put(
            f"/api/accounts/{account.id}/",
            {"name": "Existing Name"},
            format="json",
        )
        assert response.status_code == 400

    def test_validate_name_update_to_new_name(self, admin_client, org_a):
        """Updating an account to a brand new name should succeed."""
        account = Account.objects.create(name="Old Name For Update", org=org_a)
        response = admin_client.put(
            f"/api/accounts/{account.id}/",
            {"name": "Brand New Name"},
            format="json",
        )
        assert response.status_code == 200

    def test_email_serializer_validate_message_body_valid(self):
        """EmailSerializer.validate_message_body with balanced brackets."""
        from accounts.serializer import EmailSerializer

        serializer = EmailSerializer()
        result = serializer.validate_message_body("Hello {name}, welcome!")
        assert result == "Hello {name}, welcome!"

    def test_email_serializer_validate_message_body_unbalanced_open(self):
        """EmailSerializer.validate_message_body with unbalanced open bracket."""
        from rest_framework import serializers as drf_serializers

        from accounts.serializer import EmailSerializer

        serializer = EmailSerializer()
        with pytest.raises(drf_serializers.ValidationError):
            serializer.validate_message_body("Hello {name, welcome!")

    def test_email_serializer_validate_message_body_unbalanced_close(self):
        """EmailSerializer.validate_message_body with close bracket before open."""
        from rest_framework import serializers as drf_serializers

        from accounts.serializer import EmailSerializer

        serializer = EmailSerializer()
        with pytest.raises(drf_serializers.ValidationError):
            serializer.validate_message_body("Hello }name{, welcome!")

    def test_account_create_serializer_default_currency(self, admin_client, org_a):
        """AccountCreateSerializer.create() should default currency from org when annual_revenue is set."""

        # The serializer create method checks for currency default - test via API
        # We need annual_revenue but no currency
        response = admin_client.post(
            "/api/accounts/",
            {
                "name": "Currency Default Acct",
                "annual_revenue": "100000.00",
            },
            format="json",
        )
        # Should succeed regardless of whether org has default_currency set
        assert response.status_code == 200


@pytest.mark.django_db
class TestAccountSerializerMethods:
    """Cover AccountSerializer SerializerMethodField methods."""

    def test_country_display(self, org_a):
        """AccountSerializer.get_country_display returns display name."""
        from accounts.serializer import AccountSerializer

        account = Account.objects.create(name="Country Acct", org=org_a, country="US")
        serializer = AccountSerializer(account)
        assert serializer.data["country_display"] is not None

    def test_country_display_none(self, org_a):
        """AccountSerializer.get_country_display returns None when no country."""
        from accounts.serializer import AccountSerializer

        account = Account.objects.create(name="No Country Acct", org=org_a)
        serializer = AccountSerializer(account)
        assert serializer.data["country_display"] is None

    def test_get_cases(self, org_a):
        """AccountSerializer.get_cases returns case data."""
        from accounts.serializer import AccountSerializer

        account = Account.objects.create(name="Cases Acct", org=org_a)
        serializer = AccountSerializer(account)
        assert isinstance(serializer.data["cases"], list)

    def test_get_tasks(self, org_a):
        """AccountSerializer.get_tasks returns task data."""
        from accounts.serializer import AccountSerializer

        account = Account.objects.create(name="Tasks Acct", org=org_a)
        serializer = AccountSerializer(account)
        assert isinstance(serializer.data["tasks"], list)

    def test_get_opportunities(self, org_a):
        """AccountSerializer.get_opportunities returns opportunity data."""
        from accounts.serializer import AccountSerializer

        account = Account.objects.create(name="Opps Acct", org=org_a)
        serializer = AccountSerializer(account)
        assert isinstance(serializer.data["opportunities"], list)
