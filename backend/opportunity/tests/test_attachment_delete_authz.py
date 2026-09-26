"""Authorization on `DELETE /api/opportunities/attachment/<pk>/`.

The twin of `leads/tests/test_attachment_delete_authz.py`, and the worse of the
two. This view had the unscoped `objects.get(pk=pk)` AND compared
`request.profile` (a `Profile`) to `created_by` (a FK to `User`), so the
uploader branch could never be true and a non-admin could not delete their own
upload. `test_uploader_deletes_own_attachment` is the guard for that half: it
fails against the old code with a 403 rather than a cross-org leak, which is why
it has to be here and not only in the leads twin.
"""

import uuid

import pytest
from crum import impersonate
from django.contrib.contenttypes.models import ContentType
from django.core.files.uploadedfile import SimpleUploadedFile

from common.models import Attachments
from opportunity.models import Opportunity


def _attachment(org, opportunity, creator):
    # `impersonate` puts a current user in crum's thread local, so this exercises
    # the same stamping path a real upload takes rather than setting the column
    # directly. It used to be mandatory: `BaseModel.save()` answered "no current
    # user" by storing NULL over an explicit `created_by=`, so the uploader
    # assertions below would have passed for the wrong reason. That is fixed
    # (see `common/tests/test_base_model_audit_stamps.py`) and a plain
    # `created_by=` would work now, but going through the request path is still
    # the more faithful setup, so it stays.
    with impersonate(creator):
        return Attachments.objects.create(
            file_name="proposal.txt",
            attachment=SimpleUploadedFile("proposal.txt", b"contents"),
            content_type=ContentType.objects.get_for_model(Opportunity),
            object_id=opportunity.id,
            org=org,
        )


@pytest.fixture
def opp_a(org_a):
    return Opportunity.objects.create(name="Org A deal", org=org_a)


@pytest.fixture
def opp_b(org_b):
    return Opportunity.objects.create(name="Org B deal", org=org_b)


@pytest.mark.django_db
class TestOpportunityAttachmentDelete:
    URL = "/api/opportunities/attachment/{}/"

    def test_admin_deletes_own_org_attachment(
        self, org_a, admin_client, admin_user, opp_a
    ):
        att = _attachment(org_a, opp_a, admin_user)
        assert admin_client.delete(self.URL.format(att.id)).status_code == 200
        assert not Attachments.objects.filter(id=att.id).exists()

    def test_uploader_deletes_own_attachment(
        self, user_profile, org_a, user_client, regular_user, opp_a
    ):
        """The branch the `Profile` vs `User` mismatch had permanently False."""
        att = _attachment(org_a, opp_a, regular_user)
        opp_a.assigned_to.add(user_profile)  # can open it
        assert user_client.delete(self.URL.format(att.id)).status_code == 200
        assert not Attachments.objects.filter(id=att.id).exists()

    def test_other_member_cannot_delete(
        self, user_profile, org_a, user_client, admin_user, opp_a
    ):
        att = _attachment(org_a, opp_a, admin_user)
        opp_a.assigned_to.add(user_profile)  # can open it
        assert user_client.delete(self.URL.format(att.id)).status_code == 403
        assert Attachments.objects.filter(id=att.id).exists()

    def test_cross_org_admin_cannot_delete(
        self, org_b, org_b_client, org_a, admin_user, opp_a
    ):
        att = _attachment(org_a, opp_a, admin_user)
        assert org_b_client.delete(self.URL.format(att.id)).status_code == 404
        assert Attachments.objects.filter(id=att.id).exists()

    def test_unknown_id_is_404_not_500(self, admin_client):
        assert admin_client.delete(self.URL.format(uuid.uuid4())).status_code == 404

    def test_malformed_id_is_404_not_500(self, admin_client):
        assert admin_client.delete(self.URL.format("not-a-uuid")).status_code == 404
