"""Downloading a file attached to a record.

`Attachments` is generic: one table hangs off leads, deals, contacts,
accounts, tickets, tasks and invoices through a ContentType. Until this view
existed there was no authenticated way to fetch the bytes, so every client
built the file's `/media/` path instead and offered that as the download.
That path is guarded by `RLSContextMiddleware` alone, which requires *an* org
context rather than *the* org, so it refuses an anonymous caller and waves
through every authenticated one, of any tenant. It also refuses the ordinary
case, because a link opened in the phone's browser or a plain `<a href>` from
the web app carries no Authorization header at all. Both clients were
therefore offering a download that could not work and, where it did work,
worked for the wrong people.

**Reading an attachment is reading the record it hangs off**, so this view
asks that record's own read rule rather than inventing a second one: each
module's `visible_*_qs`, the queryset its list and detail views use. A file on
a record the caller cannot open answers the same 404 as an id that does not
exist, so the download cannot confirm the record or the file is there. A
content type not in the map, and an attachment whose record is gone, answer
that 404 too: a new attachable model must opt in here deliberately, because
the failure mode of the other default is handing out somebody's file.
"""

from django.http import FileResponse, Http404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from common import swagger_params
from common.lookups import get_on_visible_record_or_404, get_scoped_or_404
from common.models import Attachments
from common.permissions import HasOrgContext


def _visible():
    """content_type.model -> the records of that kind the caller may open.

    Built lazily inside the function because these modules import from
    `common`, and importing them at `common.views` module level would close
    the circle.
    """
    from accounts.access import visible_accounts_qs
    from cases.access import visible_cases_qs
    from contacts.access import visible_contacts_qs
    from invoices.permissions import visible_invoices_qs
    from leads.access import visible_leads_qs
    from opportunity.access import visible_deals_qs
    from tasks.access import visible_tasks_qs

    return {
        "lead": lambda request: visible_leads_qs(request.profile),
        "opportunity": lambda request: visible_deals_qs(request.profile),
        "contact": lambda request: visible_contacts_qs(request.profile),
        "account": lambda request: visible_accounts_qs(request.profile),
        "case": lambda request: visible_cases_qs(request.profile),
        "task": lambda request: visible_tasks_qs(request.profile),
        "invoice": lambda request: visible_invoices_qs(request.profile),
    }


def get_readable_attachment_or_404(request, pk):
    """The attachment ``pk`` if the caller may open the record it hangs off.

    Raises the same 404 for a missing id, another org's file, a file on a
    record the caller cannot read, an unmapped content type and a record that
    has been deleted: the second lookup only finds the row through the
    module's read-rule queryset, which holds none of those.
    """
    org = request.profile.org
    found = get_scoped_or_404(Attachments, pk, org)
    visible = _visible().get(found.content_type.model)
    if visible is None:
        raise Http404(f"No such {Attachments._meta.verbose_name}.")
    return get_on_visible_record_or_404(Attachments, pk, org, visible(request))


class AttachmentDownloadView(APIView):
    """`GET /api/attachments/<pk>/download/`, streamed with the file's name."""

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        tags=["Attachments"],
        operation_id="attachments_download",
        parameters=swagger_params.organization_params,
        responses={(200, "application/octet-stream"): OpenApiTypes.BINARY},
    )
    def get(self, request, pk, format=None):
        attachment = get_readable_attachment_or_404(request, pk)
        if not attachment.attachment:
            raise Http404("That attachment has no file.")
        attachment.attachment.open("rb")
        return FileResponse(
            attachment.attachment,
            as_attachment=True,
            filename=attachment.file_name or "attachment",
        )
