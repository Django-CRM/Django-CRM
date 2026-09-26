"""`PUT`/`DELETE /api/invoices/comments/<id>/` reach a comment only through an
invoice the caller may open.

It used to look up any comment in the org by id: another module's comments
included, and it answered 403 for one the caller could not touch, which
confirmed the id existed. Now a comment that is missing, in another org, on
another module's record, or on an invoice this caller cannot open answers
one identical 404. Among callers who can read the invoice, the author rule
still answers 403.
"""

import uuid

import pytest
from django.contrib.contenttypes.models import ContentType

from accounts.models import Account
from common.models import Comment
from invoices.models import Invoice


@pytest.fixture
def account(org_a):
    return Account.objects.create(name="Acme", org=org_a)


@pytest.fixture
def invoice(org_a, account):
    return Invoice.objects.create(org=org_a, account=account, invoice_title="Work")


def _comment(target, author, text="Original"):
    return Comment.objects.create(
        content_type=ContentType.objects.get_for_model(type(target)),
        object_id=target.id,
        comment=text,
        commented_by=author,
        org=target.org,
    )


def _url(comment_id):
    return f"/api/invoices/comments/{comment_id}/"


def _like_missing(client, comment_id):
    for verb in ("put", "delete"):
        hidden = getattr(client, verb)(
            _url(comment_id), {"comment": "x"}, format="json"
        )
        missing = getattr(client, verb)(
            _url(uuid.uuid4()), {"comment": "x"}, format="json"
        )
        assert hidden.status_code == missing.status_code == 404, verb
        assert hidden.json() == missing.json(), verb


@pytest.mark.django_db
class TestWhoCanReachTheComment:
    def test_comment_on_an_invoice_the_member_cannot_open(
        self, user_client, admin_profile, invoice
    ):
        comment = _comment(invoice, admin_profile)

        _like_missing(user_client, comment.id)
        comment.refresh_from_db()
        assert comment.comment == "Original"

    def test_another_modules_comment_even_for_an_admin(
        self, admin_client, admin_profile, account
    ):
        comment = _comment(account, admin_profile)

        _like_missing(admin_client, comment.id)
        assert Comment.objects.filter(id=comment.id).exists()

    def test_another_orgs_invoice_comment(self, admin_client, org_b, profile_b):
        other = Invoice.objects.create(
            org=org_b,
            account=Account.objects.create(name="Elsewhere", org=org_b),
            invoice_title="Theirs",
        )
        comment = _comment(other, profile_b)

        _like_missing(admin_client, comment.id)
        assert Comment.objects.filter(id=comment.id).exists()


@pytest.mark.django_db
class TestWhoCanChangeIt:
    def test_a_reader_who_is_not_the_author_is_refused(
        self, user_client, user_profile, admin_profile, invoice
    ):
        invoice.assigned_to.add(user_profile)
        comment = _comment(invoice, admin_profile)

        for verb in ("put", "delete"):
            response = getattr(user_client, verb)(
                _url(comment.id), {"comment": "Hacked"}, format="json"
            )
            assert response.status_code == 403, verb
        comment.refresh_from_db()
        assert comment.comment == "Original"

    def test_the_author_may_edit_and_delete(self, user_client, user_profile, invoice):
        invoice.assigned_to.add(user_profile)
        comment = _comment(invoice, user_profile)

        edited = user_client.put(_url(comment.id), {"comment": "Fixed"}, format="json")
        assert edited.status_code == 200, edited.content
        comment.refresh_from_db()
        assert comment.comment == "Fixed"

        deleted = user_client.delete(_url(comment.id))
        assert deleted.status_code == 200, deleted.content
        assert not Comment.objects.filter(id=comment.id).exists()

    def test_an_admin_may_edit_anyones(self, admin_client, user_profile, invoice):
        comment = _comment(invoice, user_profile)

        response = admin_client.put(
            _url(comment.id), {"comment": "Tidied"}, format="json"
        )

        assert response.status_code == 200, response.content

    def test_an_edit_cannot_blank_it(self, admin_client, admin_profile, invoice):
        comment = _comment(invoice, admin_profile)

        response = admin_client.put(_url(comment.id), {"comment": ""}, format="json")

        assert response.status_code == 400, response.content
        comment.refresh_from_db()
        assert comment.comment == "Original"
