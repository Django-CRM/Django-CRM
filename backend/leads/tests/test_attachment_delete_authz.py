"""Authorization on `DELETE /api/leads/attachment/<pk>/`.

The lookup was `Attachments.objects.get(pk=pk)` with no org filter, so the only
thing standing between an admin of one tenant and another tenant's attachment
was Postgres RLS, and RLS is the safety net rather than the contract. It also
meant a missing or malformed id raised out of the view as a 500.

The uploader case is asserted for a `role="USER"` profile on purpose: it is the
direction the sibling opportunity view had dead through a `Profile` vs `User`
type mismatch, so an "it deletes fine" test written only as an admin would pass
against either implementation.
"""

import uuid

import pytest
from crum import impersonate
from django.contrib.contenttypes.models import ContentType
from django.core.files.uploadedfile import SimpleUploadedFile

from common.models import Attachments
from leads.models import Lead


def _attachment(org, lead, creator):
    # `BaseModel.save()` overwrites `created_by` from crum's current user, and
    # in a test there is none, so passing `created_by=` alone silently stores
    # NULL and the uploader assertions below would pass for the wrong reason.
    # This is the same trap that made the lead CSV importer create nothing.
    with impersonate(creator):
        return Attachments.objects.create(
            file_name="brief.txt",
            attachment=SimpleUploadedFile("brief.txt", b"contents"),
            content_type=ContentType.objects.get_for_model(Lead),
            object_id=lead.id,
            org=org,
        )


@pytest.fixture
def lead_a(org_a):
    return Lead.objects.create(
        title="Org A lead", first_name="Ay", last_name="Lead", org=org_a
    )


@pytest.fixture
def lead_b(org_b):
    return Lead.objects.create(
        title="Org B lead", first_name="Bee", last_name="Lead", org=org_b
    )


@pytest.mark.django_db
class TestLeadAttachmentDelete:
    URL = "/api/leads/attachment/{}/"

    def test_admin_deletes_own_org_attachment(
        self, org_a, admin_client, admin_user, lead_a
    ):
        att = _attachment(org_a, lead_a, admin_user)
        assert admin_client.delete(self.URL.format(att.id)).status_code == 200
        assert not Attachments.objects.filter(id=att.id).exists()

    def test_uploader_deletes_own_attachment(
        self, user_profile, org_a, user_client, regular_user, lead_a
    ):
        """A non-admin may delete what they uploaded. The `created_by` branch."""
        att = _attachment(org_a, lead_a, regular_user)
        lead_a.assigned_to.add(user_profile)  # can open it
        assert user_client.delete(self.URL.format(att.id)).status_code == 200
        assert not Attachments.objects.filter(id=att.id).exists()

    def test_other_member_cannot_delete(
        self, user_profile, org_a, user_client, admin_user, lead_a
    ):
        """The same check must be able to return False, or it is not a check."""
        att = _attachment(org_a, lead_a, admin_user)
        lead_a.assigned_to.add(user_profile)  # can open it
        assert user_client.delete(self.URL.format(att.id)).status_code == 403
        assert Attachments.objects.filter(id=att.id).exists()

    def test_cross_org_admin_cannot_delete(
        self, org_b, org_b_client, user_b, lead_b, org_a, admin_user, lead_a
    ):
        """An admin of org B must not reach org A's attachment by id."""
        att = _attachment(org_a, lead_a, admin_user)
        assert org_b_client.delete(self.URL.format(att.id)).status_code == 404
        assert Attachments.objects.filter(id=att.id).exists()

    def test_unknown_id_is_404_not_500(self, admin_client):
        assert admin_client.delete(self.URL.format(uuid.uuid4())).status_code == 404

    def test_malformed_id_is_404_not_500(self, admin_client):
        assert admin_client.delete(self.URL.format("not-a-uuid")).status_code == 404
