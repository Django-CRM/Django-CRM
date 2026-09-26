"""Downloading a stored file, and being refused one.

Before these views existed, the only route to an uploaded file was its
`/media/` path, guarded by `RLSContextMiddleware` alone. That asks for *an*
org context rather than *the* org, so it answered 200 with the bytes to a
member of another tenant while `/api/documents/<id>/` answered that same
caller 404. These tests pin the replacement: the download answers exactly the
people the record's own read predicate answers.

Every check asserts BOTH directions. A permission test that only ever sees
one answer cannot tell a working check from a constant.
"""

import uuid

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from accounts.models import Account
from cases.models import Case, CaseWatcher
from common.models import Attachments, Document, Teams
from common.utils import create_attachment
from contacts.models import Contact
from invoices.models import Invoice
from leads.models import Lead
from opportunity.models import Opportunity
from tasks.models import Task

FILE_BYTES = b"the quick brown fox"


def _doc(org, user, **kw):
    """A Document owned by `user`. `created_by` is a **User** FK.

    The second step is not optional: `BaseModel.save()` overwrites
    `created_by` from crum's `get_current_user()`, which is None outside a
    request, so passing it to `create()` has no effect.
    """
    doc = Document.objects.create(
        title=kw.pop("title", "Doc"),
        document_file=SimpleUploadedFile("probe.txt", FILE_BYTES),
        status=kw.pop("status", "active"),
        org=org,
        **kw,
    )
    Document.objects.filter(pk=doc.pk).update(created_by=user)
    doc.refresh_from_db()
    return doc


def _doc_url(pk):
    return f"/api/documents/{pk}/download/"


def _attachment_url(pk):
    return f"/api/attachments/{pk}/download/"


def _body(response):
    return b"".join(response.streaming_content)


def _attach(record, profile):
    """Attach a file to `record`, as the record's own upload path does."""
    return create_attachment(
        SimpleUploadedFile("probe.txt", FILE_BYTES), record, profile
    )


@pytest.mark.django_db
class TestDocumentDownload:
    def test_uploader_gets_the_bytes(self, user_client, regular_user, org_a):
        doc = _doc(org_a, regular_user, title="Mine")
        response = user_client.get(_doc_url(doc.pk))
        assert response.status_code == 200
        assert _body(response) == FILE_BYTES

    def test_a_member_it_is_not_shared_with_is_refused(
        self, user_client, admin_user, org_a
    ):
        """The pair that made this worth building.

        This caller gets 403 from `/api/documents/<id>/` and used to get 200
        with the contents from the file's `/media/` path.
        """
        doc = _doc(org_a, admin_user, title="Not mine")
        assert user_client.get(f"/api/documents/{doc.pk}/").status_code == 403
        assert user_client.get(_doc_url(doc.pk)).status_code == 403

    def test_a_share_is_enough_to_download(
        self, user_client, admin_user, org_a, user_profile
    ):
        doc = _doc(org_a, admin_user, title="Shared with me")
        doc.shared_to.add(user_profile)
        response = user_client.get(_doc_url(doc.pk))
        assert response.status_code == 200
        assert _body(response) == FILE_BYTES

    def test_a_team_share_is_enough_to_download(
        self, user_client, admin_user, org_a, user_profile
    ):
        doc = _doc(org_a, admin_user, title="Shared with my team")
        team = Teams.objects.create(name="Support", org=org_a, created_by=admin_user)
        team.users.add(user_profile)
        doc.teams.add(team)
        assert user_client.get(_doc_url(doc.pk)).status_code == 200

    def test_an_admin_downloads_anything_in_their_org(
        self, admin_client, regular_user, org_a
    ):
        doc = _doc(org_a, regular_user, title="Somebody else's")
        assert admin_client.get(_doc_url(doc.pk)).status_code == 200

    def test_another_org_gets_404_not_the_file(self, org_b_client, admin_user, org_a):
        """404, not 403: an id in another tenant is an id that does not exist."""
        doc = _doc(org_a, admin_user, title="Theirs")
        assert org_b_client.get(_doc_url(doc.pk)).status_code == 404

    def test_anonymous_is_refused(self, unauthenticated_client, admin_user, org_a):
        doc = _doc(org_a, admin_user)
        assert unauthenticated_client.get(_doc_url(doc.pk)).status_code == 403

    def test_a_document_with_no_file_is_404_not_500(
        self, admin_client, admin_user, org_a
    ):
        doc = _doc(org_a, admin_user)
        Document.objects.filter(pk=doc.pk).update(document_file="")
        assert admin_client.get(_doc_url(doc.pk)).status_code == 404

    def test_the_download_is_named_after_the_title(
        self, admin_client, admin_user, org_a
    ):
        """The stored path is a timestamped upload name nobody chose."""
        doc = _doc(org_a, admin_user, title="Q3 pricing")
        response = admin_client.get(_doc_url(doc.pk))
        assert "Q3 pricing" in response["Content-Disposition"]


@pytest.mark.django_db
class TestAttachmentDownload:
    def test_lead_creator_gets_the_bytes(
        self, user_client, regular_user, user_profile, org_a
    ):
        lead = Lead.objects.create(title="Mine", org=org_a)
        Lead.objects.filter(pk=lead.pk).update(created_by=regular_user)
        attachment = _attach(lead, user_profile)
        response = user_client.get(_attachment_url(attachment.pk))
        assert response.status_code == 200
        assert _body(response) == FILE_BYTES

    def test_a_member_who_cannot_read_the_lead_cannot_read_its_file(
        self, user_client, admin_user, admin_profile, org_a
    ):
        lead = Lead.objects.create(title="Not mine", org=org_a)
        Lead.objects.filter(pk=lead.pk).update(created_by=admin_user)
        attachment = _attach(lead, admin_profile)
        # The lead answers as if missing, and so does its file.
        assert user_client.get(f"/api/leads/{lead.pk}/").status_code == 404
        assert user_client.get(_attachment_url(attachment.pk)).status_code == 404

    def test_assignment_to_the_lead_is_enough(
        self, user_client, admin_user, admin_profile, user_profile, org_a
    ):
        lead = Lead.objects.create(title="Assigned to me", org=org_a)
        Lead.objects.filter(pk=lead.pk).update(created_by=admin_user)
        lead.assigned_to.add(user_profile)
        attachment = _attach(lead, admin_profile)
        assert user_client.get(_attachment_url(attachment.pk)).status_code == 200

    def test_a_ticket_watcher_may_download(
        self, user_client, admin_user, admin_profile, user_profile, org_a
    ):
        """Watching is read access on a case, so it is read access to its file."""
        case = Case.objects.create(name="Watched", org=org_a, status="New")
        Case.objects.filter(pk=case.pk).update(created_by=admin_user)
        attachment = _attach(case, admin_profile)
        assert user_client.get(_attachment_url(attachment.pk)).status_code == 404
        # `watchers` goes through CaseWatcher, which carries its own org FK.
        CaseWatcher.objects.create(case=case, profile=user_profile, org=org_a)
        assert user_client.get(_attachment_url(attachment.pk)).status_code == 200

    def test_a_task_assignee_may_download_and_a_stranger_may_not(
        self, user_client, admin_user, admin_profile, user_profile, org_a
    ):
        task = Task.objects.create(
            title="Ship it", org=org_a, status="New", priority="Low"
        )
        Task.objects.filter(pk=task.pk).update(created_by=admin_user)
        attachment = _attach(task, admin_profile)
        assert user_client.get(_attachment_url(attachment.pk)).status_code == 404
        task.assigned_to.add(user_profile)
        assert user_client.get(_attachment_url(attachment.pk)).status_code == 200

    def test_an_account_creator_may_download(
        self, user_client, regular_user, user_profile, org_a
    ):
        account = Account.objects.create(name="Acme", org=org_a)
        Account.objects.filter(pk=account.pk).update(created_by=regular_user)
        attachment = _attach(account, user_profile)
        assert user_client.get(_attachment_url(attachment.pk)).status_code == 200

    def test_another_org_gets_404(self, org_b_client, admin_user, admin_profile, org_a):
        lead = Lead.objects.create(title="Theirs", org=org_a)
        Lead.objects.filter(pk=lead.pk).update(created_by=admin_user)
        attachment = _attach(lead, admin_profile)
        assert org_b_client.get(_attachment_url(attachment.pk)).status_code == 404

    def test_anonymous_is_refused(
        self, unauthenticated_client, admin_user, admin_profile, org_a
    ):
        lead = Lead.objects.create(title="Any", org=org_a)
        Lead.objects.filter(pk=lead.pk).update(created_by=admin_user)
        attachment = _attach(lead, admin_profile)
        assert (
            unauthenticated_client.get(_attachment_url(attachment.pk)).status_code
            == 403
        )

    def test_an_orphaned_attachment_is_refused_rather_than_served(
        self, admin_client, admin_user, admin_profile, org_a
    ):
        """A dangling `object_id` is a record nobody can be checked against.

        Deny by default: there is no parent to ask, so there is no answer that
        means yes.
        """
        lead = Lead.objects.create(title="About to go", org=org_a)
        Lead.objects.filter(pk=lead.pk).update(created_by=admin_user)
        attachment = _attach(lead, admin_profile)
        Lead.objects.filter(pk=lead.pk).delete()
        assert admin_client.get(_attachment_url(attachment.pk)).status_code == 404

    def test_an_unmapped_content_type_is_refused(
        self, admin_client, admin_profile, org_a
    ):
        """A model nobody registered is refused, admin or not.

        The other default would hand out a file the moment somebody makes a
        new model attachable and forgets this map.
        """
        team = Teams.objects.create(name="Support", org=org_a)
        attachment = _attach(team, admin_profile)
        assert attachment.content_type.model == "teams"
        assert admin_client.get(_attachment_url(attachment.pk)).status_code == 404

    def test_the_download_is_named_after_the_stored_file_name(
        self, admin_client, admin_user, admin_profile, org_a
    ):
        lead = Lead.objects.create(title="Named", org=org_a)
        Lead.objects.filter(pk=lead.pk).update(created_by=admin_user)
        attachment = _attach(lead, admin_profile)
        response = admin_client.get(_attachment_url(attachment.pk))
        assert attachment.file_name in response["Content-Disposition"]

    def test_an_attachment_row_with_no_file_is_404_not_500(
        self, admin_client, admin_user, admin_profile, org_a
    ):
        lead = Lead.objects.create(title="Empty", org=org_a)
        Lead.objects.filter(pk=lead.pk).update(created_by=admin_user)
        attachment = _attach(lead, admin_profile)
        Attachments.objects.filter(pk=attachment.pk).update(attachment="")
        assert admin_client.get(_attachment_url(attachment.pk)).status_code == 404


def _record(kind, org):
    """One record of each attachable kind, owned by nobody in particular."""
    if kind == "lead":
        return Lead.objects.create(title="Hidden", org=org)
    if kind == "opportunity":
        return Opportunity.objects.create(name="Hidden", stage="QUALIFICATION", org=org)
    if kind == "contact":
        return Contact.objects.create(first_name="Hidden", last_name="Person", org=org)
    if kind == "account":
        return Account.objects.create(name="Hidden", org=org)
    if kind == "case":
        return Case.objects.create(name="Hidden", org=org, status="New")
    if kind == "task":
        return Task.objects.create(
            title="Hidden", org=org, status="New", priority="Low"
        )
    account = Account.objects.create(name="Billed", org=org)
    return Invoice.objects.create(org=org, account=account, invoice_title="Hidden")


KINDS = ["lead", "opportunity", "contact", "account", "case", "task", "invoice"]


@pytest.mark.django_db
class TestHiddenFileAnswersLikeAMissingOne:
    """A file on a record the caller cannot open is indistinguishable from an id
    that does not exist: same status, same body, on every module's records."""

    @pytest.mark.parametrize("kind", KINDS)
    def test_whole_response_matches_a_missing_id(
        self, kind, user_client, admin_client, admin_user, admin_profile, org_a
    ):
        record = _record(kind, org_a)
        type(record).objects.filter(pk=record.pk).update(created_by=admin_user)
        attachment = _attach(record, admin_profile)

        hidden = user_client.get(_attachment_url(attachment.pk))
        missing = user_client.get(_attachment_url(uuid.uuid4()))
        assert hidden.status_code == missing.status_code == 404
        assert hidden.content == missing.content
        assert hidden["Content-Type"] == missing["Content-Type"]

        # The check refuses the member, not everyone: the admin gets the bytes.
        allowed = admin_client.get(_attachment_url(attachment.pk))
        assert allowed.status_code == 200
        assert _body(allowed) == FILE_BYTES

    def test_another_orgs_file_matches_a_missing_id(
        self, org_b_client, admin_user, admin_profile, org_a
    ):
        record = _record("case", org_a)
        attachment = _attach(record, admin_profile)
        other = org_b_client.get(_attachment_url(attachment.pk))
        missing = org_b_client.get(_attachment_url(uuid.uuid4()))
        assert other.status_code == missing.status_code == 404
        assert other.content == missing.content
