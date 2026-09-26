"""`DELETE /api/invoices/attachments/<id>/` reaches a file only through an
invoice the caller may open.

It used to look the attachment up org-wide across every module. An uploader
who had lost access to a lead could still delete that lead's file through
this route, and anyone else got a 403 that confirmed the id. Now a file that
is missing, in another org, on another module's record, or on an invoice
this caller cannot open answers one identical 404, and is left in place.
"""

import uuid

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from accounts.models import Account
from common.models import Attachments
from invoices.models import Invoice
from leads.models import Lead


def _attach(target, uploader):
    attachment = Attachments(
        file_name="notes.txt",
        content_object=target,
        org=target.org,
        attachment=SimpleUploadedFile("notes.txt", b"hello"),
    )
    attachment.save()
    # Set without save(), which stamps created_by from the request user.
    Attachments.objects.filter(pk=attachment.pk).update(created_by=uploader)
    return attachment


def _hidden(model, pk, admin_user):
    """Owned by the admin and assigned to nobody."""
    model.objects.filter(pk=pk).update(created_by=admin_user)


@pytest.fixture
def invoice(org_a, admin_user):
    invoice = Invoice.objects.create(
        org=org_a,
        account=Account.objects.create(name="Acme", org=org_a),
        invoice_title="Work",
    )
    _hidden(Invoice, invoice.pk, admin_user)
    return invoice


def _url(pk):
    return f"/api/invoices/attachments/{pk}/"


def _like_missing(client, attachment):
    hidden = client.delete(_url(attachment.id))
    missing = client.delete(_url(uuid.uuid4()))
    assert hidden.status_code == missing.status_code == 404
    assert hidden.json() == missing.json()
    assert Attachments.objects.filter(pk=attachment.pk).exists()


@pytest.mark.django_db
class TestWhoCanReachIt:
    def test_the_uploader_of_a_hidden_leads_file_cannot_delete_it_here(
        self, user_client, regular_user, admin_user, org_a
    ):
        """The reported bypass: the member uploaded it, then lost the lead."""
        lead = Lead.objects.create(first_name="Private", last_name="Lead", org=org_a)
        _hidden(Lead, lead.pk, admin_user)
        attachment = _attach(lead, regular_user)

        _like_missing(user_client, attachment)

    def test_another_modules_file_even_for_an_admin(
        self, admin_client, admin_user, org_a
    ):
        lead = Lead.objects.create(first_name="Any", last_name="Lead", org=org_a)
        _like_missing(admin_client, _attach(lead, admin_user))

    def test_a_file_on_an_invoice_the_member_cannot_open(
        self, user_client, admin_user, invoice
    ):
        _like_missing(user_client, _attach(invoice, admin_user))

    def test_another_orgs_invoice_file(self, admin_client, user_b, org_b):
        theirs = Invoice.objects.create(
            org=org_b,
            account=Account.objects.create(name="Elsewhere", org=org_b),
            invoice_title="Theirs",
        )
        _like_missing(admin_client, _attach(theirs, user_b))


@pytest.mark.django_db
class TestWhoCanDeleteIt:
    def test_a_reader_who_did_not_upload_it_is_refused(
        self, user_client, user_profile, admin_user, invoice
    ):
        invoice.assigned_to.add(user_profile)
        attachment = _attach(invoice, admin_user)

        response = user_client.delete(_url(attachment.id))

        assert response.status_code == 403
        assert Attachments.objects.filter(pk=attachment.pk).exists()

    def test_the_uploader_who_can_read_it_may(
        self, user_client, user_profile, regular_user, invoice
    ):
        invoice.assigned_to.add(user_profile)
        attachment = _attach(invoice, regular_user)

        response = user_client.delete(_url(attachment.id))

        assert response.status_code == 200, response.content
        assert not Attachments.objects.filter(pk=attachment.pk).exists()

    def test_an_admin_may(self, admin_client, regular_user, invoice):
        attachment = _attach(invoice, regular_user)

        assert admin_client.delete(_url(attachment.id)).status_code == 200
