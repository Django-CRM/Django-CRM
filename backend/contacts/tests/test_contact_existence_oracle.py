"""A contact the caller may not open answers exactly like one that does not exist.

Owner decision for 1.11.0: the contact detail, comment and attachment
endpoints look the contact up through `contacts.access.visible_contacts_qs`
in one step. They used to fetch org-wide and then check, answering 403 for a
hidden contact and 404 for a missing one. Each test compares the two
responses WHOLE. Writes narrower than reads keep their 403, only for a caller
who can open the contact.
"""

import pytest
from django.contrib.contenttypes.models import ContentType

from common.models import Attachments, Comment
from contacts.models import Contact

MISSING = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def contact(org_a):
    """In the caller's org, created by nobody, assigned to nobody: hidden."""
    return Contact.objects.create(
        first_name="Hidden", last_name="Person", email="hidden@example.com", org=org_a
    )


@pytest.fixture
def comment(contact, admin_profile, org_a):
    return Comment.objects.create(
        content_type=ContentType.objects.get_for_model(Contact),
        object_id=contact.id,
        comment="Not yours",
        commented_by=admin_profile,
        org=org_a,
    )


@pytest.fixture
def attachment(contact, admin_user, org_a):
    return Attachments.objects.create(
        content_type=ContentType.objects.get_for_model(Contact),
        object_id=contact.id,
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


@pytest.mark.django_db
class TestContactIdEndpoints:
    @pytest.mark.parametrize(
        "method,body",
        [
            ("get", None),
            ("put", {"first_name": "Mine", "last_name": "Now"}),
            ("patch", {"first_name": "Mine"}),
            ("post", {"comment": "hello"}),
            ("delete", None),
        ],
    )
    def test_detail(self, user_client, contact, method, body):
        _same(
            user_client,
            method,
            f"/api/contacts/{contact.id}/",
            f"/api/contacts/{MISSING}/",
            body,
        )
        contact.refresh_from_db()
        assert contact.first_name == "Hidden"


@pytest.mark.django_db
class TestCommentsAndFiles:
    @pytest.mark.parametrize(
        "method,body",
        [("put", {"comment": "x"}), ("patch", {"comment": "x"}), ("delete", None)],
    )
    def test_comment_on_hidden_contact(self, user_client, comment, method, body):
        _same(
            user_client,
            method,
            f"/api/contacts/comment/{comment.id}/",
            f"/api/contacts/comment/{MISSING}/",
            body,
        )
        assert Comment.objects.filter(id=comment.id).exists()

    def test_attachment_on_hidden_contact(self, user_client, attachment):
        _same(
            user_client,
            "delete",
            f"/api/contacts/attachment/{attachment.id}/",
            f"/api/contacts/attachment/{MISSING}/",
        )
        assert Attachments.objects.filter(id=attachment.id).exists()

    def test_readable_contact_non_author_is_403(
        self, user_client, user_profile, contact, comment, attachment
    ):
        contact.assigned_to.add(user_profile)
        url = f"/api/contacts/comment/{comment.id}/"
        assert user_client.put(url, {"comment": "x"}, format="json").status_code == 403
        assert user_client.delete(url).status_code == 403
        attachment_url = f"/api/contacts/attachment/{attachment.id}/"
        assert user_client.delete(attachment_url).status_code == 403


@pytest.mark.django_db
class TestDeleteStaysNarrower:
    def test_assignee_who_is_not_creator_gets_403(
        self, user_client, user_profile, contact
    ):
        contact.assigned_to.add(user_profile)
        assert user_client.get(f"/api/contacts/{contact.id}/").status_code == 200
        assert user_client.delete(f"/api/contacts/{contact.id}/").status_code == 403
        assert Contact.objects.filter(id=contact.id).exists()

    def test_creator_may_delete(self, user_client, regular_user, contact):
        Contact.objects.filter(id=contact.id).update(created_by=regular_user)
        assert user_client.delete(f"/api/contacts/{contact.id}/").status_code == 200
