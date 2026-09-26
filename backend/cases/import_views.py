"""CSV import endpoints for tickets (cases).

Preview reads the uploaded file and returns row-level validation results
without touching the DB. Commit re-runs validation and writes inside a
single transaction. Both endpoints are gated by
`common.permissions.can_mass_import` (admins, superusers, sales access)
so non-privileged members cannot mass-create cases through this surface.
"""

from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from cases.services.csv_import import commit_rows, parse_and_validate
from common.permissions import HasOrgContext, can_mass_import

MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MB; matches the UI hint


def _read_upload(request):
    """Return (file_bytes, error_response). One of the two will be None.

    The size cap is enforced against the actual bytes read, not against
    `upload.size`. `upload.size` is derived from a client-supplied
    Content-Length header for in-memory uploads and can be zero or absent even
    when the body is large, so trusting it for the limit check creates a
    bypass. `contacts.import_views` already does it this way.
    """
    upload = request.FILES.get("file")
    if not upload:
        return None, Response(
            {"error": True, "message": "No file uploaded (expected field 'file')"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    name = (upload.name or "").lower()
    if not name.endswith(".csv"):
        return None, Response(
            {"error": True, "message": "File must have a .csv extension"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    file_bytes = upload.read()
    if len(file_bytes) > MAX_UPLOAD_BYTES:
        return None, Response(
            {"error": True, "message": "File exceeds the 5 MB upload limit"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return file_bytes, None


class CaseImportPreviewView(APIView):
    permission_classes = (IsAuthenticated, HasOrgContext)
    parser_classes = (MultiPartParser,)

    @extend_schema(
        tags=["Cases"],
        request=inline_serializer(
            name="CaseImportPreviewRequest",
            fields={"file": serializers.FileField()},
        ),
        responses={
            200: inline_serializer(
                name="CaseImportPreviewResponse",
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
            return Response(
                {"error": True, "message": "Permission denied"},
                status=status.HTTP_403_FORBIDDEN,
            )
        file_bytes, err = _read_upload(request)
        if err is not None:
            return err
        result = parse_and_validate(file_bytes, request.profile.org, request.profile)
        return Response(result.to_dict(), status=status.HTTP_200_OK)


class CaseImportCommitView(APIView):
    permission_classes = (IsAuthenticated, HasOrgContext)
    parser_classes = (MultiPartParser,)

    @extend_schema(
        tags=["Cases"],
        request=inline_serializer(
            name="CaseImportCommitRequest",
            fields={"file": serializers.FileField()},
        ),
        responses={
            200: inline_serializer(
                name="CaseImportCommitResponse",
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
            return Response(
                {"error": True, "message": "Permission denied"},
                status=status.HTTP_403_FORBIDDEN,
            )
        file_bytes, err = _read_upload(request)
        if err is not None:
            return err
        result = commit_rows(file_bytes, request.profile.org, request.profile)
        http_status = (
            status.HTTP_400_BAD_REQUEST if result.get("error") else status.HTTP_200_OK
        )
        return Response(result, status=http_status)
