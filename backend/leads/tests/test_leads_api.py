import uuid
from unittest.mock import patch

import pytest
from django.contrib.contenttypes.models import ContentType
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.utils import timezone

from common.models import Attachments, Comment, Tags, Teams
from contacts.models import Contact
from leads.models import Lead

LEADS_LIST_URL = "/api/leads/"


def _detail_url(pk):
    return f"/api/leads/{pk}/"


def _comment_url(pk):
    return f"/api/leads/comment/{pk}/"


def _attachment_url(pk):
    return f"/api/leads/attachment/{pk}/"


def _set_rls(org):
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.current_org', %s, false)", [str(org.id)])


@pytest.mark.django_db
class TestLeadListView:
    """Tests for GET /api/leads/ and POST /api/leads/."""

    def test_list_leads(self, admin_client, admin_user, org_a):
        Lead.objects.create(
            first_name="John",
            last_name="Doe",
            email="john@example.com",
            created_by=admin_user,
            org=org_a,
        )
        response = admin_client.get("/api/leads/")
        assert response.status_code == 200
        data = response.json()
        assert "open_leads" in data

    def test_create_lead(self, admin_client, org_a):
        response = admin_client.post(
            "/api/leads/",
            {
                "first_name": "Jane",
                "last_name": "Smith",
                "email": "jane@example.com",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["error"] is False
        assert Lead.objects.filter(email="jane@example.com", org=org_a).exists()

    def test_create_lead_ignores_is_sample_in_payload(self, admin_client, org_a):
        """`is_sample` is server-set only (see leads/models.py) and it is the
        sole key common.packs.applier.clear_sample_data uses to decide what
        to delete. LeadCreateSerializer.Meta.fields never lists it, so DRF
        silently drops it from validated_data rather than erroring. This
        proves that drop actually holds and a user cannot mark their own
        lead "sample" (and have it swept up by a future clear) by adding the
        field to the request body.
        """
        response = admin_client.post(
            "/api/leads/",
            {
                "first_name": "Not",
                "last_name": "Sample",
                "email": "not.sample@example.com",
                "is_sample": True,
            },
        )
        assert response.status_code == 200
        lead = Lead.objects.get(email="not.sample@example.com", org=org_a)
        assert lead.is_sample is False

    def test_create_lead_invalid_data(self, admin_client):
        response = admin_client.post(
            "/api/leads/",
            {"email": "not-a-valid-email"},
        )
        assert response.status_code == 400
        data = response.json()
        assert data["error"] is True

    def test_create_lead_unauthenticated(self, unauthenticated_client):
        """`HasOrgContext` refuses before anything reads the body.

        Was already failing before the v2 leads work: it asserted that the
        request *raises* `PermissionDenied` out of the client, but DRF handles
        the exception and renders it, so nothing propagates. Assert the
        response the caller actually receives.
        """
        response = unauthenticated_client.post(
            "/api/leads/",
            {"first_name": "Test", "last_name": "Lead", "email": "t@t.com"},
        )

        assert response.status_code == 403
        assert not Lead.objects.filter(email="t@t.com").exists()

    def test_org_isolation(self, org_b_client, admin_user, org_a):
        Lead.objects.create(
            first_name="Private",
            last_name="Lead",
            email="private@org-a.com",
            created_by=admin_user,
            org=org_a,
        )
        response = org_b_client.get("/api/leads/")
        assert response.status_code == 200
        data = response.json()
        open_leads_list = data["open_leads"]["open_leads"]
        emails = [lead["email"] for lead in open_leads_list]
        assert "private@org-a.com" not in emails

    def test_create_lead_with_all_optional_fields(self, admin_client, org_a):
        """Creating a lead with all optional fields populates them correctly."""
        payload = {
            "first_name": "Full",
            "last_name": "Fields",
            "email": "full@example.com",
            "phone": "+1234567890",
            "status": "assigned",
            "source": "call",
            "address_line": "123 Main St",
            "city": "Springfield",
            "state": "IL",
            "postcode": "62701",
            "country": "US",
            "description": "A test lead with all fields",
            "company_name": "Acme Inc",
            "website": "https://acme.com",
            "opportunity_amount": "5000.00",
            "probability": 50,
        }
        response = admin_client.post(LEADS_LIST_URL, payload, format="json")
        assert response.status_code == 200
        assert response.data["error"] is False
        lead = Lead.objects.get(email="full@example.com")
        assert lead.city == "Springfield"
        assert lead.company_name == "Acme Inc"
        assert lead.phone == "+1234567890"
        assert lead.description == "A test lead with all fields"

    @patch("leads.views.lead_views.send_email_to_assigned_user.delay")
    def test_create_lead_with_tags(self, mock_email, admin_client, org_a):
        """Creating a lead with tags associates them properly."""
        _set_rls(org_a)
        tag = Tags.objects.create(name="VIP", org=org_a)
        payload = {
            "first_name": "Tagged",
            "last_name": "Lead",
            "email": "tagged@example.com",
            "tags": [str(tag.id)],
        }
        response = admin_client.post(LEADS_LIST_URL, payload, format="json")
        assert response.status_code == 200
        lead = Lead.objects.get(email="tagged@example.com")
        assert tag in lead.tags.all()

    @patch("leads.views.lead_views.send_email_to_assigned_user.delay")
    def test_create_lead_with_assigned_to(
        self, mock_email, admin_client, admin_profile, org_a
    ):
        """Creating a lead with assigned_to sets users and sends email."""
        payload = {
            "first_name": "Assigned",
            "last_name": "Lead",
            "email": "assigned@example.com",
            "assigned_to": [str(admin_profile.id)],
        }
        response = admin_client.post(LEADS_LIST_URL, payload, format="json")
        assert response.status_code == 200
        lead = Lead.objects.get(email="assigned@example.com")
        assert admin_profile in lead.assigned_to.all()
        mock_email.assert_called_once()

    @patch("leads.views.lead_views.send_email_to_assigned_user.delay")
    def test_create_lead_with_teams(self, mock_email, admin_client, admin_user, org_a):
        """Creating a lead with teams associates them properly."""
        _set_rls(org_a)
        team = Teams.objects.create(name="Sales Team", created_by=admin_user, org=org_a)
        payload = {
            "first_name": "Teamed",
            "last_name": "Lead",
            "email": "teamed@example.com",
            "teams": [str(team.id)],
        }
        response = admin_client.post(LEADS_LIST_URL, payload, format="json")
        assert response.status_code == 200
        lead = Lead.objects.get(email="teamed@example.com")
        assert team in lead.teams.all()

    def test_list_leads_with_search_filter(self, admin_client, admin_user, org_a):
        """Filtering leads by search query works correctly."""
        Lead.objects.create(
            first_name="Alpha",
            last_name="Beta",
            email="alpha@example.com",
            company_name="AlphaCorp",
            created_by=admin_user,
            org=org_a,
        )
        Lead.objects.create(
            first_name="Gamma",
            last_name="Delta",
            email="gamma@example.com",
            created_by=admin_user,
            org=org_a,
        )
        response = admin_client.get(LEADS_LIST_URL, {"search": "Alpha"})
        assert response.status_code == 200
        data = response.json()
        open_leads_list = data["open_leads"]["open_leads"]
        emails = [lead["email"] for lead in open_leads_list]
        assert "alpha@example.com" in emails

    def test_list_leads_with_status_filter(self, admin_client, admin_user, org_a):
        """Filtering by status returns only matching leads."""
        Lead.objects.create(
            first_name="Open",
            last_name="Lead",
            email="open@example.com",
            status="assigned",
            created_by=admin_user,
            org=org_a,
        )
        Lead.objects.create(
            first_name="Closed",
            last_name="Lead",
            email="closed@example.com",
            status="closed",
            created_by=admin_user,
            org=org_a,
        )
        response = admin_client.get(LEADS_LIST_URL, {"status": "assigned"})
        assert response.status_code == 200

    def test_list_leads_with_source_filter(self, admin_client, admin_user, org_a):
        """Filtering by source works."""
        Lead.objects.create(
            first_name="Web",
            last_name="Lead",
            email="web@example.com",
            source="call",
            created_by=admin_user,
            org=org_a,
        )
        response = admin_client.get(LEADS_LIST_URL, {"source": "call"})
        assert response.status_code == 200

    def test_list_leads_with_city_filter(self, admin_client, admin_user, org_a):
        """Filtering by city works."""
        Lead.objects.create(
            first_name="City",
            last_name="Lead",
            email="city@example.com",
            city="Denver",
            created_by=admin_user,
            org=org_a,
        )
        response = admin_client.get(LEADS_LIST_URL, {"city": "Denver"})
        assert response.status_code == 200

    def test_list_leads_with_email_filter(self, admin_client, admin_user, org_a):
        """Filtering by email works."""
        Lead.objects.create(
            first_name="Email",
            last_name="Lead",
            email="specific@company.com",
            created_by=admin_user,
            org=org_a,
        )
        response = admin_client.get(LEADS_LIST_URL, {"email": "specific@company"})
        assert response.status_code == 200

    def test_list_leads_with_tags_filter(self, admin_client, admin_user, org_a):
        """?tags=<uuid> returns only leads carrying that tag.

        Regression: previously this filter used `tags__in=params.get("tags")`,
        which iterates the UUID string char-by-char and silently matched
        nothing (or every row, depending on DB). Now uses
        `tags__id__in=params.getlist("tags")`.
        """
        tag_vip = Tags.objects.create(name="VIP", org=org_a)
        tag_cold = Tags.objects.create(name="Cold", org=org_a)
        tagged = Lead.objects.create(
            first_name="Tagged",
            last_name="Lead",
            email="tagged-vip@example.com",
            created_by=admin_user,
            org=org_a,
        )
        tagged.tags.add(tag_vip)
        other = Lead.objects.create(
            first_name="Other",
            last_name="Lead",
            email="tagged-cold@example.com",
            created_by=admin_user,
            org=org_a,
        )
        other.tags.add(tag_cold)
        Lead.objects.create(
            first_name="None",
            last_name="Lead",
            email="untagged@example.com",
            created_by=admin_user,
            org=org_a,
        )

        response = admin_client.get(LEADS_LIST_URL, {"tags": str(tag_vip.id)})
        assert response.status_code == 200
        emails = {lead["email"] for lead in response.json()["open_leads"]["open_leads"]}
        assert emails == {"tagged-vip@example.com"}

    def test_list_leads_with_assigned_to_filter(
        self, admin_client, admin_user, admin_profile, user_profile, org_a
    ):
        """?assigned_to=<profile-id> filters by the named assignee.

        Regression: the filter used `params.get` (single string) with
        `assigned_to__id__in`, which iterated the UUID string and matched
        nothing. Now uses `params.getlist`.
        """
        admin_assigned = Lead.objects.create(
            first_name="Admin",
            last_name="Assignee",
            email="adminassigned@example.com",
            created_by=admin_user,
            org=org_a,
        )
        admin_assigned.assigned_to.add(admin_profile)
        user_assigned = Lead.objects.create(
            first_name="User",
            last_name="Assignee",
            email="userassigned@example.com",
            created_by=admin_user,
            org=org_a,
        )
        user_assigned.assigned_to.add(user_profile)

        response = admin_client.get(
            LEADS_LIST_URL, {"assigned_to": str(admin_profile.id)}
        )
        assert response.status_code == 200
        emails = {lead["email"] for lead in response.json()["open_leads"]["open_leads"]}
        assert emails == {"adminassigned@example.com"}

    def test_list_leads_regular_user_sees_assigned(
        self, user_client, admin_user, org_a, user_profile
    ):
        """Non-admin user sees leads they are assigned to."""
        lead_assigned = Lead.objects.create(
            first_name="Assigned",
            last_name="Lead",
            email="assigneduser@example.com",
            created_by=admin_user,
            org=org_a,
        )
        lead_assigned.assigned_to.add(user_profile)
        Lead.objects.create(
            first_name="NotAssigned",
            last_name="Lead",
            email="notassigned@example.com",
            created_by=admin_user,
            org=org_a,
        )
        response = user_client.get(LEADS_LIST_URL)
        assert response.status_code == 200
        data = response.json()
        open_leads = data["open_leads"]["open_leads"]
        emails = [lead["email"] for lead in open_leads]
        assert "assigneduser@example.com" in emails
        assert "notassigned@example.com" not in emails

    def test_list_leads_context_keys(self, admin_client, org_a):
        """Response context contains expected metadata keys."""
        response = admin_client.get(LEADS_LIST_URL)
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert "source" in data
        assert "tags" in data
        assert "users" in data
        assert "countries" in data
        assert "industries" in data
        assert "close_leads" in data
        assert "per_page" in data

    def test_list_leads_close_date_filter(self, admin_client, admin_user, org_a):
        """Filtering by close_date range works."""
        Lead.objects.create(
            first_name="Close",
            last_name="Lead",
            email="closedate@example.com",
            close_date="2025-06-01",
            created_by=admin_user,
            org=org_a,
        )
        response = admin_client.get(
            LEADS_LIST_URL,
            {"close_date__gte": "2025-01-01", "close_date__lte": "2025-12-31"},
        )
        assert response.status_code == 200

    def test_list_leads_created_at_filter(self, admin_client, admin_user, org_a):
        """Filtering by created_at range works."""
        Lead.objects.create(
            first_name="Dated",
            last_name="Lead",
            email="dated@example.com",
            created_by=admin_user,
            org=org_a,
        )
        response = admin_client.get(
            LEADS_LIST_URL,
            {"created_at__gte": "2020-01-01", "created_at__lte": "2030-12-31"},
        )
        assert response.status_code == 200


@pytest.mark.django_db
class TestLeadDetailView:
    """Tests for GET/PUT/DELETE /api/leads/<pk>/."""

    def test_get_lead_detail(self, admin_client, admin_user, org_a):
        lead = Lead.objects.create(
            first_name="Detail",
            last_name="Lead",
            email="detail@example.com",
            created_by=admin_user,
            org=org_a,
        )
        response = admin_client.get(f"/api/leads/{lead.id}/")
        assert response.status_code == 200
        data = response.json()
        assert "lead_obj" in data

    def test_update_lead(self, admin_client, admin_user, org_a):
        lead = Lead.objects.create(
            first_name="Old",
            last_name="Name",
            email="update@example.com",
            created_by=admin_user,
            org=org_a,
        )
        response = admin_client.put(
            f"/api/leads/{lead.id}/",
            {
                "first_name": "New",
                "last_name": "Name",
                "email": "update@example.com",
            },
            format="json",
        )
        assert response.status_code == 200
        data = response.json()
        assert data["error"] is False

    def test_delete_lead(self, admin_client, admin_user, org_a):
        lead = Lead.objects.create(
            first_name="Delete",
            last_name="Me",
            email="delete@example.com",
            created_by=admin_user,
            org=org_a,
        )
        response = admin_client.delete(f"/api/leads/{lead.id}/")
        assert response.status_code == 200
        assert not Lead.objects.filter(id=lead.id).exists()

    def test_get_lead_cross_org(self, org_b_client, admin_user, org_a):
        lead = Lead.objects.create(
            first_name="Cross",
            last_name="Org",
            email="cross@org-a.com",
            created_by=admin_user,
            org=org_a,
        )
        response = org_b_client.get(f"/api/leads/{lead.id}/")
        assert response.status_code == 404

    def test_get_lead_detail_context_keys(self, admin_client, admin_user, org_a):
        """Detail response contains all expected context keys."""
        lead = Lead.objects.create(
            first_name="Context",
            last_name="Lead",
            email="context@example.com",
            created_by=admin_user,
            org=org_a,
        )
        response = admin_client.get(_detail_url(lead.id))
        assert response.status_code == 200
        data = response.json()
        assert "lead_obj" in data
        assert "attachments" in data
        assert "comments" in data
        assert "users_mention" in data
        assert "assigned_data" in data
        assert "users" in data
        assert "source" in data
        assert "status" in data
        assert "teams" in data
        assert "countries" in data

    def test_update_lead_with_tags(self, admin_client, admin_user, org_a):
        """PUT with tags clears old tags and sets new ones."""
        _set_rls(org_a)
        tag1 = Tags.objects.create(name="Old Tag", org=org_a)
        tag2 = Tags.objects.create(name="New Tag", org=org_a)
        lead = Lead.objects.create(
            first_name="Tag",
            last_name="Lead",
            email="taglead@example.com",
            created_by=admin_user,
            org=org_a,
        )
        lead.tags.add(tag1)

        response = admin_client.put(
            _detail_url(lead.id),
            {
                "first_name": "Tag",
                "last_name": "Lead",
                "email": "taglead@example.com",
                "tags": [str(tag2.id)],
            },
            format="json",
        )
        assert response.status_code == 200
        lead.refresh_from_db()
        assert tag2 in lead.tags.all()
        assert tag1 not in lead.tags.all()

    @patch("leads.views.lead_views.send_email_to_assigned_user.delay")
    def test_update_lead_with_assigned_to(
        self, mock_email, admin_client, admin_user, admin_profile, org_a
    ):
        """PUT with assigned_to updates assignees and emails new ones."""
        lead = Lead.objects.create(
            first_name="Assign",
            last_name="Lead",
            email="assignlead@example.com",
            created_by=admin_user,
            org=org_a,
        )
        response = admin_client.put(
            _detail_url(lead.id),
            {
                "first_name": "Assign",
                "last_name": "Lead",
                "email": "assignlead@example.com",
                "assigned_to": [str(admin_profile.id)],
            },
            format="json",
        )
        assert response.status_code == 200
        lead.refresh_from_db()
        assert admin_profile in lead.assigned_to.all()
        mock_email.assert_called_once()

    @patch("leads.views.lead_views.send_email_to_assigned_user.delay")
    def test_update_lead_with_teams(self, mock_email, admin_client, admin_user, org_a):
        """PUT with teams updates team associations."""
        _set_rls(org_a)
        team = Teams.objects.create(name="Dev Team", created_by=admin_user, org=org_a)
        lead = Lead.objects.create(
            first_name="Team",
            last_name="Lead",
            email="teamlead@example.com",
            created_by=admin_user,
            org=org_a,
        )
        response = admin_client.put(
            _detail_url(lead.id),
            {
                "first_name": "Team",
                "last_name": "Lead",
                "email": "teamlead@example.com",
                "teams": [str(team.id)],
            },
            format="json",
        )
        assert response.status_code == 200
        lead.refresh_from_db()
        assert team in lead.teams.all()

    def test_update_lead_invalid_data(self, admin_client, admin_user, org_a):
        """PUT with invalid data returns 400."""
        lead = Lead.objects.create(
            first_name="Invalid",
            last_name="Lead",
            email="invalidput@example.com",
            created_by=admin_user,
            org=org_a,
        )
        response = admin_client.put(
            _detail_url(lead.id),
            {"email": "bad-email"},
            format="json",
        )
        assert response.status_code == 400

    def test_delete_lead_non_admin_non_creator_forbidden(
        self, user_client, admin_user, org_a, user_profile
    ):
        """Non-admin user who did not create the lead cannot delete it."""
        lead = Lead.objects.create(
            first_name="Protected",
            last_name="Lead",
            email="protected@example.com",
            created_by=admin_user,
            org=org_a,
        )
        response = user_client.delete(_detail_url(lead.id))
        assert response.status_code == 403

    def test_get_lead_detail_admin_with_assigned_users(
        self, admin_client, admin_user, admin_profile, org_a
    ):
        """Admin can view detail of a lead that has assigned users."""
        lead = Lead.objects.create(
            first_name="Assigned",
            last_name="Detail",
            email="assigneddetail@example.com",
            created_by=admin_user,
            org=org_a,
        )
        lead.assigned_to.add(admin_profile)
        response = admin_client.get(_detail_url(lead.id))
        assert response.status_code == 200
        data = response.json()
        assert "lead_obj" in data
        assert len(data["assigned_data"]) == 1

    def test_patch_lead_partial_update(self, admin_client, admin_user, org_a):
        """PATCH allows partial field updates without requiring all fields."""
        lead = Lead.objects.create(
            first_name="Partial",
            last_name="Lead",
            email="partial@example.com",
            status="assigned",
            created_by=admin_user,
            org=org_a,
        )
        response = admin_client.patch(
            _detail_url(lead.id),
            {"first_name": "Updated"},
            format="json",
        )
        assert response.status_code == 200
        assert response.data["error"] is False
        lead.refresh_from_db()
        assert lead.first_name == "Updated"
        assert lead.last_name == "Lead"  # unchanged

    def test_patch_lead_ignores_is_sample_in_payload(
        self, admin_client, admin_user, org_a
    ):
        """Mirrors test_create_lead_ignores_is_sample_in_payload for the
        update path, PUT/PATCH share LeadCreateSerializer with create, so
        this is the "and False" half of proving the mass-assignment door is
        shut: an existing real lead cannot be flipped to is_sample=True and
        then destroyed by clear_sample_data.
        """
        lead = Lead.objects.create(
            first_name="Real",
            last_name="Lead",
            email="real.lead@example.com",
            created_by=admin_user,
            org=org_a,
        )
        response = admin_client.patch(
            _detail_url(lead.id),
            {"is_sample": True},
            format="json",
        )
        assert response.status_code == 200
        lead.refresh_from_db()
        assert lead.is_sample is False

    def test_patch_lead_with_tags(self, admin_client, admin_user, org_a):
        """PATCH with tags replaces existing tags."""
        _set_rls(org_a)
        tag1 = Tags.objects.create(name="PatchOld", org=org_a)
        tag2 = Tags.objects.create(name="PatchNew", org=org_a)
        lead = Lead.objects.create(
            first_name="PatchTag",
            last_name="Lead",
            email="patchtag@example.com",
            created_by=admin_user,
            org=org_a,
        )
        lead.tags.add(tag1)
        response = admin_client.patch(
            _detail_url(lead.id),
            {"tags": [str(tag2.id)]},
            format="json",
        )
        assert response.status_code == 200
        lead.refresh_from_db()
        assert tag2 in lead.tags.all()
        assert tag1 not in lead.tags.all()

    def test_patch_lead_clear_tags(self, admin_client, admin_user, org_a):
        """PATCH with empty tags list clears all tags."""
        _set_rls(org_a)
        tag = Tags.objects.create(name="ClearMe", org=org_a)
        lead = Lead.objects.create(
            first_name="ClearTag",
            last_name="Lead",
            email="cleartag@example.com",
            created_by=admin_user,
            org=org_a,
        )
        lead.tags.add(tag)
        response = admin_client.patch(
            _detail_url(lead.id),
            {"tags": []},
            format="json",
        )
        assert response.status_code == 200
        lead.refresh_from_db()
        assert lead.tags.count() == 0

    @patch("leads.views.lead_views.send_email_to_assigned_user.delay")
    def test_patch_lead_with_assigned_to(
        self, mock_email, admin_client, admin_user, admin_profile, org_a
    ):
        """PATCH with assigned_to updates assignees and sends email to new ones."""
        lead = Lead.objects.create(
            first_name="PatchAssign",
            last_name="Lead",
            email="patchassign@example.com",
            created_by=admin_user,
            org=org_a,
        )
        response = admin_client.patch(
            _detail_url(lead.id),
            {"assigned_to": [str(admin_profile.id)]},
            format="json",
        )
        assert response.status_code == 200
        lead.refresh_from_db()
        assert admin_profile in lead.assigned_to.all()
        mock_email.assert_called_once()

    def test_patch_lead_non_admin_not_creator_forbidden(
        self, user_client, admin_user, org_a, user_profile
    ):
        """Non-admin non-creator/non-assignee gets 403 on PATCH."""
        lead = Lead.objects.create(
            first_name="ForbidPatch",
            last_name="Lead",
            email="forbidpatch@example.com",
            created_by=admin_user,
            org=org_a,
        )
        response = user_client.patch(
            _detail_url(lead.id),
            {"first_name": "Hacked"},
            format="json",
        )
        assert response.status_code == 403

    def test_patch_lead_invalid_data(self, admin_client, admin_user, org_a):
        """PATCH with invalid data returns 400."""
        lead = Lead.objects.create(
            first_name="BadPatch",
            last_name="Lead",
            email="badpatch@example.com",
            created_by=admin_user,
            org=org_a,
        )
        response = admin_client.patch(
            _detail_url(lead.id),
            {"email": "not-an-email"},
            format="json",
        )
        assert response.status_code == 400


@pytest.mark.django_db
class TestLeadCommentView:
    """Tests for POST /api/leads/<pk>/ (adding comments) and comment CRUD."""

    def test_add_comment_to_lead(self, admin_client, admin_user, org_a):
        lead = Lead.objects.create(
            first_name="Comment",
            last_name="Lead",
            email="comment@example.com",
            created_by=admin_user,
            org=org_a,
        )
        response = admin_client.post(
            f"/api/leads/{lead.id}/",
            {"comment": "This is a test comment"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "lead_obj" in data
        assert "comments" in data

    def test_add_comment_creates_comment_object(self, admin_client, admin_user, org_a):
        """POSTing a comment actually creates a Comment object in the database."""
        lead = Lead.objects.create(
            first_name="Comm",
            last_name="Lead",
            email="comm@example.com",
            created_by=admin_user,
            org=org_a,
        )
        admin_client.post(
            _detail_url(lead.id),
            {"comment": "DB comment test"},
            format="json",
        )
        ct = ContentType.objects.get_for_model(Lead)
        assert Comment.objects.filter(
            content_type=ct, object_id=lead.id, comment="DB comment test"
        ).exists()

    def test_add_comment_non_admin_not_assigned_forbidden(
        self, user_client, admin_user, org_a, user_profile
    ):
        """Non-admin user not assigned/creator gets 403 when adding comment."""
        lead = Lead.objects.create(
            first_name="Forbidden",
            last_name="Comment",
            email="forbidcomment@example.com",
            created_by=admin_user,
            org=org_a,
        )
        response = user_client.post(
            _detail_url(lead.id),
            {"comment": "Should be forbidden"},
            format="json",
        )
        assert response.status_code == 403

    def test_update_comment_put(self, admin_client, admin_user, admin_profile, org_a):
        """Admin can update a comment via PUT."""
        _set_rls(org_a)
        lead = Lead.objects.create(
            first_name="Edit",
            last_name="Comment",
            email="editcomment@example.com",
            created_by=admin_user,
            org=org_a,
        )
        ct = ContentType.objects.get_for_model(Lead)
        comment = Comment.objects.create(
            content_type=ct,
            object_id=lead.id,
            comment="Original comment",
            commented_by=admin_profile,
            org=org_a,
        )
        response = admin_client.put(
            _comment_url(comment.id),
            {"comment": "Updated comment"},
            format="json",
        )
        assert response.status_code == 200
        assert response.data["error"] is False

    def test_update_comment_patch(self, admin_client, admin_user, admin_profile, org_a):
        """Admin can partially update a comment via PATCH."""
        _set_rls(org_a)
        lead = Lead.objects.create(
            first_name="Patch",
            last_name="Comment",
            email="patchcomment@example.com",
            created_by=admin_user,
            org=org_a,
        )
        ct = ContentType.objects.get_for_model(Lead)
        comment = Comment.objects.create(
            content_type=ct,
            object_id=lead.id,
            comment="Original",
            commented_by=admin_profile,
            org=org_a,
        )
        response = admin_client.patch(
            _comment_url(comment.id),
            {"comment": "Patched"},
            format="json",
        )
        assert response.status_code == 200
        assert response.data["error"] is False

    def test_delete_comment(self, admin_client, admin_user, admin_profile, org_a):
        """Admin can delete a comment."""
        _set_rls(org_a)
        lead = Lead.objects.create(
            first_name="Del",
            last_name="Comment",
            email="delcomment@example.com",
            created_by=admin_user,
            org=org_a,
        )
        ct = ContentType.objects.get_for_model(Lead)
        comment = Comment.objects.create(
            content_type=ct,
            object_id=lead.id,
            comment="Delete me",
            commented_by=admin_profile,
            org=org_a,
        )
        response = admin_client.delete(_comment_url(comment.id))
        assert response.status_code == 200
        assert response.data["error"] is False
        assert not Comment.objects.filter(id=comment.id).exists()

    def test_update_comment_non_admin_non_author_forbidden(
        self, user_client, admin_user, admin_profile, org_a, user_profile
    ):
        """Non-admin user who didn't author the comment gets 403 on PUT."""
        _set_rls(org_a)
        lead = Lead.objects.create(
            first_name="Forbid",
            last_name="CommentEdit",
            email="forbidcedit@example.com",
            created_by=admin_user,
            org=org_a,
        )
        ct = ContentType.objects.get_for_model(Lead)
        comment = Comment.objects.create(
            content_type=ct,
            object_id=lead.id,
            comment="Not yours",
            commented_by=admin_profile,
            org=org_a,
        )
        response = user_client.put(
            _comment_url(comment.id),
            {"comment": "Hacked"},
            format="json",
        )
        assert response.status_code == 403

    def test_delete_comment_non_admin_non_author_forbidden(
        self, user_client, admin_user, admin_profile, org_a, user_profile
    ):
        """Non-admin user who didn't author the comment gets 403 on DELETE."""
        _set_rls(org_a)
        lead = Lead.objects.create(
            first_name="Forbid",
            last_name="CommentDel",
            email="forbidcdel@example.com",
            created_by=admin_user,
            org=org_a,
        )
        ct = ContentType.objects.get_for_model(Lead)
        comment = Comment.objects.create(
            content_type=ct,
            object_id=lead.id,
            comment="Protected",
            commented_by=admin_profile,
            org=org_a,
        )
        response = user_client.delete(_comment_url(comment.id))
        assert response.status_code == 403


@pytest.mark.django_db
class TestLeadAttachmentView:
    """Tests for DELETE /api/leads/attachment/<pk>/."""

    def test_delete_attachment_admin(self, admin_client, admin_user, org_a):
        """Admin can delete an attachment."""
        _set_rls(org_a)
        lead = Lead.objects.create(
            first_name="Attach",
            last_name="Lead",
            email="attach@example.com",
            created_by=admin_user,
            org=org_a,
        )
        ct = ContentType.objects.get_for_model(Lead)
        attachment = Attachments.objects.create(
            content_type=ct,
            object_id=lead.id,
            file_name="test.txt",
            attachment="attachments/test.txt",
            created_by=admin_user,
            org=org_a,
        )
        response = admin_client.delete(_attachment_url(attachment.id))
        assert response.status_code == 200
        assert response.data["error"] is False
        assert not Attachments.objects.filter(id=attachment.id).exists()

    def test_delete_attachment_non_admin_non_creator_forbidden(
        self, user_client, admin_user, org_a, user_profile
    ):
        """Non-admin user who didn't create the attachment gets 403."""
        _set_rls(org_a)
        lead = Lead.objects.create(
            first_name="AttachForbid",
            last_name="Lead",
            email="attachforbid@example.com",
            created_by=admin_user,
            org=org_a,
        )
        ct = ContentType.objects.get_for_model(Lead)
        attachment = Attachments.objects.create(
            content_type=ct,
            object_id=lead.id,
            file_name="forbidden.txt",
            attachment="attachments/forbidden.txt",
            created_by=admin_user,
            org=org_a,
        )
        response = user_client.delete(_attachment_url(attachment.id))
        assert response.status_code == 403


@pytest.mark.django_db
class TestLeadUniqueEmail:
    """Tests for unique email constraint per organization."""

    def test_duplicate_email_same_org(self, admin_client, org_a):
        admin_client.post(
            "/api/leads/",
            {
                "first_name": "First",
                "last_name": "Lead",
                "email": "dupe@example.com",
            },
        )
        response = admin_client.post(
            "/api/leads/",
            {
                "first_name": "Second",
                "last_name": "Lead",
                "email": "dupe@example.com",
            },
        )
        assert response.status_code == 400

    def test_same_email_different_org(
        self, admin_client, org_b_client, admin_user, user_b, org_a, org_b
    ):
        resp1 = admin_client.post(
            "/api/leads/",
            {
                "first_name": "Org A",
                "last_name": "Lead",
                "email": "shared@example.com",
            },
        )
        assert resp1.status_code == 200

        resp2 = org_b_client.post(
            "/api/leads/",
            {
                "first_name": "Org B",
                "last_name": "Lead",
                "email": "shared@example.com",
            },
        )
        assert resp2.status_code == 200


# ================================================================
# Coverage-targeted tests for lead_views.py uncovered lines
# ================================================================


@pytest.mark.django_db
class TestLeadListViewFilters:
    """Tests targeting uncovered filter lines 59-106 in LeadListView.get_context_data."""

    def test_filter_by_name(self, admin_client, admin_user, org_a):
        """Filter by name matches first_name OR last_name icontains (lines 59-62)."""
        Lead.objects.create(
            first_name="NameFilter",
            last_name="NameFilter",
            email="namefilter@example.com",
            created_by=admin_user,
            org=org_a,
        )
        response = admin_client.get(LEADS_LIST_URL, {"name": "NameFilter"})
        assert response.status_code == 200
        open_leads = response.json()["open_leads"]["open_leads"]
        emails = [lead["email"] for lead in open_leads]
        assert "namefilter@example.com" in emails

    def test_filter_by_name_matches_first_or_last_name(
        self, admin_client, admin_user, org_a
    ):
        """The name filter matches a partial first or last name (issue #709)."""
        _set_rls(org_a)
        first_name_match = Lead.objects.create(
            first_name="John",
            last_name="Smith",
            email="john.smith@example.com",
            created_by=admin_user,
            org=org_a,
        )
        last_name_match = Lead.objects.create(
            first_name="Jane",
            last_name="Johnson",
            email="jane.johnson@example.com",
            created_by=admin_user,
            org=org_a,
        )
        Lead.objects.create(
            first_name="Alice",
            last_name="Walker",
            email="alice.walker@example.com",
            created_by=admin_user,
            org=org_a,
        )

        response = admin_client.get(LEADS_LIST_URL, {"name": "john"})

        assert response.status_code == 200
        returned_ids = {
            lead["id"] for lead in response.json()["open_leads"]["open_leads"]
        }
        assert returned_ids == {str(first_name_match.id), str(last_name_match.id)}

    def test_filter_by_salutation(self, admin_client, admin_user, org_a):
        """Filter by salutation (lines 63-66)."""
        Lead.objects.create(
            first_name="Sal",
            last_name="Test",
            email="salutation@example.com",
            salutation="Dr",
            created_by=admin_user,
            org=org_a,
        )
        response = admin_client.get(LEADS_LIST_URL, {"salutation": "Dr"})
        assert response.status_code == 200

    def test_filter_by_assigned_to(
        self, admin_client, admin_user, admin_profile, org_a
    ):
        """Filter by assigned_to returns only leads assigned to the given profile."""
        lead = Lead.objects.create(
            first_name="AssignFilter",
            last_name="Test",
            email="assignfilter@example.com",
            created_by=admin_user,
            org=org_a,
        )
        lead.assigned_to.add(admin_profile)
        response = admin_client.get(
            LEADS_LIST_URL, {"assigned_to": str(admin_profile.id)}
        )
        assert response.status_code == 200
        emails = {lead["email"] for lead in response.json()["open_leads"]["open_leads"]}
        assert "assignfilter@example.com" in emails

    def test_filter_by_tags(self, admin_client, admin_user, org_a):
        """Filter by tags returns only leads carrying that tag."""
        _set_rls(org_a)
        tag = Tags.objects.create(name="FilterTag", org=org_a)
        lead = Lead.objects.create(
            first_name="TagFilter",
            last_name="Test",
            email="tagfilter@example.com",
            created_by=admin_user,
            org=org_a,
        )
        lead.tags.add(tag)
        response = admin_client.get(LEADS_LIST_URL, {"tags": str(tag.id)})
        assert response.status_code == 200
        emails = {lead["email"] for lead in response.json()["open_leads"]["open_leads"]}
        assert "tagfilter@example.com" in emails

    def test_filter_by_rating(self, admin_client, admin_user, org_a):
        """Filter by rating (lines 81-82)."""
        Lead.objects.create(
            first_name="Rating",
            last_name="Test",
            email="rating@example.com",
            rating="HOT",
            created_by=admin_user,
            org=org_a,
        )
        response = admin_client.get(LEADS_LIST_URL, {"rating": "HOT"})
        assert response.status_code == 200

    def test_closed_leads_pagination_offset(self, admin_client, admin_user, org_a):
        """Closed leads have pagination offset computed (lines 133-136)."""
        Lead.objects.create(
            first_name="ClosedLead",
            last_name="One",
            email="closed1@example.com",
            status="closed",
            created_by=admin_user,
            org=org_a,
        )
        response = admin_client.get(LEADS_LIST_URL)
        assert response.status_code == 200
        data = response.json()
        assert "close_leads" in data
        close_data = data["close_leads"]
        assert "leads_count" in close_data
        assert "offset" in close_data


@pytest.mark.django_db
class TestLeadCreateWithM2M:
    """Tests targeting uncovered create lines: contacts, attachments, teams, assigned_to, emails."""

    @patch("leads.views.lead_views.send_email_to_assigned_user.delay")
    def test_create_lead_with_contacts(
        self, mock_email, admin_client, admin_user, org_a
    ):
        """Creating a lead with contacts associates them (lines 242-246)."""
        _set_rls(org_a)
        contact = Contact.objects.create(
            first_name="Linked",
            last_name="Contact",
            email="linkedcontact@example.com",
            created_by=admin_user,
            org=org_a,
        )
        payload = {
            "first_name": "WithContact",
            "last_name": "Lead",
            "email": "withcontact@example.com",
            "contacts": [str(contact.id)],
        }
        response = admin_client.post(LEADS_LIST_URL, payload, format="json")
        assert response.status_code == 200
        lead = Lead.objects.get(email="withcontact@example.com")
        assert contact in lead.contacts.all()

    @patch("leads.views.lead_views.send_email_to_assigned_user.delay")
    def test_create_lead_with_attachment(self, mock_email, admin_client, org_a):
        """Creating a lead with an attachment file (lines 248-255)."""
        test_file = SimpleUploadedFile(
            "test_doc.txt", b"file content", content_type="text/plain"
        )
        payload = {
            "first_name": "Attached",
            "last_name": "Lead",
            "email": "attached@example.com",
            "lead_attachment": test_file,
        }
        response = admin_client.post(LEADS_LIST_URL, payload, format="multipart")
        assert response.status_code == 200
        lead = Lead.objects.get(email="attached@example.com")
        ct = ContentType.objects.get_for_model(Lead)
        assert Attachments.objects.filter(content_type=ct, object_id=lead.id).exists()

    @patch("leads.views.lead_views.send_email_to_assigned_user.delay")
    def test_create_lead_email_notification_sent(
        self, mock_email, admin_client, admin_profile, org_a
    ):
        """Email notification sent to assigned users on create (lines 286-292)."""
        payload = {
            "first_name": "Notify",
            "last_name": "Lead",
            "email": "notify@example.com",
            "assigned_to": [str(admin_profile.id)],
        }
        response = admin_client.post(LEADS_LIST_URL, payload, format="json")
        assert response.status_code == 200
        mock_email.assert_called_once()
        call_args = mock_email.call_args
        assert admin_profile.id in call_args[0][0]

    @patch("leads.views.lead_views.send_email_to_assigned_user.delay")
    @patch("leads.services.convert_lead_to_account")
    def test_create_lead_with_status_converted(
        self, mock_convert, mock_email, admin_client, admin_profile, org_a
    ):
        """Creating a lead with status=converted triggers conversion (lines 294-318)."""
        from unittest.mock import MagicMock

        mock_account = MagicMock()
        mock_account.id = "fake-account-id"
        mock_contact = MagicMock()
        mock_contact.id = "fake-contact-id"
        mock_opportunity = MagicMock()
        mock_opportunity.id = "fake-opp-id"
        mock_convert.return_value = (mock_account, mock_contact, mock_opportunity)

        payload = {
            "first_name": "Converted",
            "last_name": "Lead",
            "email": "converted@example.com",
            "status": "converted",
            "assigned_to": [str(admin_profile.id)],
        }
        response = admin_client.post(LEADS_LIST_URL, payload, format="json")
        assert response.status_code == 200
        data = response.json()
        assert data["message"] == "Lead Converted Successfully"
        assert "account_id" in data
        mock_convert.assert_called_once()

    @patch("leads.views.lead_views.send_email_to_assigned_user.delay")
    def test_create_lead_with_teams_as_json_string(
        self, mock_email, admin_client, admin_user, org_a
    ):
        """Teams can be passed as a JSON string (line 260)."""
        import json

        _set_rls(org_a)
        team = Teams.objects.create(name="StringTeam", created_by=admin_user, org=org_a)
        payload = {
            "first_name": "StringTeam",
            "last_name": "Lead",
            "email": "stringteam@example.com",
            "teams": json.dumps([str(team.id)]),
        }
        response = admin_client.post(LEADS_LIST_URL, payload, format="json")
        assert response.status_code == 200
        lead = Lead.objects.get(email="stringteam@example.com")
        assert team in lead.teams.all()

    @patch("leads.views.lead_views.send_email_to_assigned_user.delay")
    def test_create_lead_with_assigned_to_as_json_string(
        self, mock_email, admin_client, admin_profile, org_a
    ):
        """assigned_to can be passed as a JSON string (line 272)."""
        import json

        payload = {
            "first_name": "StringAssign",
            "last_name": "Lead",
            "email": "stringassign@example.com",
            "assigned_to": json.dumps([str(admin_profile.id)]),
        }
        response = admin_client.post(LEADS_LIST_URL, payload, format="json")
        assert response.status_code == 200
        lead = Lead.objects.get(email="stringassign@example.com")
        assert admin_profile in lead.assigned_to.all()


@pytest.mark.django_db
class TestLeadDetailViewNonAdmin:
    """Tests targeting uncovered lines in detail view for non-admin users (lines 341-345, 373-376, 393, 402-405)."""

    def _create_lead_with_creator(self, user, org, **kwargs):
        """Create a lead and force the created_by field via update() to bypass BaseModel.save()."""
        lead = Lead.objects.create(
            org=org,
            **kwargs,
        )
        # BaseModel.save() overrides created_by based on crum. Force it via update.
        Lead.objects.filter(id=lead.id).update(created_by=user)
        lead.refresh_from_db()
        return lead

    def test_detail_non_admin_assigned_exercises_non_creator_branch(
        self, user_client, admin_user, org_a, user_profile
    ):
        """An assigned non-admin can open a lead somebody else created.

        This used to assert `pytest.raises(AttributeError, match="username")`,
        pinning a crash in place as though it were the specification: the
        mention list read `created_by.username`, and this User model sets
        `USERNAME_FIELD = "email"` and has no `username` field. Being assigned
        a lead and then getting a 500 when you open it is not a branch worth
        covering. It is the bug.
        """
        lead = self._create_lead_with_creator(
            admin_user,
            org_a,
            first_name="Assigned",
            last_name="View",
            email="assignedview@example.com",
        )
        lead.assigned_to.add(user_profile)

        response = user_client.get(_detail_url(lead.id))

        assert response.status_code == 200
        assert response.json()["users_mention"] == [{"user__email": admin_user.email}]

    def test_detail_non_admin_not_assigned_not_creator_gets_error(
        self, user_client, admin_user, org_a, user_profile
    ):
        """A non-admin who is neither assigned nor the creator gets a 403.

        This used to assert `pytest.raises(TypeError, match="not JSON
        serializable")`, and the docstring described the mechanism as though it
        were intended: `get_context_data` returned a `Response` on refusal and
        `get()` wrapped it in a second one, so the 403 rendered as a 500. The
        refusal is now raised, so it arrives as the status it always meant.
        """
        lead = self._create_lead_with_creator(
            admin_user,
            org_a,
            first_name="Forbidden",
            last_name="Detail",
            email="forbiddendetail@example.com",
        )

        assert user_client.get(_detail_url(lead.id)).status_code == 403

    def test_detail_non_admin_creator_assigned_exercises_creator_branch(
        self, user_client, regular_user, org_a, user_profile
    ):
        """Non-admin creator who is also assigned exercises lines 341-342,
        375-376, 401-402 (users_mention for non-admin creator).
        request.profile.user == lead.created_by triggers the creator path at line 341
        and the users_mention at line 375-376."""
        lead = self._create_lead_with_creator(
            regular_user,
            org_a,
            first_name="Creator",
            last_name="View",
            email="creatorview@example.com",
        )
        # Also assign the user so they pass the first permission check
        lead.assigned_to.add(user_profile)
        response = user_client.get(_detail_url(lead.id))
        assert response.status_code == 200
        data = response.json()
        assert "users_mention" in data
        assert "lead_obj" in data


@pytest.mark.django_db
class TestLeadUpdateWithContacts:
    """Tests targeting uncovered update lines for contacts, teams, tags, conversion."""

    @patch("leads.views.lead_views.send_email_to_assigned_user.delay")
    def test_update_lead_with_contacts(
        self, mock_email, admin_client, admin_user, org_a
    ):
        """PUT with contacts updates contact associations (lines 608-620)."""
        _set_rls(org_a)
        contact = Contact.objects.create(
            first_name="Update",
            last_name="Contact",
            email="updatecontact@example.com",
            created_by=admin_user,
            org=org_a,
        )
        lead = Lead.objects.create(
            first_name="UpdateC",
            last_name="Lead",
            email="updateclead@example.com",
            created_by=admin_user,
            org=org_a,
        )
        response = admin_client.put(
            _detail_url(lead.id),
            {
                "first_name": "UpdateC",
                "last_name": "Lead",
                "email": "updateclead@example.com",
                "contacts": [str(contact.id)],
            },
            format="json",
        )
        assert response.status_code == 200
        lead.refresh_from_db()
        assert contact in lead.contacts.all()

    @patch("leads.views.lead_views.send_email_to_assigned_user.delay")
    def test_update_lead_with_attachment(
        self, mock_email, admin_client, admin_user, org_a
    ):
        """PUT with lead_attachment creates attachment (lines 598-605)."""
        lead = Lead.objects.create(
            first_name="UpAttach",
            last_name="Lead",
            email="upattach@example.com",
            created_by=admin_user,
            org=org_a,
        )
        test_file = SimpleUploadedFile(
            "update.txt", b"update content", content_type="text/plain"
        )
        response = admin_client.put(
            _detail_url(lead.id),
            {
                "first_name": "UpAttach",
                "last_name": "Lead",
                "email": "upattach@example.com",
                "lead_attachment": test_file,
            },
            format="multipart",
        )
        assert response.status_code == 200
        ct = ContentType.objects.get_for_model(Lead)
        assert Attachments.objects.filter(content_type=ct, object_id=lead.id).exists()

    @patch("leads.views.lead_views.send_email_to_assigned_user.delay")
    @patch("leads.services.convert_lead_to_account")
    def test_update_lead_status_converted(
        self, mock_convert, mock_email, admin_client, admin_user, admin_profile, org_a
    ):
        """PUT with status=converted triggers conversion (lines 665-690)."""
        from unittest.mock import MagicMock

        mock_account = MagicMock()
        mock_account.id = "acct-id"
        mock_contact = MagicMock()
        mock_contact.id = "cont-id"
        mock_opp = MagicMock()
        mock_opp.id = "opp-id"
        mock_convert.return_value = (mock_account, mock_contact, mock_opp)

        lead = Lead.objects.create(
            first_name="ConvertPut",
            last_name="Lead",
            email="convertput@example.com",
            created_by=admin_user,
            org=org_a,
        )
        lead.assigned_to.add(admin_profile)
        response = admin_client.put(
            _detail_url(lead.id),
            {
                "first_name": "ConvertPut",
                "last_name": "Lead",
                "email": "convertput@example.com",
                "status": "converted",
                "assigned_to": [str(admin_profile.id)],
            },
            format="json",
        )
        assert response.status_code == 200
        data = response.json()
        assert data["message"] == "Lead Converted Successfully"
        assert "account_id" in data
        mock_convert.assert_called_once()
        # Email should be sent for converted leads (lines 673-679)
        mock_email.assert_called()

    @patch("leads.views.lead_views.send_email_to_assigned_user.delay")
    def test_update_lead_email_notification_new_assignees(
        self, mock_email, admin_client, admin_user, admin_profile, org_a, user_profile
    ):
        """PUT sends email only to newly assigned users (lines 652-663)."""
        lead = Lead.objects.create(
            first_name="NotifyUpdate",
            last_name="Lead",
            email="notifyupdate@example.com",
            created_by=admin_user,
            org=org_a,
        )
        # Already assigned to admin_profile
        lead.assigned_to.add(admin_profile)
        # Now assign to user_profile as well - only user_profile should get email
        response = admin_client.put(
            _detail_url(lead.id),
            {
                "first_name": "NotifyUpdate",
                "last_name": "Lead",
                "email": "notifyupdate@example.com",
                "assigned_to": [str(admin_profile.id), str(user_profile.id)],
            },
            format="json",
        )
        assert response.status_code == 200
        mock_email.assert_called_once()
        # Check only the new user is in recipients
        call_args = mock_email.call_args
        assert user_profile.id in call_args[0][0]


@pytest.mark.django_db
class TestLeadPatchConversion:
    """Tests for PATCH conversion and M2M handling (lines 750-866)."""

    @patch("leads.views.lead_views.send_email_to_assigned_user.delay")
    @patch("leads.services.convert_lead_to_account")
    def test_patch_lead_status_converted(
        self, mock_convert, mock_email, admin_client, admin_user, admin_profile, org_a
    ):
        """PATCH with status=converted triggers conversion (lines 750-775)."""
        from unittest.mock import MagicMock

        mock_account = MagicMock()
        mock_account.id = "patch-acct"
        mock_contact = MagicMock()
        mock_contact.id = "patch-cont"
        mock_opp = MagicMock()
        mock_opp.id = "patch-opp"
        mock_convert.return_value = (mock_account, mock_contact, mock_opp)

        lead = Lead.objects.create(
            first_name="PatchConvert",
            last_name="Lead",
            email="patchconvert@example.com",
            created_by=admin_user,
            org=org_a,
        )
        lead.assigned_to.add(admin_profile)
        response = admin_client.patch(
            _detail_url(lead.id),
            {"status": "converted"},
            format="json",
        )
        assert response.status_code == 200
        data = response.json()
        assert data["message"] == "Lead Converted Successfully"
        mock_convert.assert_called_once()
        mock_email.assert_called_once()

    @patch("leads.views.lead_views.send_email_to_assigned_user.delay")
    @patch("leads.services.convert_lead_to_account")
    def test_patch_lead_is_converted_flag(
        self, mock_convert, mock_email, admin_client, admin_user, org_a
    ):
        """PATCH with is_converted=True triggers conversion (line 750)."""
        from unittest.mock import MagicMock

        mock_account = MagicMock()
        mock_account.id = "is-conv-acct"
        mock_contact = None
        mock_opp = None
        mock_convert.return_value = (mock_account, mock_contact, mock_opp)

        lead = Lead.objects.create(
            first_name="IsConverted",
            last_name="Lead",
            email="isconverted@example.com",
            created_by=admin_user,
            org=org_a,
        )
        response = admin_client.patch(
            _detail_url(lead.id),
            {"is_converted": True},
            format="json",
        )
        assert response.status_code == 200
        data = response.json()
        assert data["message"] == "Lead Converted Successfully"
        assert data["contact_id"] is None
        assert data["opportunity_id"] is None

    def test_patch_lead_with_contacts(self, admin_client, admin_user, org_a):
        """PATCH with contacts updates contact associations (lines 809-823)."""
        _set_rls(org_a)
        contact = Contact.objects.create(
            first_name="PatchC",
            last_name="Contact",
            email="patchccontact@example.com",
            created_by=admin_user,
            org=org_a,
        )
        lead = Lead.objects.create(
            first_name="PatchContact",
            last_name="Lead",
            email="patchcontactlead@example.com",
            created_by=admin_user,
            org=org_a,
        )
        response = admin_client.patch(
            _detail_url(lead.id),
            {"contacts": [str(contact.id)]},
            format="json",
        )
        assert response.status_code == 200
        lead.refresh_from_db()
        assert contact in lead.contacts.all()

    def test_patch_lead_with_teams(self, admin_client, admin_user, org_a):
        """PATCH with teams updates team associations (lines 825-839)."""
        _set_rls(org_a)
        team = Teams.objects.create(
            name="PatchTeamLead", created_by=admin_user, org=org_a
        )
        lead = Lead.objects.create(
            first_name="PatchTeamLead",
            last_name="Lead",
            email="patchteamlead@example.com",
            created_by=admin_user,
            org=org_a,
        )
        response = admin_client.patch(
            _detail_url(lead.id),
            {"teams": [str(team.id)]},
            format="json",
        )
        assert response.status_code == 200
        lead.refresh_from_db()
        assert team in lead.teams.all()

    def test_patch_lead_clear_contacts(self, admin_client, admin_user, org_a):
        """PATCH with empty contacts clears all contacts."""
        _set_rls(org_a)
        contact = Contact.objects.create(
            first_name="ClearC",
            last_name="Contact",
            email="clearc@example.com",
            created_by=admin_user,
            org=org_a,
        )
        lead = Lead.objects.create(
            first_name="ClearContacts",
            last_name="Lead",
            email="clearcontacts@example.com",
            created_by=admin_user,
            org=org_a,
        )
        lead.contacts.add(contact)
        response = admin_client.patch(
            _detail_url(lead.id),
            {"contacts": []},
            format="json",
        )
        assert response.status_code == 200
        lead.refresh_from_db()
        assert lead.contacts.count() == 0

    def test_patch_lead_clear_teams(self, admin_client, admin_user, org_a):
        """PATCH with empty teams clears all teams."""
        _set_rls(org_a)
        team = Teams.objects.create(name="ClearTeam", created_by=admin_user, org=org_a)
        lead = Lead.objects.create(
            first_name="ClearTeams",
            last_name="Lead",
            email="clearteams@example.com",
            created_by=admin_user,
            org=org_a,
        )
        lead.teams.add(team)
        response = admin_client.patch(
            _detail_url(lead.id),
            {"teams": []},
            format="json",
        )
        assert response.status_code == 200
        lead.refresh_from_db()
        assert lead.teams.count() == 0


@pytest.mark.django_db
class TestLeadCommentAttachmentOnDetail:
    """Tests for POST to detail endpoint (comment + attachment, lines 515-525)."""

    def test_add_comment_with_attachment(self, admin_client, admin_user, org_a):
        """POSTing a comment with lead_attachment creates both (lines 515-525)."""
        lead = Lead.objects.create(
            first_name="CommentAttach",
            last_name="Lead",
            email="commentattach@example.com",
            created_by=admin_user,
            org=org_a,
        )
        test_file = SimpleUploadedFile(
            "comment_attach.txt", b"comment file", content_type="text/plain"
        )
        response = admin_client.post(
            _detail_url(lead.id),
            {"comment": "Comment with file", "lead_attachment": test_file},
            format="multipart",
        )
        assert response.status_code == 200
        ct = ContentType.objects.get_for_model(Lead)
        assert Comment.objects.filter(
            content_type=ct, object_id=lead.id, comment="Comment with file"
        ).exists()
        assert Attachments.objects.filter(content_type=ct, object_id=lead.id).exists()

    def test_post_comment_org_mismatch(
        self, org_b_client, admin_user, org_a, profile_b
    ):
        """POSTing a comment on another tenant's lead is a 404, not a 403.

        A 403 here answered "that lead exists, but it isn't yours", which is a
        cross-tenant existence leak: an outsider could probe ids and learn
        which ones are real. Every other method on this view already answers
        404 through ``get_object``, and ``put`` documents that as deliberate.
        """
        lead = Lead.objects.create(
            first_name="OrgMismatch",
            last_name="Comment",
            email="orgmismatchcomment@example.com",
            created_by=admin_user,
            org=org_a,
        )
        response = org_b_client.post(
            _detail_url(lead.id),
            {"comment": "Wrong org"},
            format="json",
        )
        assert response.status_code == 404

    def test_post_comment_unknown_lead(self, admin_client, org_a):
        """An id that matches no lead is a 404, not a crash.

        ``Lead.objects.get(pk=pk)`` raised ``DoesNotExist`` straight out of the
        handler, so a well-formed id for a lead that had been deleted returned
        a 500 to the caller and logged an exception on the server.
        """
        missing = uuid.uuid4()
        response = admin_client.post(
            _detail_url(missing),
            {"comment": "No such lead"},
            format="json",
        )
        assert response.status_code == 404

    def test_put_lead_org_mismatch(self, org_b_client, admin_user, org_a, profile_b):
        """PUT on another tenant's lead is a 404, same as GET and POST."""
        lead = Lead.objects.create(
            first_name="OrgMismatchPut",
            last_name="Lead",
            email="orgmismatchput@example.com",
            created_by=admin_user,
            org=org_a,
        )
        response = org_b_client.put(
            _detail_url(lead.id),
            {
                "first_name": "Hacked",
                "last_name": "Lead",
                "email": "orgmismatchput@example.com",
            },
            format="json",
        )
        # `in (403, 404)` here passed whichever way the view behaved, so it
        # could not have caught the POST handler drifting to 403.
        assert response.status_code == 404

    def test_patch_lead_org_mismatch(self, org_b_client, admin_user, org_a, profile_b):
        """PATCH on another tenant's lead is a 404, same as GET and POST."""
        lead = Lead.objects.create(
            first_name="OrgMismatchPatch",
            last_name="Lead",
            email="orgmismatchpatch@example.com",
            created_by=admin_user,
            org=org_a,
        )
        response = org_b_client.patch(
            _detail_url(lead.id),
            {"first_name": "Hacked"},
            format="json",
        )
        assert response.status_code == 404


@pytest.mark.django_db
class TestLeadModelProperties:
    """Tests for Lead model properties (leads/models.py uncovered lines)."""

    def test_str_with_salutation(self, admin_user, org_a):
        """__str__ includes salutation when present."""
        lead = Lead.objects.create(
            salutation="Dr",
            first_name="John",
            last_name="Smith",
            email="drjohn@example.com",
            created_by=admin_user,
            org=org_a,
        )
        assert str(lead) == "Dr John Smith"

    def test_str_without_name(self, admin_user, org_a):
        """__str__ falls back to 'Lead <id>' when no name parts."""
        lead = Lead.objects.create(
            email="noname@example.com",
            created_by=admin_user,
            org=org_a,
        )
        assert str(lead) == f"Lead {lead.id}"

    def test_days_since_last_contact_with_last_contacted(self, admin_user, org_a):
        """days_since_last_contact uses last_contacted when set."""
        import datetime

        lead = Lead.objects.create(
            first_name="LastContact",
            last_name="Test",
            email="lastcontact@example.com",
            last_contacted=timezone.localdate() - datetime.timedelta(days=10),
            created_by=admin_user,
            org=org_a,
        )
        assert lead.days_since_last_contact == 10

    def test_days_since_last_contact_without_last_contacted(self, admin_user, org_a):
        """days_since_last_contact falls back to created_at."""
        lead = Lead.objects.create(
            first_name="NoLastContact",
            last_name="Test",
            email="nolastcontact@example.com",
            created_by=admin_user,
            org=org_a,
        )
        assert lead.days_since_last_contact >= 0

    def test_is_stale_true(self, admin_user, org_a):
        """is_stale returns True when >30 days without contact."""
        import datetime

        lead = Lead.objects.create(
            first_name="Stale",
            last_name="Lead",
            email="stale@example.com",
            status="assigned",
            last_contacted=timezone.localdate() - datetime.timedelta(days=31),
            created_by=admin_user,
            org=org_a,
        )
        assert lead.is_stale is True

    def test_is_stale_false_for_converted(self, admin_user, org_a):
        """is_stale returns False for converted leads."""
        import datetime

        lead = Lead.objects.create(
            first_name="ConvertedStale",
            last_name="Lead",
            email="convertedstale@example.com",
            status="converted",
            last_contacted=timezone.localdate() - datetime.timedelta(days=60),
            created_by=admin_user,
            org=org_a,
        )
        assert lead.is_stale is False

    def test_is_stale_false_for_closed(self, admin_user, org_a):
        """is_stale returns False for closed leads."""
        import datetime

        lead = Lead.objects.create(
            first_name="ClosedStale",
            last_name="Lead",
            email="closedstale@example.com",
            status="closed",
            last_contacted=timezone.localdate() - datetime.timedelta(days=60),
            created_by=admin_user,
            org=org_a,
        )
        assert lead.is_stale is False

    def test_days_until_follow_up(self, admin_user, org_a):
        """days_until_follow_up returns days until next follow-up."""
        import datetime

        lead = Lead.objects.create(
            first_name="FollowUp",
            last_name="Lead",
            email="followup@example.com",
            next_follow_up=timezone.localdate() + datetime.timedelta(days=5),
            created_by=admin_user,
            org=org_a,
        )
        assert lead.days_until_follow_up == 5

    def test_days_until_follow_up_none(self, admin_user, org_a):
        """days_until_follow_up returns None when no follow-up set."""
        lead = Lead.objects.create(
            first_name="NoFollowUp",
            last_name="Lead",
            email="nofollowup@example.com",
            created_by=admin_user,
            org=org_a,
        )
        assert lead.days_until_follow_up is None

    def test_is_follow_up_overdue_true(self, admin_user, org_a):
        """is_follow_up_overdue returns True when follow-up date passed."""
        import datetime

        lead = Lead.objects.create(
            first_name="Overdue",
            last_name="Lead",
            email="overdue@example.com",
            next_follow_up=timezone.localdate() - datetime.timedelta(days=1),
            created_by=admin_user,
            org=org_a,
        )
        assert lead.is_follow_up_overdue is True

    def test_is_follow_up_overdue_false(self, admin_user, org_a):
        """is_follow_up_overdue returns False when no follow-up set."""
        lead = Lead.objects.create(
            first_name="NotOverdue",
            last_name="Lead",
            email="notoverdue@example.com",
            created_by=admin_user,
            org=org_a,
        )
        assert lead.is_follow_up_overdue is False

    def test_clean_converted_without_email_raises(self, admin_user, org_a):
        """clean() raises ValidationError when status=converted but no email."""
        from django.core.exceptions import ValidationError

        lead = Lead(
            first_name="NoEmail",
            last_name="Converted",
            status="converted",
            created_by=admin_user,
            org=org_a,
        )
        with pytest.raises(ValidationError) as exc_info:
            lead.clean()
        assert "email" in exc_info.value.message_dict

    def test_lead_stage_str(self, admin_user, org_a):
        """LeadStage __str__ returns formatted pipeline-stage name."""
        from leads.models import LeadPipeline, LeadStage

        _set_rls(org_a)
        pipeline = LeadPipeline.objects.create(name="TestPipeline", org=org_a)
        stage = LeadStage.objects.create(
            pipeline=pipeline, name="Qualification", order=1, org=org_a
        )
        assert str(stage) == "TestPipeline - Qualification"

    def test_lead_stage_save_auto_org(self, admin_user, org_a):
        """LeadStage.save() auto-sets org from pipeline if not set."""
        from leads.models import LeadPipeline, LeadStage

        _set_rls(org_a)
        pipeline = LeadPipeline.objects.create(name="AutoOrgPipeline", org=org_a)
        stage = LeadStage(pipeline=pipeline, name="AutoOrg", order=1)
        stage.save()
        assert stage.org_id == org_a.id

    def test_lead_pipeline_str(self, admin_user, org_a):
        """LeadPipeline __str__ returns name with org."""
        from leads.models import LeadPipeline

        _set_rls(org_a)
        pipeline = LeadPipeline.objects.create(name="MyPipeline", org=org_a)
        assert str(pipeline) == f"MyPipeline ({org_a.name})"
