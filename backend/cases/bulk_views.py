"""Bulk update / bulk delete endpoints for the Cases module."""

import uuid

from django.core.exceptions import ValidationError
from django.db import transaction
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from cases.access import has_case_write_access, is_org_admin
from cases.models import Case
from common.models import Activity, Profile, Tags
from common.permissions import HasOrgContext

ALLOWED_FIELDS = {"status", "priority", "case_type", "closed_on"}
ALLOWED_M2M = {"assigned_to": Profile, "tags": Tags}

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


def _close_gate_outcome(case_id, exc):
    """Map a `Case.clean()` ValidationError to a per-record outcome.

    Keyed on the message dict's field name, not its text, so wording changes do
    not move a ticket into the wrong bucket. `status` is the approval error,
    `closed_on` is the missing-date error, anything else is a generic invalid.
    """
    detail = (
        exc.message_dict if hasattr(exc, "message_dict") else {"error": exc.messages}
    )
    if "status" in detail:
        return {
            "id": case_id,
            "status": "approval_required",
            "detail": detail["status"],
        }
    if "closed_on" in detail:
        return {"id": case_id, "status": "closed_on_required"}
    return {"id": case_id, "status": "invalid", "detail": detail}


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

        unknown = set(fields) - ALLOWED_FIELDS - set(ALLOWED_M2M)
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

        scalar_updates = {k: v for k, v in fields.items() if k in ALLOWED_FIELDS}
        m2m_updates = {k: v for k, v in fields.items() if k in ALLOWED_M2M}

        org = request.profile.org
        results = []
        updated_count = 0
        for case in Case.objects.filter(pk__in=ids, org=org):
            # Per-case authorization, mirroring the single-case PUT path
            # (`CaseDetailView.put` calls `assert_case_write_access`). Without
            # this any org member could edit, reassign or close any case in
            # the org. Cases the caller may not write are reported as
            # `no_access` rather than silently skipped, so the caller can see
            # which of their selected tickets were denied.
            if not has_case_write_access(request.profile, case):
                results.append({"id": str(case.pk), "status": "no_access"})
                continue
            try:
                # A savepoint per case, so a blocked close rolls back only
                # itself and the rest of the batch still commits.
                with transaction.atomic():
                    for k, v in scalar_updates.items():
                        setattr(case, k, v)
                    if scalar_updates:
                        # `Case.clean()` carries the close-transition guard: a
                        # `closed_on` is required and, where a pre_close
                        # ApprovalRule matches, a recorded approval is too. A
                        # raw `save()` skips it, which is how the bulk path
                        # let a case be closed with no approval; the
                        # single-case path runs the same rule through the
                        # serializer.
                        case.clean()
                        case.save()
                    for m2m_field, model in ALLOWED_M2M.items():
                        if m2m_field not in m2m_updates:
                            continue
                        related_ids = m2m_updates[m2m_field] or []
                        related = list(
                            model.objects.filter(pk__in=related_ids, org=org)
                        )
                        manager = getattr(case, m2m_field)
                        if m2m_field == "tags":
                            # Append: bulk-tagging must not wipe a ticket's
                            # other tags. Reassign (`assigned_to`) still
                            # replaces.
                            manager.add(*related)
                        else:
                            manager.set(related)
            except ValidationError as exc:
                results.append(_close_gate_outcome(str(case.pk), exc))
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
        # assignee may work a ticket but not erase it.
        for row in Case.objects.filter(pk__in=ids, org=org, is_active=True).values(
            "id", "name", "created_by_id"
        ):
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
