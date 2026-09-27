"""Bulk update / bulk delete endpoints for the Cases module."""

import uuid

from django.db import transaction
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from cases.access import has_case_write_access, is_org_admin, visible_cases_qs
from cases.models import Case
from cases.updates import update_case
from cases.workflow import DUPLICATE_BY_MERGE_ONLY, merged_status_refusal
from common.models import Activity
from common.permissions import HasOrgContext
from common.validators import payload_id_list

ALLOWED_FIELDS = {"status", "priority", "case_type", "closed_on"}
ALLOWED_M2M = {"assigned_to", "tags"}

# Scalar fields whose value must be one of the model's declared choices. A raw
# `setattr` + `save()` skips both DRF's ChoiceField and the model's `clean_fields`,
# so without this an authenticated caller could persist an off-enum status.
_CHOICE_FIELDS = ("status", "priority", "case_type")


def _valid_ids(raw):
    """Keep only the well-formed UUIDs from a client-supplied `ids` list.

    Case PKs are UUIDs, so `Case.objects.filter(pk__in=ids)` raises a
    ValidationError (a 500) the moment the queryset is evaluated on a value
    like "not-a-uuid". That evaluation happens in the bulk loops, outside any
    try/except, so a single malformed id in the request body would crash the
    whole call. A malformed id names no real case, so drop it here: this
    matches how a well-formed-but-nonexistent or other-org id already produces
    no result row rather than an error.
    """
    ids = []
    for value in raw if isinstance(raw, (list, tuple)) else []:
        try:
            ids.append(str(uuid.UUID(str(value))))
        except (ValueError, TypeError, AttributeError):
            continue
    return ids


def _refusal_outcome(case_id, errors):
    """Map a refused `update_case` to a per-record outcome.

    Keyed on the error dict's field name, not its text, so wording changes do
    not move a ticket into the wrong bucket. `status` is the approval error
    (Duplicate and off-enum values are refused for the whole batch before the
    loop, and a merged ticket is caught per ticket before the write), and
    anything else, a malformed date included, is a generic invalid. A close
    with no date is not refused: `update_case` dates it today in the org's
    timezone.
    """
    if "status" in errors:
        return {
            "id": case_id,
            "status": "approval_required",
            "detail": errors["status"],
        }
    return {"id": case_id, "status": "invalid", "detail": errors}


class BulkUpdateCasesView(APIView):
    permission_classes = (IsAuthenticated, HasOrgContext)

    def post(self, request):
        ids = _valid_ids(request.data.get("ids"))
        fields = request.data.get("fields") or {}
        if not ids:
            return Response(
                {"error": True, "errors": "ids required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not fields:
            return Response(
                {"error": True, "errors": "fields required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        unknown = set(fields) - ALLOWED_FIELDS - ALLOWED_M2M
        if unknown:
            return Response(
                {"error": True, "errors": f"Unsupported fields: {sorted(unknown)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        for field_name in _CHOICE_FIELDS:
            if field_name not in fields:
                continue
            value = fields[field_name]
            model_field = Case._meta.get_field(field_name)
            valid_values = {choice for choice, _ in model_field.choices}
            # `value in valid_values` would raise TypeError (500) on an
            # unhashable payload like `{"status": ["Closed"]}`, so gate the
            # membership test on a string first: a list/dict/number is simply
            # an invalid value and gets the clean 400.
            if isinstance(value, str) and value in valid_values:
                continue
            if value is None and model_field.null:
                continue
            return Response(
                {"error": True, "errors": f"Invalid value for '{field_name}'"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if fields.get("status") == "Duplicate":
            return Response(
                {"error": True, "errors": DUPLICATE_BY_MERGE_ONLY},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # `closed_on` is a scalar date, not a choice field, so it skips the loop
        # above. A non-string payload would setattr onto the model and blow up at
        # `save()` as a DB error (500). Reject it here with the same clean 400.
        if "closed_on" in fields:
            closed_on = fields["closed_on"]
            if closed_on is not None and not isinstance(closed_on, str):
                return Response(
                    {"error": True, "errors": "Invalid value for 'closed_on'"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        # Parsed before the first ticket is written, so a malformed id is a 400
        # for the whole batch rather than a failure half way through it.
        for m2m_field in ALLOWED_M2M:
            if m2m_field in fields:
                payload_id_list(fields[m2m_field], m2m_field)

        org = request.profile.org
        results = []
        updated_count = 0
        # Only tickets the caller may open. One they may not is left out of
        # `results` exactly as a missing id is, so the batch cannot be used to
        # learn which ids exist.
        for case in Case.objects.filter(
            org=org,
            pk__in=visible_cases_qs(request.profile).filter(pk__in=ids).values("pk"),
        ):
            # Per-case authorization, mirroring the single-case PUT path
            # (`CaseDetailView.put` calls `assert_case_write_access`). Without
            # this any org member could edit, reassign or close any case in
            # the org. Cases the caller may open but not write are reported as
            # `no_access` rather than silently skipped, so the caller can see
            # which of their selected tickets were denied.
            if not has_case_write_access(request.profile, case):
                results.append({"id": str(case.pk), "status": "no_access"})
                continue
            # The single-case rule, reported per ticket like the close gate.
            refusal = merged_status_refusal(case, fields.get("status", case.status))
            if refusal:
                results.append(
                    {
                        "id": str(case.pk),
                        "status": "merged",
                        "detail": refusal["status"],
                    }
                )
                continue
            # The single-ticket write (`update_case`, the PATCH's own path),
            # so the close gate, active-only assignees and tags, and the email
            # to whoever is newly assigned all apply here too. Tags append:
            # bulk-tagging must not wipe a ticket's other tags. `assigned_to`
            # replaces. A savepoint per case, so one ticket's failure rolls
            # back only itself and the rest of the batch still commits.
            with transaction.atomic():
                errors = update_case(request, case, fields, append_tags=True)
            if errors:
                results.append(_refusal_outcome(str(case.pk), errors))
                continue
            results.append({"id": str(case.pk), "status": "updated"})
            updated_count += 1

        return Response(
            {"error": False, "updated": updated_count, "results": results},
            status=status.HTTP_200_OK,
        )


class BulkDeleteCasesView(APIView):
    permission_classes = (IsAuthenticated, HasOrgContext)

    def post(self, request):
        ids = _valid_ids(request.data.get("ids"))
        if not ids:
            return Response(
                {"error": True, "errors": "ids required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        org = request.profile.org
        admin = is_org_admin(request.profile)
        results = []
        deletable = []
        # Deleting is admin-or-creator only (`assert_case_delete_access`); an
        # assignee may work a ticket but not erase it. A ticket the caller may
        # not open is left out of `results`, as a missing id is.
        for row in Case.objects.filter(
            org=org,
            pk__in=visible_cases_qs(request.profile).filter(pk__in=ids).values("pk"),
            is_active=True,
        ).values("id", "name", "created_by_id"):
            if admin or row["created_by_id"] == request.profile.user_id:
                deletable.append(row)
            else:
                results.append({"id": str(row["id"]), "status": "no_access"})

        deleted_count = 0
        if deletable:
            deleted_count = Case.objects.filter(
                id__in=[row["id"] for row in deletable]
            ).update(is_active=False)
            # queryset.update() bypasses signals, so emit Activity rows here.
            Activity.objects.bulk_create(
                [
                    Activity(
                        user=request.profile,
                        action="DELETE",
                        entity_type="Case",
                        entity_id=row["id"],
                        entity_name=(row["name"] or "")[:255],
                        metadata={"bulk": True},
                        org_id=org.id,
                    )
                    for row in deletable
                ]
            )
            for row in deletable:
                results.append({"id": str(row["id"]), "status": "deleted"})

        return Response(
            {"error": False, "deleted": deleted_count, "results": results},
            status=status.HTTP_200_OK,
        )
