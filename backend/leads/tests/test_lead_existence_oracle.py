"""A lead the caller may not open answers exactly like a lead that does not exist.

Owner decision for 1.11.0: every endpoint that takes a lead id, and every
comment or attachment endpoint whose record is a lead, looks it up through the
read rule (`leads.access.visible_leads_qs`) in one step. The detail view used
to fetch org-wide and then check, answering 403 for a hidden lead and 404 for
a missing one, so any member could tell which ids were real. Each test
compares the two responses WHOLE, status and body.

Writes narrower than reads still refuse with 403, but only a caller who can
open the lead: an assignee cannot delete it, a non-author cannot edit a
comment on it.
"""

import pytest
from django.contrib.contenttypes.models import ContentType

from common.models import Attachments, Comment
from leads.models import Lead, LeadPipeline, LeadStage

MISSING = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def lead(org_a):
    """In the caller's org, created by nobody, assigned to nobody: hidden."""
    return Lead.objects.create(
        first_name="Hidden", last_name="Lead", email="hidden@example.com", org=org_a
    )


@pytest.fixture
def comment(lead, admin_profile, org_a):
    return Comment.objects.create(
        content_type=ContentType.objects.get_for_model(Lead),
        object_id=lead.id,
        comment="Not yours",
        commented_by=admin_profile,
        org=org_a,
    )


@pytest.fixture
def attachment(lead, admin_user, org_a):
    return Attachments.objects.create(
        content_type=ContentType.objects.get_for_model(Lead),
        object_id=lead.id,
        file_name="x.txt",
        attachment="attachments/x.txt",
        created_by=admin_user,
        org=org_a,
    )


def _same(client, method, hidden_url, missing_url, body=None):
    call = getattr(client, method)
    kwargs = {"format": "json"} if body is not None else {}
    args = (body,) if body is not None else ()
    hidden = call(hidden_url, *args, **kwargs)
    missing = call(missing_url, *args, **kwargs)
    assert hidden.status_code == missing.status_code == 404
    assert hidden.json() == missing.json()


DETAIL = [
    ("get", None),
    ("put", {"first_name": "Mine", "last_name": "Now"}),
    ("patch", {"first_name": "Mine"}),
    ("post", {"comment": "hello"}),
    ("delete", None),
]


@pytest.mark.django_db
class TestLeadIdEndpoints:
    @pytest.mark.parametrize("method,body", DETAIL)
    def test_detail(self, user_client, lead, method, body):
        _same(
            user_client,
            method,
            f"/api/leads/{lead.id}/",
            f"/api/leads/{MISSING}/",
            body,
        )
        lead.refresh_from_db()
        assert lead.first_name == "Hidden"

    def test_move(self, user_client, lead, org_a):
        pipeline = LeadPipeline.objects.create(name="P", org=org_a)
        stage = LeadStage.objects.create(
            pipeline=pipeline, name="S", order=1, org=org_a
        )
        _same(
            user_client,
            "patch",
            f"/api/leads/{lead.id}/move/",
            f"/api/leads/{MISSING}/move/",
            {"stage_id": str(stage.id)},
        )
        lead.refresh_from_db()
        assert lead.stage_id is None


@pytest.mark.django_db
class TestCommentsAndFiles:
    @pytest.mark.parametrize(
        "method,body",
        [("put", {"comment": "x"}), ("patch", {"comment": "x"}), ("delete", None)],
    )
    def test_comment_on_hidden_lead(self, user_client, comment, method, body):
        _same(
            user_client,
            method,
            f"/api/leads/comment/{comment.id}/",
            f"/api/leads/comment/{MISSING}/",
            body,
        )
        assert Comment.objects.filter(id=comment.id).exists()

    def test_attachment_on_hidden_lead(self, user_client, attachment):
        _same(
            user_client,
            "delete",
            f"/api/leads/attachment/{attachment.id}/",
            f"/api/leads/attachment/{MISSING}/",
        )
        assert Attachments.objects.filter(id=attachment.id).exists()

    def test_readable_lead_non_author_is_403(
        self, user_client, user_profile, lead, comment, attachment
    ):
        lead.assigned_to.add(user_profile)
        url = f"/api/leads/comment/{comment.id}/"
        assert user_client.put(url, {"comment": "x"}, format="json").status_code == 403
        assert user_client.delete(url).status_code == 403
        assert (
            user_client.delete(f"/api/leads/attachment/{attachment.id}/").status_code
            == 403
        )


@pytest.mark.django_db
class TestDeleteStaysNarrower:
    def test_assignee_who_is_not_creator_gets_403(
        self, user_client, user_profile, lead
    ):
        lead.assigned_to.add(user_profile)
        assert user_client.get(f"/api/leads/{lead.id}/").status_code == 200
        assert user_client.delete(f"/api/leads/{lead.id}/").status_code == 403
        assert Lead.objects.filter(id=lead.id).exists()

    def test_creator_may_delete(self, user_client, regular_user, lead):
        Lead.objects.filter(id=lead.id).update(created_by=regular_user)
        assert user_client.delete(f"/api/leads/{lead.id}/").status_code == 200

    def test_admin_opens_any_lead(self, admin_client, lead):
        assert admin_client.get(f"/api/leads/{lead.id}/").status_code == 200
