"""A deal the caller may not open answers exactly like a deal that does not exist.

Owner decision for 1.11.0: every endpoint that takes a deal id looks the deal
up through the read rule (`opportunity.access.visible_deals_qs`), and so does
every comment or attachment endpoint whose record is a deal. The detail
view and the line-item views used to fetch org-wide and then check, which
answered 403 for a hidden deal and 404 for a missing one, so any member could
tell which ids were real. Each test below compares the two responses WHOLE,
status and body, because a 404 whose body differs is the same oracle.

Writes stay narrower than reads where they already were: an assignee can open
a deal but only its creator or an admin may delete it, and that refusal is an
honest 403 because the caller can see the deal.
"""

import uuid

import pytest
from django.contrib.contenttypes.models import ContentType

from common.models import Attachments, Comment
from opportunity.models import Opportunity, OpportunityLineItem

MISSING = uuid.UUID("11111111-1111-1111-1111-111111111111")


def _deal(org, creator, name="Hidden deal"):
    deal = Opportunity.objects.create(org=org, name=name, stage="PROSPECTING")
    # `BaseModel.save()` stamps `created_by` from the request thread-local,
    # which is empty in a test, so it is set with `.update()`.
    Opportunity.objects.filter(pk=deal.pk).update(created_by=creator)
    deal.refresh_from_db()
    return deal


def _same(hidden, missing):
    assert hidden.status_code == missing.status_code == 404
    assert hidden.json() == missing.json()


@pytest.fixture
def hidden_deal(org_a, admin_user):
    """In the caller's org, created by an admin, assigned to nobody."""
    return _deal(org_a, admin_user)


@pytest.mark.django_db
class TestDetailVerbs:
    URL = "/api/opportunities/{}/"

    def test_get(self, user_client, hidden_deal):
        _same(
            user_client.get(self.URL.format(hidden_deal.pk)),
            user_client.get(self.URL.format(MISSING)),
        )

    def test_put(self, user_client, hidden_deal):
        body = {"name": "Mine now", "stage": "PROSPECTING"}
        _same(
            user_client.put(self.URL.format(hidden_deal.pk), body, format="json"),
            user_client.put(self.URL.format(MISSING), body, format="json"),
        )
        hidden_deal.refresh_from_db()
        assert hidden_deal.name == "Hidden deal"

    def test_patch(self, user_client, hidden_deal):
        body = {"name": "Mine now"}
        _same(
            user_client.patch(self.URL.format(hidden_deal.pk), body, format="json"),
            user_client.patch(self.URL.format(MISSING), body, format="json"),
        )
        hidden_deal.refresh_from_db()
        assert hidden_deal.name == "Hidden deal"

    def test_post_comment(self, user_client, hidden_deal):
        body = {"comment": "hello"}
        _same(
            user_client.post(self.URL.format(hidden_deal.pk), body, format="json"),
            user_client.post(self.URL.format(MISSING), body, format="json"),
        )

    def test_delete(self, user_client, hidden_deal):
        _same(
            user_client.delete(self.URL.format(hidden_deal.pk)),
            user_client.delete(self.URL.format(MISSING)),
        )
        assert Opportunity.objects.filter(pk=hidden_deal.pk).exists()


@pytest.mark.django_db
class TestDeleteStaysNarrower:
    """Reading and deleting are different rules; the 404 is only for hidden deals."""

    URL = "/api/opportunities/{}/"

    def test_assignee_who_is_not_creator_gets_403(
        self, user_client, user_profile, hidden_deal
    ):
        hidden_deal.assigned_to.add(user_profile)
        assert user_client.get(self.URL.format(hidden_deal.pk)).status_code == 200
        response = user_client.delete(self.URL.format(hidden_deal.pk))
        assert response.status_code == 403
        assert Opportunity.objects.filter(pk=hidden_deal.pk).exists()

    def test_creator_may_delete(self, user_client, regular_user, org_a):
        deal = _deal(org_a, regular_user, name="Mine")
        response = user_client.delete(self.URL.format(deal.pk))
        assert response.status_code == 200
        assert not Opportunity.objects.filter(pk=deal.pk).exists()

    def test_admin_still_opens_any_deal(self, admin_client, hidden_deal):
        assert admin_client.get(self.URL.format(hidden_deal.pk)).status_code == 200


@pytest.mark.django_db
class TestLineItems:
    def _item(self, deal, org):
        return OpportunityLineItem.objects.create(
            opportunity=deal, name="Widget", quantity=1, unit_price=10, org=org
        )

    def test_list_and_create(self, user_client, hidden_deal):
        url = "/api/opportunities/{}/line-items/"
        _same(
            user_client.get(url.format(hidden_deal.pk)),
            user_client.get(url.format(MISSING)),
        )
        body = {"name": "Sneaky", "quantity": 1, "unit_price": "5.00"}
        _same(
            user_client.post(url.format(hidden_deal.pk), body, format="json"),
            user_client.post(url.format(MISSING), body, format="json"),
        )
        assert not OpportunityLineItem.objects.filter(name="Sneaky").exists()

    def test_detail_verbs(self, user_client, hidden_deal, org_a):
        item = self._item(hidden_deal, org_a)
        url = "/api/opportunities/{}/line-items/{}/"
        real = url.format(hidden_deal.pk, item.pk)
        fake = url.format(MISSING, MISSING)
        _same(user_client.get(real), user_client.get(fake))
        body = {"name": "Renamed"}
        _same(
            user_client.put(real, body, format="json"),
            user_client.put(fake, body, format="json"),
        )
        _same(user_client.delete(real), user_client.delete(fake))
        item.refresh_from_db()
        assert item.name == "Widget"

    def test_assignee_can_still_price_the_deal(
        self, user_client, user_profile, hidden_deal
    ):
        hidden_deal.assigned_to.add(user_profile)
        response = user_client.post(
            f"/api/opportunities/{hidden_deal.pk}/line-items/",
            {"name": "Allowed", "quantity": 1, "unit_price": "5.00"},
            format="json",
        )
        assert response.status_code == 201


@pytest.mark.django_db
class TestMove:
    URL = "/api/opportunities/{}/move/"

    def test_hidden_deal_moves_like_a_missing_one(self, user_client, hidden_deal):
        body = {"column_id": "NEGOTIATION"}
        _same(
            user_client.patch(self.URL.format(hidden_deal.pk), body, format="json"),
            user_client.patch(self.URL.format(MISSING), body, format="json"),
        )
        hidden_deal.refresh_from_db()
        assert hidden_deal.stage == "PROSPECTING"

    def test_assignee_may_move(self, user_client, user_profile, hidden_deal):
        hidden_deal.assigned_to.add(user_profile)
        response = user_client.patch(
            self.URL.format(hidden_deal.pk), {"column_id": "NEGOTIATION"}, format="json"
        )
        assert response.status_code == 200
        hidden_deal.refresh_from_db()
        assert hidden_deal.stage == "NEGOTIATION"


@pytest.mark.django_db
class TestCommentsAndFiles:
    """A comment or file on a hidden deal is the same 404 as a missing id."""

    def _comment(self, deal, author, org):
        return Comment.objects.create(
            content_type=ContentType.objects.get_for_model(Opportunity),
            object_id=deal.id,
            comment="Not yours",
            commented_by=author,
            org=org,
        )

    def _file(self, deal, uploader, org):
        return Attachments.objects.create(
            content_type=ContentType.objects.get_for_model(Opportunity),
            object_id=deal.id,
            file_name="x.txt",
            attachment="attachments/x.txt",
            created_by=uploader,
            org=org,
        )

    @pytest.mark.parametrize("method", ["put", "patch", "delete"])
    def test_comment_on_hidden_deal(
        self, user_client, hidden_deal, admin_profile, org_a, method
    ):
        comment = self._comment(hidden_deal, admin_profile, org_a)
        call = getattr(user_client, method)
        args = () if method == "delete" else ({"comment": "x"},)
        kwargs = {} if method == "delete" else {"format": "json"}
        _same(
            call(f"/api/opportunities/comment/{comment.id}/", *args, **kwargs),
            call(f"/api/opportunities/comment/{MISSING}/", *args, **kwargs),
        )
        assert Comment.objects.filter(id=comment.id).exists()

    def test_attachment_on_hidden_deal(
        self, user_client, hidden_deal, admin_user, org_a
    ):
        attachment = self._file(hidden_deal, admin_user, org_a)
        _same(
            user_client.delete(f"/api/opportunities/attachment/{attachment.id}/"),
            user_client.delete(f"/api/opportunities/attachment/{MISSING}/"),
        )
        assert Attachments.objects.filter(id=attachment.id).exists()

    def test_readable_deal_non_author_is_403(
        self, user_client, user_profile, hidden_deal, admin_profile, admin_user, org_a
    ):
        hidden_deal.assigned_to.add(user_profile)
        comment = self._comment(hidden_deal, admin_profile, org_a)
        attachment = self._file(hidden_deal, admin_user, org_a)
        url = f"/api/opportunities/comment/{comment.id}/"
        assert user_client.put(url, {"comment": "x"}, format="json").status_code == 403
        assert user_client.delete(url).status_code == 403
        attachment_url = f"/api/opportunities/attachment/{attachment.id}/"
        assert user_client.delete(attachment_url).status_code == 403
