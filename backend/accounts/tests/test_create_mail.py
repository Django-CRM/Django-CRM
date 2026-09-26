"""`POST /api/accounts/<id>/create_mail/`.

This endpoint had never succeeded once. `EmailSerializer` does not pop the
`request_obj` kwarg the view passes it, so every call raised `TypeError` and
answered 500 before reaching any logic. Behind that sat three more defects that
only become observable once the first is fixed, which is why the happy path is
not enough here: `data = {}` clobbered the request payload before the recipient
and scheduling values were read from it, the scheduled branch never saved, and
dispatch fired on the scheduled path anyway.

Each test below pins one of the four.
"""

import json
import uuid
from unittest import mock

import pytest

from accounts.models import Account, AccountEmail
from contacts.models import Contact


@pytest.fixture
def account(org_a):
    return Account.objects.create(name="Acme", email="acme@example.com", org=org_a)


@pytest.fixture
def contact_a(org_a, account):
    """A contact of `account`: the only kind of recipient it may mail."""
    contact = Contact.objects.create(
        first_name="Cee", last_name="One", email="c1@example.com", org=org_a
    )
    account.contacts.add(contact)
    return contact


@pytest.fixture
def contact_b(org_b):
    return Contact.objects.create(
        first_name="Dee", last_name="Two", email="d2@example.com", org=org_b
    )


def _url(account_id):
    return f"/api/accounts/{account_id}/create_mail/"


def _payload(**overrides):
    body = {
        "from_email": "sales@example.com",
        "message_subject": "Hello",
        "message_body": "Body with no braces",
    }
    body.update(overrides)
    return body


@pytest.mark.django_db
class TestCreateMail:
    def test_valid_call_succeeds(self, admin_client, account):
        """Defect 1: the whole endpoint used to 500 on `request_obj`."""
        resp = admin_client.post(_url(account.id), _payload())
        assert resp.status_code == 200, resp.content
        assert AccountEmail.objects.filter(from_account=account).count() == 1

    def test_recipients_are_attached(self, admin_client, account, contact_a, org_a):
        """Defect 2: `data = {}` meant no recipient could ever be read."""
        resp = admin_client.post(
            _url(account.id), _payload(recipients=json.dumps([str(contact_a.id)]))
        )
        assert resp.status_code == 200, resp.content
        email = AccountEmail.objects.get(from_account=account)
        assert list(email.recipients.values_list("id", flat=True)) == [contact_a.id]

    def test_recipients_accept_a_plain_list(self, admin_client, account, contact_a):
        """A JSON body sends a real list; only multipart needs the encoded form."""
        resp = admin_client.post(
            _url(account.id), _payload(recipients=[str(contact_a.id)]), format="json"
        )
        assert resp.status_code == 200, resp.content
        email = AccountEmail.objects.get(from_account=account)
        assert email.recipients.count() == 1

    def test_scheduled_mail_is_recorded_and_not_dispatched(self, admin_client, account):
        """Defects 3 and 4: the flag was never saved and the mail sent anyway."""
        with mock.patch("accounts.views.send_email.delay") as dispatch:
            resp = admin_client.post(
                _url(account.id),
                _payload(
                    scheduled_later="true", scheduled_date_time="2099-01-01T10:00:00Z"
                ),
            )
        assert resp.status_code == 200, resp.content
        email = AccountEmail.objects.get(from_account=account)
        assert email.scheduled_later is True
        assert email.scheduled_date_time is not None
        dispatch.assert_not_called()

    def test_unscheduled_mail_is_dispatched(self, admin_client, account):
        with mock.patch("accounts.views.send_email.delay") as dispatch:
            assert admin_client.post(_url(account.id), _payload()).status_code == 200
        dispatch.assert_called_once()

    def test_scheduled_without_a_time_is_rejected(self, admin_client, account):
        resp = admin_client.post(_url(account.id), _payload(scheduled_later="true"))
        assert resp.status_code == 400
        assert not AccountEmail.objects.filter(from_account=account).exists()


@pytest.mark.django_db
class TestCreateMailScoping:
    def test_account_in_another_org_is_404(self, admin_client, org_b):
        other = Account.objects.create(name="Theirs", org=org_b)
        resp = admin_client.post(_url(other.id), _payload())
        assert resp.status_code == 404
        assert not AccountEmail.objects.filter(from_account=other).exists()

    def test_unknown_account_is_404(self, admin_client):
        assert admin_client.post(_url(uuid.uuid4()), _payload()).status_code == 404

    def test_recipient_in_another_org_is_rejected(
        self, admin_client, account, contact_b
    ):
        """And the half-built mail row must not be left behind."""
        resp = admin_client.post(
            _url(account.id), _payload(recipients=json.dumps([str(contact_b.id)]))
        )
        assert resp.status_code == 400
        assert not AccountEmail.objects.filter(from_account=account).exists()

    def test_one_bad_recipient_rejects_the_whole_send(
        self, admin_client, account, contact_a, contact_b
    ):
        """Never mail the valid subset and call it success."""
        resp = admin_client.post(
            _url(account.id),
            _payload(recipients=json.dumps([str(contact_a.id), str(contact_b.id)])),
        )
        assert resp.status_code == 400
        assert not AccountEmail.objects.filter(from_account=account).exists()

    def test_malformed_recipient_id_is_400_not_500(self, admin_client, account):
        resp = admin_client.post(
            _url(account.id), _payload(recipients=json.dumps(["not-a-uuid"]))
        )
        assert resp.status_code == 400
        assert not AccountEmail.objects.filter(from_account=account).exists()

    def test_mail_is_stamped_with_the_callers_org(self, admin_client, account, org_a):
        assert admin_client.post(_url(account.id), _payload()).status_code == 200
        assert AccountEmail.objects.get(from_account=account).org_id == org_a.id


@pytest.mark.django_db
class TestCreateMailValidation:
    def test_unbalanced_braces_rejected(self, admin_client, account):
        """The serializer's own validator, which nothing could ever reach before."""
        resp = admin_client.post(
            _url(account.id), _payload(message_body="Hello {first_name")
        )
        assert resp.status_code == 400
        assert "message_body" in resp.json()["errors"]

    def test_missing_from_email_rejected(self, admin_client, account):
        body = _payload()
        body.pop("from_email")
        resp = admin_client.post(_url(account.id), body)
        assert resp.status_code == 400


@pytest.mark.django_db
class TestCreateMailAccountAccess:
    """Sending from an account needs the same access as opening it."""

    @pytest.fixture
    def admins_account(self, org_a, admin_user):
        account = Account.objects.create(name="Not yours", org=org_a)
        Account.objects.filter(pk=account.pk).update(created_by=admin_user)
        return account

    def test_member_who_cannot_open_the_account_is_refused(
        self, user_client, admins_account
    ):
        """403, matching `GET` on the same account, and no mail row left."""
        assert user_client.get(f"/api/accounts/{admins_account.id}/").status_code == 403
        with mock.patch("accounts.views.send_email.delay") as dispatch:
            resp = user_client.post(_url(admins_account.id), _payload())
        assert resp.status_code == 403
        assert not AccountEmail.objects.filter(from_account=admins_account).exists()
        dispatch.assert_not_called()

    def test_assigned_member_may_send(self, user_client, admins_account, user_profile):
        admins_account.assigned_to.add(user_profile)
        with mock.patch("accounts.views.send_email.delay") as dispatch:
            resp = user_client.post(_url(admins_account.id), _payload())
        assert resp.status_code == 200, resp.content
        assert AccountEmail.objects.filter(from_account=admins_account).count() == 1
        dispatch.assert_called_once()

    def test_creating_member_may_send(self, user_client, org_a, regular_user):
        account = Account.objects.create(name="Mine", org=org_a)
        Account.objects.filter(pk=account.pk).update(created_by=regular_user)
        with mock.patch("accounts.views.send_email.delay"):
            resp = user_client.post(_url(account.id), _payload())
        assert resp.status_code == 200, resp.content
        assert AccountEmail.objects.filter(from_account=account).count() == 1


@pytest.mark.django_db
class TestCreateMailRecipientsBelongToTheAccount:
    """An account mails its own contacts, not anyone else in the org.

    Recipients were checked against the org only, so a member who may open one
    account could send mail from it to any contact in the org, including
    people they cannot open, under that account's name.
    """

    @pytest.fixture
    def stranger(self, org_a):
        return Contact.objects.create(
            first_name="Not", last_name="Linked", email="nl@example.com", org=org_a
        )

    def test_unlinked_contact_is_400_naming_it(self, admin_client, account, stranger):
        with mock.patch("accounts.views.send_email.delay") as dispatch:
            resp = admin_client.post(
                _url(account.id), _payload(recipients=[str(stranger.id)]), format="json"
            )
        assert resp.status_code == 400
        assert str(stranger.id) in str(resp.json()["errors"]["recipients"])
        assert not AccountEmail.objects.filter(from_account=account).exists()
        dispatch.assert_not_called()

    def test_one_unlinked_contact_refuses_the_whole_send(
        self, admin_client, account, contact_a, stranger
    ):
        resp = admin_client.post(
            _url(account.id),
            _payload(recipients=[str(contact_a.id), str(stranger.id)]),
            format="json",
        )
        assert resp.status_code == 400
        errors = str(resp.json()["errors"]["recipients"])
        assert str(stranger.id) in errors
        assert str(contact_a.id) not in errors
        assert not AccountEmail.objects.filter(from_account=account).exists()

    def test_contact_of_another_account_is_refused(
        self, admin_client, account, org_a, stranger
    ):
        other = Account.objects.create(name="Elsewhere", org=org_a)
        other.contacts.add(stranger)
        resp = admin_client.post(
            _url(account.id), _payload(recipients=[str(stranger.id)]), format="json"
        )
        assert resp.status_code == 400
        assert not AccountEmail.objects.filter(from_account=account).exists()

    def test_contact_linked_by_the_m2m_is_accepted(
        self, admin_client, account, contact_a
    ):
        with mock.patch("accounts.views.send_email.delay"):
            resp = admin_client.post(
                _url(account.id),
                _payload(recipients=[str(contact_a.id)]),
                format="json",
            )
        assert resp.status_code == 200, resp.content
        email = AccountEmail.objects.get(from_account=account)
        assert list(email.recipients.values_list("id", flat=True)) == [contact_a.id]

    def test_contact_linked_by_the_primary_fk_is_accepted(
        self, admin_client, account, stranger
    ):
        Contact.objects.filter(pk=stranger.pk).update(account=account)
        with mock.patch("accounts.views.send_email.delay"):
            resp = admin_client.post(
                _url(account.id),
                _payload(recipients=[str(stranger.id).upper()]),
                format="json",
            )
        assert resp.status_code == 200, resp.content
        email = AccountEmail.objects.get(from_account=account)
        assert list(email.recipients.values_list("id", flat=True)) == [stranger.id]
