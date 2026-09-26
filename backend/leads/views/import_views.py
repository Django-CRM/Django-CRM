"""CSV import endpoints for leads: preview, then commit, plus the deprecated
single-step `upload/` alias over the same commit path.

The same contract as `contacts.import_views` and `cases.import_views`, which
is what the web import drawers speak. All three endpoints are gated by
`common.permissions.can_mass_import`, so a member without admin or sales
access cannot mass-create leads here even though they can create them one at
a time.
"""

from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.permissions import HasOrgContext, can_mass_import
from leads.csv_import import commit_rows, parse_and_validate

MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MB; matches the UI hint


def _read_upload(request, field="file", csv_name=True):
    """Return (file_bytes, problem); exactly one is None.

    The cap is measured on the bytes actually read. `upload.size` comes from a
    client-supplied Content-Length for in-memory uploads and can understate
    the body. `csv_name=False` skips the extension check for `upload/`, which
    never had one; the content is parsed as CSV either way.
    """
    upload = request.FILES.get(field)
    if not upload:
        return None, f"No file uploaded (expected field '{field}')"
    if csv_name and not (upload.name or "").lower().endswith(".csv"):
        return None, "File must have a .csv extension"
    file_bytes = upload.read(MAX_UPLOAD_BYTES + 1)
    if len(file_bytes) > MAX_UPLOAD_BYTES:
        return None, "File exceeds the 5 MB upload limit"
    return file_bytes, None


def _as_utf8(file_bytes):
    """1.9.2's `upload/` read every file as ISO-8859-1, so a file saved in a
    legacy encoding (Excel's default CSV, for one) imported there. UTF-8 is
    still tried first; anything else is read the way 1.9.2 read it."""
    try:
        file_bytes.decode("utf-8")
        return file_bytes
    except UnicodeDecodeError:
        return file_bytes.decode("iso-8859-1").encode("utf-8")


def _bad_upload(problem):
    return Response(
        {"error": True, "message": problem}, status=status.HTTP_400_BAD_REQUEST
    )


def _forbidden():
    return Response(
        {"error": True, "message": "Permission denied"},
        status=status.HTTP_403_FORBIDDEN,
    )


class LeadImportPreviewView(APIView):
    permission_classes = (IsAuthenticated, HasOrgContext)
    parser_classes = (MultiPartParser,)

    @extend_schema(
        tags=["Leads"],
        request=inline_serializer(
            name="LeadImportPreviewRequest",
            fields={"file": serializers.FileField()},
        ),
        responses={
            200: inline_serializer(
                name="LeadImportPreviewResponse",
                fields={
                    "header_error": serializers.CharField(allow_null=True),
                    "valid": serializers.ListField(child=serializers.DictField()),
                    "errors": serializers.ListField(child=serializers.DictField()),
                    "summary": serializers.DictField(),
                },
            )
        },
    )
    def post(self, request, *args, **kwargs):
        if not can_mass_import(request.profile):
            return _forbidden()
        file_bytes, problem = _read_upload(request)
        if problem:
            return _bad_upload(problem)
        result = parse_and_validate(file_bytes, request)
        return Response(result.to_dict(), status=status.HTTP_200_OK)


class LeadImportCommitView(APIView):
    permission_classes = (IsAuthenticated, HasOrgContext)
    parser_classes = (MultiPartParser,)

    @extend_schema(
        tags=["Leads"],
        request=inline_serializer(
            name="LeadImportCommitRequest",
            fields={"file": serializers.FileField()},
        ),
        responses={
            200: inline_serializer(
                name="LeadImportCommitResponse",
                fields={
                    "error": serializers.BooleanField(),
                    "created": serializers.IntegerField(),
                    "ids": serializers.ListField(child=serializers.CharField()),
                },
            )
        },
    )
    def post(self, request, *args, **kwargs):
        if not can_mass_import(request.profile):
            return _forbidden()
        file_bytes, problem = _read_upload(request)
        if problem:
            return _bad_upload(problem)
        result = commit_rows(file_bytes, request)
        http_status = (
            status.HTTP_400_BAD_REQUEST if result.get("error") else status.HTTP_200_OK
        )
        return Response(result, status=http_status)


class LeadUploadView(APIView):
    """Deprecated: use `import/preview/` then `import/commit/`.

    The original single-step uploader, kept so existing API clients do not
    break. It keeps its URL, its `leads_file` field, its 403, its response
    shapes and the files it accepted: 1.9.2's column names, any file name,
    and ISO-8859-1 content (see `csv_import.parse_and_validate(legacy=True)`).
    It runs the same validator and all-or-nothing write as
    `LeadImportCommitView` and is now synchronous: a 200 means every row was
    created, and any invalid row is a 400 that names it and creates nothing.
    """

    permission_classes = (IsAuthenticated, HasOrgContext)
    parser_classes = (MultiPartParser,)

    @extend_schema(
        tags=["Leads"],
        deprecated=True,
        description=(
            "Deprecated in favour of `import/preview/` and `import/commit/`. "
            "Accepts the 1.9.2 column names and ignores columns it does not "
            "know. Synchronous: validates with the import/commit row rules and "
            "creates every row or none."
        ),
        request=inline_serializer(
            name="LeadUploadRequest",
            fields={"leads_file": serializers.FileField()},
        ),
        responses={
            200: inline_serializer(
                name="LeadUploadResponse",
                fields={
                    "error": serializers.BooleanField(),
                    "message": serializers.CharField(),
                },
            )
        },
    )
    def post(self, request, *args, **kwargs):
        if not can_mass_import(request.profile):
            return Response(
                {"error": True, "errors": "Admin access required"},
                status=status.HTTP_403_FORBIDDEN,
            )
        file_bytes, problem = _read_upload(request, "leads_file", csv_name=False)
        if problem:
            return Response(
                {"error": True, "errors": problem}, status=status.HTTP_400_BAD_REQUEST
            )
        result = commit_rows(_as_utf8(file_bytes), request, legacy=True)
        if result.get("error"):
            return Response(
                {
                    "error": True,
                    "errors": result.get("errors")
                    or result.get("header_error")
                    or result.get("message"),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(
            {"error": False, "message": "Leads created Successfully"},
            status=status.HTTP_200_OK,
        )
