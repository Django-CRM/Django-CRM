import logging

from django.core.exceptions import ValidationError
from django.db import IntegrityError
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.lookups import get_scoped_or_404
from common.models import APISettings, Attachments, Comment
from common.permissions import HasOrgContext, is_org_admin
from common.request_meta import client_ip, referer
from common.serializer import LeadCommentSerializer
from contacts.models import Contact
from leads import swagger_params
from leads.forms import LeadListForm
from leads.models import Lead
from leads.serializer import (
    CreateLeadFromSiteSwaggerSerializer,
    LeadCommentEditSwaggerSerializer,
    LeadUploadSwaggerSerializer,
)
from leads.tasks import create_lead_from_file
from webforms.dynamic_serializer import build_serializer
from webforms.legacy import ensure_web_form
from webforms.service import submit_form
from webforms.tasks import send_webform_submission_email

logger = logging.getLogger(__name__)

# Matches the contacts and cases importers, and the UI hint beside the control.
MAX_UPLOAD_BYTES = 5 * 1024 * 1024


def _can_import(profile) -> bool:
    """Mass-create requires admin or explicit sales-access permission.

    Same rule as `contacts.import_views._can_import` and its cases twin. This
    endpoint predates both and never grew the check, so any member could
    bulk-create leads through it.
    """
    if profile is None:
        return False
    if getattr(profile, "role", None) == "ADMIN":
        return True
    if getattr(profile, "is_admin", False):
        return True
    return bool(getattr(profile, "has_sales_access", False))


class LeadUploadView(APIView):
    model = Lead
    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        tags=["Leads"],
        parameters=swagger_params.organization_params,
        request=LeadUploadSwaggerSerializer,
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
        if not _can_import(request.profile):
            return Response(
                {"error": True, "errors": "Admin access required"},
                status=status.HTTP_403_FORBIDDEN,
            )
        # The cap is measured against the bytes actually read, not against
        # `upload.size`, which for an in-memory upload derives from a
        # client-supplied Content-Length and can understate the body.
        upload = request.FILES.get("leads_file")
        if upload is not None:
            file_bytes = upload.read()
            upload.seek(0)
            if len(file_bytes) > MAX_UPLOAD_BYTES:
                return Response(
                    {"error": True, "errors": "File exceeds the 5 MB upload limit"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        lead_form = LeadListForm(request.POST, request.FILES)
        if lead_form.is_valid():
            create_lead_from_file.delay(
                lead_form.validated_rows,
                lead_form.invalid_rows,
                request.profile.id,
                request.get_host(),
                request.profile.org.id,
            )
            return Response(
                {"error": False, "message": "Leads created Successfully"},
                status=status.HTTP_200_OK,
            )
        return Response(
            {"error": True, "errors": lead_form.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )


class LeadCommentView(APIView):
    model = Comment
    permission_classes = (IsAuthenticated, HasOrgContext)

    def get_object(self, pk):
        return get_scoped_or_404(self.model, pk, self.request.profile.org)

    @extend_schema(
        tags=["Leads"],
        parameters=swagger_params.organization_params,
        request=LeadCommentEditSwaggerSerializer,
        responses={
            200: inline_serializer(
                name="LeadCommentUpdateResponse",
                fields={
                    "error": serializers.BooleanField(),
                    "message": serializers.CharField(),
                },
            )
        },
    )
    def put(self, request, pk, format=None):
        params = request.data
        obj = self.get_object(pk)
        if (
            is_org_admin(request.profile)
            or request.user.is_superuser
            or request.profile == obj.commented_by
        ):
            serializer = LeadCommentSerializer(obj, data=params)
            if serializer.is_valid():
                serializer.save()
                return Response(
                    {"error": False, "message": "Comment Submitted"},
                    status=status.HTTP_200_OK,
                )
            return Response(
                {"error": True, "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(
            {
                "error": True,
                "errors": "You don't have permission to perform this action",
            },
            status=status.HTTP_403_FORBIDDEN,
        )

    @extend_schema(
        tags=["Leads"],
        parameters=swagger_params.organization_params,
        request=LeadCommentEditSwaggerSerializer,
        description="Partial Comment Update",
        responses={
            200: inline_serializer(
                name="LeadCommentPatchResponse",
                fields={
                    "error": serializers.BooleanField(),
                    "message": serializers.CharField(),
                },
            )
        },
    )
    def patch(self, request, pk, format=None):
        """Handle partial updates to a comment."""
        params = request.data
        obj = self.get_object(pk)
        if (
            is_org_admin(request.profile)
            or request.user.is_superuser
            or request.profile == obj.commented_by
        ):
            serializer = LeadCommentSerializer(obj, data=params, partial=True)
            if serializer.is_valid():
                serializer.save()
                return Response(
                    {"error": False, "message": "Comment Updated"},
                    status=status.HTTP_200_OK,
                )
            return Response(
                {"error": True, "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(
            {
                "error": True,
                "errors": "You don't have permission to perform this action",
            },
            status=status.HTTP_403_FORBIDDEN,
        )

    @extend_schema(
        tags=["Leads"],
        parameters=swagger_params.organization_params,
        responses={
            200: inline_serializer(
                name="LeadCommentDeleteResponse",
                fields={
                    "error": serializers.BooleanField(),
                    "message": serializers.CharField(),
                },
            )
        },
    )
    def delete(self, request, pk, format=None):
        self.object = self.get_object(pk)
        if (
            is_org_admin(request.profile)
            or request.user.is_superuser
            or request.profile == self.object.commented_by
        ):
            self.object.delete()
            return Response(
                {"error": False, "message": "Comment Deleted Successfully"},
                status=status.HTTP_200_OK,
            )

        return Response(
            {
                "error": True,
                "errors": "You do not have permission to perform this action",
            },
            status=status.HTTP_403_FORBIDDEN,
        )


class LeadAttachmentView(APIView):
    model = Attachments
    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        tags=["Leads"],
        parameters=swagger_params.organization_params,
        responses={
            200: inline_serializer(
                name="LeadAttachmentDeleteResponse",
                fields={
                    "error": serializers.BooleanField(),
                    "message": serializers.CharField(),
                },
            )
        },
    )
    def delete(self, request, pk, format=None):
        # Was `objects.get(pk=pk)`: no org filter, so an admin of any org could
        # delete any attachment in the system by id, and a missing or malformed
        # id raised out of the view as a 500 instead of answering 404. The
        # comment view above already scopes its lookup, as do the accounts,
        # contacts, cases, tasks, tags and teams equivalents.
        self.object = get_scoped_or_404(self.model, pk, request.profile.org)
        if (
            is_org_admin(request.profile)
            or request.user.is_superuser
            or request.profile.user == self.object.created_by
        ):
            self.object.delete()
            return Response(
                {"error": False, "message": "Attachment Deleted Successfully"},
                status=status.HTTP_200_OK,
            )
        return Response(
            {
                "error": True,
                "errors": "You don't have permission to perform this action",
            },
            status=status.HTTP_403_FORBIDDEN,
        )


class CreateLeadFromSite(APIView):
    """DEPRECATED. Use `/api/public/forms/<org_id>/<form_id>/submit/` instead.

    This is the original web-to-lead endpoint. It is kept working, and its
    request and response bodies are unchanged, but everything new should use
    the public web form endpoint, which is genuinely anonymous, domain-bound,
    rate-limited and captcha-capable.

    WHAT CHANGED HERE, AND WHAT DID NOT

    The body of this view now hands off to `webforms.service.submit_form`, the
    same write path the public endpoint uses. That is what repairs the six
    defects it carried, each of which now has a test in
    `leads/tests/test_create_lead_from_site.py`:

      * it 500'd on EVERY successful call. `Lead.assigned_to` is an M2M to
        Profile and this passed `api_setting.created_by`, which is a User, so
        `assigned_to.add()` raised TypeError. The endpoint has never returned
        200 in any configuration.
      * a repeat submission from one address raised IntegrityError against
        `Lead`'s `UniqueConstraint(Lower("email"), "org")`.
      * `APISettings.lead_assigned_to` was never read, so the configured
        recipients were notified of nothing.
      * `APISettings.tags` was never applied to the lead.
      * `Lead.objects.create()` skipped serializer validation, so a malformed
        email or an over-length value reached the database.
      * `Lead.source` was assigned `api_setting.website`, a URL, while that
        column declares `choices=LEAD_SOURCE`. Those rows were invisible to
        every source-based filter.

    REACHABILITY IS UNCHANGED, deliberately. This still requires a JWT or an
    org API key. `apiSettings` is an RLS-scoped table, so an anonymous caller
    cannot look up its own key without either carving that table out of RLS or
    moving the org into the URL, and neither is worth doing to an endpoint the
    public one replaces.

    One narrowing: the key lookup is filtered on `request.org`. RLS made that
    true in production already, since the lookup runs under the caller's own
    context, but the ORM filter is the contract and without it the endpoint
    behaved differently on a superuser database than on a correct one.

    The backing form is provisioned on first use by
    `webforms.legacy.ensure_web_form`, not assumed. Migration 0002 covers the
    keys that predate this feature, but it is a one-shot: nothing else ever set
    `legacy_api_setting`, so a key minted afterwards through the settings
    screen had no form and this endpoint refused it permanently.
    """

    # Legacy request parameter -> the Lead column it has always meant. Neither
    # name matches its target, which is exactly why this map is explicit:
    # `title` here is an honorific, NOT `Lead.title` (the subject line).
    LEGACY_PARAM_MAP = {
        "title": "salutation",
        "message": "description",
    }

    @extend_schema(
        tags=["Leads"],
        deprecated=True,
        summary="Deprecated: use the public web form endpoint",
        description=(
            "Superseded by POST /api/public/forms/<org_id>/<form_id>/submit/, "
            "which is anonymous, origin-restricted, rate-limited and supports "
            "a captcha. This endpoint is kept for existing integrations and "
            "still requires an authenticated caller."
        ),
        parameters=swagger_params.organization_params,
        request=CreateLeadFromSiteSwaggerSerializer,
        responses={
            200: inline_serializer(
                name="CreateLeadFromSiteResponse",
                fields={
                    "error": serializers.BooleanField(),
                    "message": serializers.CharField(),
                },
            )
        },
    )
    def post(self, request, *args, **kwargs):
        params = request.data
        # Scoped to the caller's org. `apiSettings` is RLS-protected so a
        # stranger's key is already invisible here, but RLS is the safety net
        # and the explicit filter is the contract.
        api_setting = APISettings.objects.filter(
            apikey=params.get("apikey"), org=request.org
        ).first()
        if not api_setting:
            return Response(
                {
                    "error": True,
                    "message": "You don't have permission, please contact the admin!.",
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        # Provisioned on first use rather than assumed to exist. Migration 0002
        # covers the keys that predate this feature; nothing else ever set
        # `legacy_api_setting`, so a key minted through the settings screen
        # afterwards would otherwise have no form and this endpoint would
        # refuse it for good.
        form = ensure_web_form(api_setting)

        submitted = {
            self.LEGACY_PARAM_MAP.get(key, key): value
            for key, value in params.items()
            if key != "apikey"
        }
        serializer = build_serializer(form)(data=submitted)
        if not serializer.is_valid():
            # The legacy body is a flat {"error", "message"} pair and callers
            # parse it. Field-level errors are not folded in, because that
            # would change the response shape of a deprecated endpoint.
            return Response(
                {"error": True, "message": "Invalid data"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        submission = submit_form(
            form,
            serializer.lead_values(),
            custom_fields=serializer.custom_values(),
            ip=client_ip(request),
            referer=referer(request),
        )
        send_webform_submission_email.delay(str(submission.id), str(api_setting.org_id))

        # Contact creation is preserved. Removing it would be a behaviour
        # change on a deprecated endpoint that live integrations may rely on.
        # The bare `except Exception: pass` it used to sit behind is gone: a
        # failure here is now logged rather than swallowed.
        self._attach_contact(api_setting, form, submission, serializer)

        return Response(
            {"error": False, "message": "Lead Created sucessfully."},
            status=status.HTTP_200_OK,
        )

    def _attach_contact(self, api_setting, form, submission, serializer):
        if submission.lead is None:
            return
        values = serializer.lead_values()
        email = values.get("email")
        if not email:
            # `get_or_create(email=None)` matches the FIRST contact in the org
            # with a null email and links it to this lead, which is somebody
            # else's record. A submission with no address has nothing to
            # identify a contact by, so it gets none.
            return
        try:
            contact, _ = Contact.objects.get_or_create(
                org=api_setting.org,
                email=email,
                defaults={
                    "first_name": values.get("first_name") or "",
                    "last_name": values.get("last_name") or "",
                    "phone": values.get("phone") or "",
                    "description": values.get("description") or "",
                    "created_by": form.created_by,
                    "is_active": True,
                },
            )
            if form.assign_to is not None:
                contact.assigned_to.add(form.assign_to)
            submission.lead.contacts.add(contact)
        except (IntegrityError, ValidationError, ValueError):
            logger.warning(
                "Could not attach a contact for web-to-lead submission %s",
                submission.id,
                exc_info=True,
            )
