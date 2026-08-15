"""
Access control, tenancy and account links on contacts.

Every case here was written against a defect proven by driving the running API,
and each one fails on the code as it stood:

1.  `get`, `put`, `patch` and the comment endpoint compared `created_by` -- a
    `User` -- against `request.profile`, so those branches were always False.
    The list filter and `delete` compared it correctly. A non-admin therefore
    saw their own contacts on the index, was refused a look at any of them, and
    could delete the very record they had just been refused.

2.  `users_mention` read `contact.created_by.user.email`. `created_by` is
    already a User, so this raised AttributeError: every non-admin who was
    allowed to open a contact got a 500 instead. Between this and (1), the
    detail endpoint had never once worked for a role="USER" profile.

3.  The comment/attachment POST looked the contact up with
    `Contact.objects.get(pk=pk)` -- no org filter -- and returned that
    contact's name, email, phone and address to any admin of any org.

4.  A comment on a contact could never be recorded. `Comment` is a generic
    relation with no `contact` field, so `save(contact_id=...)` raised
    TypeError; and `object_id`/`org` are required on the serializer but are not
    the client's to send, so `is_valid()` was False and the whole block was
    skipped in silence -- 200, with the comment dropped.

5.  `ContactAttachmentView.delete` looked its row up by primary key with no org
    filter, so it deleted any attachment in the database regardless of tenant,
    and compared a Profile to a User so the uploader could not delete their own
    file.

6.  `account` was an unvalidated FK: one org's contact could be attached to
    another org's account, and was.

7.  Setting that FK put nobody on the account's people list, because the two
    account links -- `Contact.account` and `Account.contacts` -- were never kept
    in step.

8.  `?city=` filtered `address__city` on a model with a flat `city` (500), and
    `?assigned_to=` read one value out of a list into `__in` so Django iterated
    the string character by character (500).

9.  The list was ordered by `-id`, a random UUID, while claiming to be ordered
    at all. Malformed ids answered 500 rather than 404.

Run with: pytest contacts/tests/test_contact_access_and_links.py -v
"""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.utils import timezone
from rest_framework import status

from accounts.models import Account
from common.models import COMMENT_MAX_LENGTH, Attachments, Comment
from contacts.models import Contact

LIST_URL = "/api/contacts/"


def _detail(pk):
    return f"/api/contacts/{pk}/"


def _attachment(pk):
    return f"/api/contacts/attachment/{pk}/"


def _comment(pk):
    return f"/api/contacts/comment/{pk}/"


def _set_rls(org):
    """Let direct ORM writes through when the tests run on PostgreSQL."""
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.current_org', %s, false)", [str(org.id)])


def _created_by(instance, user):
    """Record who created something, after the fact.

    `BaseModel.save()` takes `created_by` from the crum thread-local and sets it
    to None when there is no request in flight -- which is every direct ORM
    write in a test. Passing `created_by=` to `create()` looks like it works and
    stores nothing, so the ownership tests below would pass against any code at
    all. `update()` writes the column without going through `save()`.
    """
    type(instance).objects.filter(id=instance.id).update(created_by=user)
    instance.refresh_from_db()
    return instance


@pytest.fixture
def contact(org_a):
    _set_rls(org_a)
    return Contact.objects.create(
        first_name="Dana", last_name="Reyes", email="dana@example.com", org=org_a
    )


@pytest.fixture
def account_a(org_a):
    _set_rls(org_a)
    return Account.objects.create(name="Northwind Traders", org=org_a)


@pytest.fixture
def account_b(org_b):
    _set_rls(org_b)
    return Account.objects.create(name="Someone Else Ltd", org=org_b)


@pytest.fixture
def their_attachment(org_b, user_b):
    """An attachment belonging entirely to the other org."""
    _set_rls(org_b)
    their_contact = Contact.objects.create(
        first_name="Not", last_name="Yours", org=org_b
    )
    attachment = Attachments(
        file_name="theirs.txt", content_object=their_contact, org=org_b
    )
    attachment.attachment = SimpleUploadedFile("theirs.txt", b"not yours")
    attachment.save()
    return _created_by(attachment, user_b)


@pytest.mark.django_db
class TestContactDetailAccess:
    """Who may open a contact."""

    def test_admin_opens_any_contact_in_their_org(self, admin_client, contact):
        response = admin_client.get(_detail(contact.id))
        assert response.status_code == status.HTTP_200_OK

    def test_creator_opens_the_contact_they_entered(
        self, user_client, contact, regular_user
    ):
        """Was 403: a Profile is never equal to a User."""
        _created_by(contact, regular_user)
        response = user_client.get(_detail(contact.id))
        assert response.status_code == status.HTTP_200_OK

    def test_assignee_opens_the_contact_without_a_500(
        self, user_client, contact, user_profile
    ):
        """Was 500, from `created_by.user.email` in users_mention."""
        contact.assigned_to.add(user_profile)
        response = user_client.get(_detail(contact.id))
        assert response.status_code == status.HTTP_200_OK
        assert response.data["contact_obj"]["first_name"] == "Dana"

    def test_owner_of_the_account_opens_the_people_at_it(
        self, user_client, contact, user_profile, account_a
    ):
        account_a.contacts.add(contact)
        account_a.assigned_to.add(user_profile)
        response = user_client.get(_detail(contact.id))
        assert response.status_code == status.HTTP_200_OK

    def test_stranger_is_refused(self, user_client, contact):
        """The predicate has to be able to say no, too."""
        response = user_client.get(_detail(contact.id))
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_another_orgs_contact_is_not_found(self, org_b_client, contact):
        response = org_b_client.get(_detail(contact.id))
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_users_mention_names_the_creator_not_a_missing_attribute(
        self, user_client, contact, user_profile, admin_user
    ):
        contact.assigned_to.add(user_profile)
        _created_by(contact, admin_user)
        response = user_client.get(_detail(contact.id))
        assert response.status_code == status.HTTP_200_OK
        assert response.data["users_mention"] == [{"user__email": admin_user.email}]

    def test_users_mention_survives_a_contact_with_no_creator(
        self, user_client, contact, user_profile
    ):
        """Imported and inbound-created contacts have `created_by` of None."""
        contact.assigned_to.add(user_profile)
        assert contact.created_by is None
        response = user_client.get(_detail(contact.id))
        assert response.status_code == status.HTTP_200_OK
        assert response.data["users_mention"] == []


@pytest.mark.django_db
class TestTheVerbsAgree:
    """List, get, patch and delete used to tell one person four things."""

    def test_creator_sees_it_listed_and_can_open_and_edit_it(
        self, user_client, contact, regular_user
    ):
        _created_by(contact, regular_user)

        listed = user_client.get(LIST_URL)
        assert str(contact.id) in [row["id"] for row in listed.data["results"]]

        assert user_client.get(_detail(contact.id)).status_code == status.HTTP_200_OK

        patched = user_client.patch(
            _detail(contact.id), {"title": "Head of Ops"}, format="json"
        )
        assert patched.status_code == status.HTTP_200_OK
        contact.refresh_from_db()
        assert contact.title == "Head of Ops"

    def test_creator_may_still_delete(self, user_client, contact, regular_user):
        _created_by(contact, regular_user)
        response = user_client.delete(_detail(contact.id))
        assert response.status_code == status.HTTP_200_OK
        assert not Contact.objects.filter(id=contact.id).exists()

    def test_assignee_may_edit_but_not_delete(self, user_client, contact, user_profile):
        """Deliberately narrower, and now deliberate rather than accidental."""
        contact.assigned_to.add(user_profile)
        assert (
            user_client.patch(
                _detail(contact.id), {"title": "Ops"}, format="json"
            ).status_code
            == status.HTTP_200_OK
        )
        assert (
            user_client.delete(_detail(contact.id)).status_code
            == status.HTTP_403_FORBIDDEN
        )
        assert Contact.objects.filter(id=contact.id).exists()

    @patch("contacts.views.send_email_to_assigned_user.delay")
    def test_put_refuses_before_it_validates(self, _email, user_client, contact):
        """A rejected payload must not double as a permission oracle.

        Sending garbage as somebody with no access should say 403, not report
        which fields were wrong -- otherwise the error body tells a stranger
        what the record's shape is.
        """
        response = user_client.put(
            _detail(contact.id), {"email": "nope"}, format="json"
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
class TestCommentsAreRecorded:
    """The comment endpoint answered 200 and saved nothing."""

    def test_a_comment_is_saved_and_returned(self, admin_client, contact, org_a):
        response = admin_client.post(
            _detail(contact.id), {"comment": "Left a voicemail"}, format="json"
        )
        assert response.status_code == status.HTTP_200_OK
        assert [c["comment"] for c in response.data["comments"]] == ["Left a voicemail"]

        saved = Comment.objects.get(object_id=contact.id)
        assert saved.org_id == org_a.id
        assert saved.comment == "Left a voicemail"

    def test_the_author_and_the_target_come_from_the_server(
        self, admin_client, contact, admin_profile, org_a, org_b, user_profile
    ):
        """Neither is the caller's to choose."""
        other_contact = Contact.objects.create(
            first_name="Someone", last_name="Else", org=org_a
        )
        response = admin_client.post(
            _detail(contact.id),
            {
                "comment": "mine",
                "object_id": str(other_contact.id),
                "org": str(org_b.id),
                "commented_by": str(user_profile.id),
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        saved = Comment.objects.get(comment="mine")
        assert saved.object_id == contact.id
        assert saved.org_id == org_a.id
        assert saved.commented_by_id == admin_profile.id

    def test_an_empty_body_records_nothing_and_does_not_fail(
        self, admin_client, contact
    ):
        response = admin_client.post(_detail(contact.id), {}, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert Comment.objects.filter(object_id=contact.id).count() == 0

    def test_a_comment_longer_than_the_old_limit_is_stored(self, admin_client, contact):
        """`comment` was a CharField(255) and is now TextField(COMMENT_MAX_LENGTH).

        255 characters could not hold a real reply, which is why the customer
        portal forced the widening. This pins the new floor so the limit cannot
        creep back down without somebody noticing.
        """
        response = admin_client.post(
            _detail(contact.id), {"comment": "x" * 300}, format="json"
        )
        assert response.status_code == status.HTTP_200_OK
        assert Comment.objects.filter(object_id=contact.id).count() == 1

    def test_a_comment_too_long_to_store_is_reported(self, admin_client, contact):
        """Rather than dropped. The bound is explicit now, not a leftover."""
        response = admin_client.post(
            _detail(contact.id),
            {"comment": "x" * (COMMENT_MAX_LENGTH + 1)},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Comment.objects.filter(object_id=contact.id).count() == 0

    def test_commenting_on_another_orgs_contact_is_not_found(
        self, org_b_client, contact
    ):
        """Was 200, with the whole contact record in the response body."""
        response = org_b_client.post(
            _detail(contact.id), {"comment": "yours now"}, format="json"
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert Comment.objects.filter(object_id=contact.id).count() == 0

    def test_commenting_on_a_contact_that_is_gone_is_not_found(self, admin_client):
        response = admin_client.post(
            _detail("11111111-1111-1111-1111-111111111111"),
            {"comment": "hello"},
            format="json",
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_a_stranger_cannot_comment(self, user_client, contact):
        response = user_client.post(
            _detail(contact.id), {"comment": "hello"}, format="json"
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert Comment.objects.filter(object_id=contact.id).count() == 0

    def test_editing_a_comment_that_is_gone_is_not_found(self, admin_client):
        response = admin_client.patch(
            _comment("11111111-1111-1111-1111-111111111111"),
            {"comment": "edited"},
            format="json",
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
class TestAttachmentDeleteIsScoped:
    """One generic table, six modules, and no org filter on the delete."""

    def test_an_admin_cannot_delete_another_orgs_attachment(
        self, admin_client, their_attachment
    ):
        response = admin_client.delete(_attachment(their_attachment.id))
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert Attachments.objects.filter(id=their_attachment.id).exists()

    def test_the_uploader_can_delete_their_own(
        self, user_client, contact, regular_user, org_a
    ):
        attachment = Attachments(
            file_name="mine.txt", content_object=contact, org=org_a
        )
        attachment.attachment = SimpleUploadedFile("mine.txt", b"mine")
        attachment.save()
        _created_by(attachment, regular_user)

        response = user_client.delete(_attachment(attachment.id))
        assert response.status_code == status.HTTP_200_OK
        assert not Attachments.objects.filter(id=attachment.id).exists()

    def test_somebody_elses_upload_is_refused(
        self, user_client, contact, admin_user, org_a
    ):
        attachment = Attachments(
            file_name="theirs.txt", content_object=contact, org=org_a
        )
        attachment.attachment = SimpleUploadedFile("theirs.txt", b"theirs")
        attachment.save()
        _created_by(attachment, admin_user)

        response = user_client.delete(_attachment(attachment.id))
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert Attachments.objects.filter(id=attachment.id).exists()


@pytest.mark.django_db
class TestAccountLink:
    """`Contact.account` and `Account.contacts` are two links to one thing."""

    def test_an_account_from_another_org_is_refused(
        self, admin_client, contact, account_b
    ):
        response = admin_client.patch(
            _detail(contact.id), {"account": str(account_b.id)}, format="json"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        contact.refresh_from_db()
        assert contact.account_id is None

    def test_setting_the_primary_account_puts_them_on_its_people_list(
        self, admin_client, contact, account_a
    ):
        """Otherwise the field writes to a column no page reads."""
        response = admin_client.patch(
            _detail(contact.id), {"account": str(account_a.id)}, format="json"
        )
        assert response.status_code == status.HTTP_200_OK
        contact.refresh_from_db()
        assert contact.account_id == account_a.id
        assert list(account_a.contacts.values_list("id", flat=True)) == [contact.id]

    def test_clearing_the_primary_account_leaves_the_membership(
        self, admin_client, contact, account_a
    ):
        """Membership can be granted from the account side; losing "primary"
        is not a statement that the person left the company."""
        admin_client.patch(
            _detail(contact.id), {"account": str(account_a.id)}, format="json"
        )
        response = admin_client.patch(
            _detail(contact.id), {"account": None}, format="json"
        )
        assert response.status_code == status.HTTP_200_OK
        contact.refresh_from_db()
        assert contact.account_id is None
        assert list(account_a.contacts.values_list("id", flat=True)) == [contact.id]

    @patch("contacts.views.send_email_to_assigned_user.delay")
    def test_creating_a_contact_against_an_account_links_both_ways(
        self, _email, admin_client, account_a
    ):
        response = admin_client.post(
            LIST_URL,
            {
                "first_name": "Marcus",
                "last_name": "Webb",
                "email": "marcus@example.com",
                "account": str(account_a.id),
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        created = Contact.objects.get(id=response.data["id"])
        assert created.account_id == account_a.id
        assert created in account_a.contacts.all()

    @patch("contacts.views.send_email_to_assigned_user.delay")
    def test_create_returns_the_id_it_made(self, _email, admin_client):
        response = admin_client.post(
            LIST_URL,
            {"first_name": "Ada", "last_name": "Nolan", "email": "ada@example.com"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert Contact.objects.filter(id=response.data["id"]).exists()

    @patch("contacts.views.send_email_to_assigned_user.delay")
    def test_creating_against_another_orgs_account_is_refused(
        self, _email, admin_client, account_b
    ):
        response = admin_client.post(
            LIST_URL,
            {
                "first_name": "Nope",
                "last_name": "Nope",
                "email": "nope@example.com",
                "account": str(account_b.id),
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not Contact.objects.filter(email="nope@example.com").exists()

    def test_both_account_links_are_published(
        self, admin_client, contact, account_a, org_a
    ):
        """A reader has to be able to tell which link is which."""
        second = Account.objects.create(name="Also Theirs", org=org_a)
        second.contacts.add(contact)
        admin_client.patch(
            _detail(contact.id), {"account": str(account_a.id)}, format="json"
        )

        response = admin_client.get(_detail(contact.id))
        detail = response.data["contact_obj"]
        assert detail["account_detail"] == {
            "id": str(account_a.id),
            "name": "Northwind Traders",
        }
        assert {row["name"] for row in detail["linked_accounts"]} == {
            "Northwind Traders",
            "Also Theirs",
        }

    def test_account_detail_is_null_when_there_is_no_primary(
        self, admin_client, contact
    ):
        response = admin_client.get(_detail(contact.id))
        assert response.data["contact_obj"]["account_detail"] is None
        assert response.data["contact_obj"]["linked_accounts"] == []


@pytest.mark.django_db
class TestListFiltersAndOrder:
    def test_filtering_by_city(self, admin_client, contact):
        """Was 500: `address__city` on a model with a flat `city`."""
        contact.city = "North Michael"
        contact.save()
        response = admin_client.get(LIST_URL, {"city": "north"})
        assert response.status_code == status.HTTP_200_OK
        assert [row["id"] for row in response.data["results"]] == [str(contact.id)]

    def test_filtering_by_city_that_matches_nobody(self, admin_client, contact):
        response = admin_client.get(LIST_URL, {"city": "Atlantis"})
        assert response.status_code == status.HTTP_200_OK
        assert response.data["results"] == []

    def test_filtering_by_owner(self, admin_client, contact, user_profile, org_a):
        """Was 500: one uuid string fed to `__in` is iterated per character."""
        other = Contact.objects.create(
            first_name="Someone", last_name="Else", org=org_a
        )
        contact.assigned_to.add(user_profile)
        response = admin_client.get(LIST_URL, {"assigned_to": str(user_profile.id)})
        assert response.status_code == status.HTTP_200_OK
        ids = [row["id"] for row in response.data["results"]]
        assert str(contact.id) in ids
        assert str(other.id) not in ids

    def test_the_list_is_newest_first(self, admin_client, org_a):
        """Was ordered by `-id` -- a random UUID, so by nothing at all."""
        now = timezone.now()
        made = []
        for index, name in enumerate(["Oldest", "Middle", "Newest"]):
            row = Contact.objects.create(first_name=name, last_name="Row", org=org_a)
            Contact.objects.filter(id=row.id).update(
                created_at=now - timedelta(days=10 - index)
            )
            made.append(row)

        response = admin_client.get(LIST_URL)
        names = [row["first_name"] for row in response.data["results"]]
        assert names == ["Newest", "Middle", "Oldest"]


@pytest.mark.django_db
class TestMalformedIds:
    """A mistyped URL is a 404, not an error report."""

    def test_detail(self, admin_client):
        assert (
            admin_client.get(_detail("not-a-uuid")).status_code
            == status.HTTP_404_NOT_FOUND
        )

    def test_patch(self, admin_client):
        assert (
            admin_client.patch(
                _detail("not-a-uuid"), {"title": "x"}, format="json"
            ).status_code
            == status.HTTP_404_NOT_FOUND
        )

    def test_delete(self, admin_client):
        assert (
            admin_client.delete(_detail("not-a-uuid")).status_code
            == status.HTTP_404_NOT_FOUND
        )

    def test_comment_endpoint(self, admin_client):
        assert (
            admin_client.delete(_comment("not-a-uuid")).status_code
            == status.HTTP_404_NOT_FOUND
        )

    def test_attachment_endpoint(self, admin_client):
        assert (
            admin_client.delete(_attachment("not-a-uuid")).status_code
            == status.HTTP_404_NOT_FOUND
        )
