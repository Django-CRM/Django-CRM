"""An account the caller may not open answers exactly like one that does not exist.

Owner decision for 1.11.0: the account detail, mail, comment and attachment
endpoints look the account up through `accounts.access.visible_accounts_qs`
in one step. They used to fetch org-wide and then check, answering 403 for a
hidden account and 404 for a missing one. Each test compares the two
responses WHOLE. Writes narrower than reads keep their 403, only for a caller
who can open the account.
"""

from unittest import mock

import pytest
from django.contrib.contenttypes.models import ContentType

from accounts.models import Account
from common.models import Attachments, Comment

MISSING = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def account(org_a):
    """In the caller's org, created by nobody, assigned to nobody: hidden."""
    return Account.objects.create(name="Hidden Co", org=org_a)


@pytest.fixture
def comment(account, admin_profile, org_a):
    return Comment.objects.create(
        content_type=ContentType.objects.get_for_model(Account),
        object_id=account.id,
        comment="Not yours",
        commented_by=admin_profile,
        org=org_a,
    )


@pytest.fixture
def attachment(account, admin_user, org_a):
    return Attachments.objects.create(
        content_type=ContentType.objects.get_for_model(Account),
        object_id=account.id,
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
class TestAccountIdEndpoints:
    @pytest.mark.parametrize(
        "method,body",
        [
            ("get", None),
            ("put", {"name": "Mine now"}),
            ("patch", {"name": "Mine now"}),
            ("post", {"comment": "hello"}),
            ("delete", None),
        ],
    )
    def test_detail(self, user_client, account, method, body):
        _same(
            user_client,
            method,
            f"/api/accounts/{account.id}/",
            f"/api/accounts/{MISSING}/",
            body,
        )
        account.refresh_from_db()
        assert account.name == "Hidden Co"

    def test_create_mail(self, user_client, account):
        body = {
            "from_email": "sales@example.com",
            "message_subject": "Hello",
            "message_body": "Body",
        }
        with mock.patch("accounts.views.send_email.delay") as dispatch:
            _same(
                user_client,
                "post",
                f"/api/accounts/{account.id}/create_mail/",
                f"/api/accounts/{MISSING}/create_mail/",
                body,
            )
        dispatch.assert_not_called()


@pytest.mark.django_db
class TestCommentsAndFiles:
    @pytest.mark.parametrize(
        "method,body",
        [("put", {"comment": "x"}), ("patch", {"comment": "x"}), ("delete", None)],
    )
    def test_comment_on_hidden_account(self, user_client, comment, method, body):
        _same(
            user_client,
            method,
            f"/api/accounts/comment/{comment.id}/",
            f"/api/accounts/comment/{MISSING}/",
            body,
        )
        assert Comment.objects.filter(id=comment.id).exists()

    def test_attachment_on_hidden_account(self, user_client, attachment):
        _same(
            user_client,
            "delete",
            f"/api/accounts/attachment/{attachment.id}/",
            f"/api/accounts/attachment/{MISSING}/",
        )
        assert Attachments.objects.filter(id=attachment.id).exists()

    def test_readable_account_non_author_is_403(
        self, user_client, user_profile, account, comment, attachment
    ):
        account.assigned_to.add(user_profile)
        url = f"/api/accounts/comment/{comment.id}/"
        assert user_client.put(url, {"comment": "x"}, format="json").status_code == 403
        assert user_client.delete(url).status_code == 403
        attachment_url = f"/api/accounts/attachment/{attachment.id}/"
        assert user_client.delete(attachment_url).status_code == 403


@pytest.mark.django_db
class TestDeleteStaysNarrower:
    def test_assignee_who_is_not_creator_gets_403(
        self, user_client, user_profile, account
    ):
        account.assigned_to.add(user_profile)
        assert user_client.get(f"/api/accounts/{account.id}/").status_code == 200
        assert user_client.delete(f"/api/accounts/{account.id}/").status_code == 403
        assert Account.objects.filter(id=account.id).exists()

    def test_creator_may_delete(self, user_client, regular_user, account):
        Account.objects.filter(id=account.id).update(created_by=regular_user)
        assert user_client.delete(f"/api/accounts/{account.id}/").status_code == 200
