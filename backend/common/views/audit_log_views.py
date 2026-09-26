"""The security audit log, read by an org's admins (G33).

GET /api/org/audit-log/   newest first, paginated (limit/offset, at most 100)
    ?event_type=<one of SecurityAuditLog.EVENT_TYPES>
    ?actor=<user id>
    ?from=YYYY-MM-DD  ?to=YYYY-MM-DD   inclusive, in the org's timezone

`security_audit_log` has no RLS policy, on purpose (`common/0036`, and
`common/tests/test_audit_log_not_exposed.py`): its `org` is nullable because
the events it exists for, failed logins and invalid API keys, often have none.
So the `org=request.profile.org` filter below is the only tenant barrier, and a
row with no org never matches it.

What leaves the server is chosen field by field rather than dumped:

* `description` is not returned. An ORG_SWITCH row's text names the org the
  user switched from, which is another tenant.
* `metadata` is cut down to `SAFE_DETAILS`, keys whose values are ids, counts
  or sentences the server wrote. Other keys can hold what a caller supplied:
  `suspicious_activity(details=...)`, the email a failed login tried, an API
  key prefix.
* A path under `/api/public/` can carry a bearer token in the URL (the CSAT
  link does), so only that prefix is shown.

Admins only. `/api/org/audit-log/` is on the credential deny-list in
`common/scopes.py`, so a personal access token or the org API key is refused
before it gets here, the same way `/api/webhooks/` is.
"""

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from common.audit_log import SecurityAuditLog
from common.permissions import HasOrgContext, IsOrgAdmin
from common.validators import date_param, uuid_param

SAFE_DETAILS = (
    "action",
    "resource",
    "deleted_count",
    "endpoint_id",
    "creator_id",
    "pause_reason",
    "previous_creator_id",
    "changed",
)

PUBLIC_PREFIX = "/api/public/"

EVENT_LABELS = dict(SecurityAuditLog.EVENT_TYPES)


class AuditLogPagination(LimitOffsetPagination):
    default_limit = 25
    max_limit = 100


def _entry(row):
    user = row.user
    path = row.request_path
    if path.startswith(PUBLIC_PREFIX):
        path = PUBLIC_PREFIX
    return {
        "id": str(row.id),
        "event_type": row.event_type,
        "event_label": EVENT_LABELS.get(row.event_type, row.event_type),
        "created_at": row.created_at.isoformat(),
        "success": row.success,
        "actor": (
            {"id": str(user.id), "name": user.name, "email": user.email}
            if user is not None
            else None
        ),
        "ip_address": row.ip_address,
        "user_agent": row.user_agent,
        "request_method": row.request_method,
        "request_path": path,
        "details": {
            key: row.metadata[key]
            for key in SAFE_DETAILS
            if isinstance(row.metadata, dict) and key in row.metadata
        },
    }


class SecurityAuditLogListView(APIView):
    permission_classes = (IsAuthenticated, HasOrgContext, IsOrgAdmin)

    @extend_schema(
        tags=["Audit log"],
        operation_id="org_audit_log",
        parameters=[
            OpenApiParameter("event_type", str, OpenApiParameter.QUERY),
            OpenApiParameter("actor", OpenApiTypes.UUID, OpenApiParameter.QUERY),
            OpenApiParameter("from", OpenApiTypes.DATE, OpenApiParameter.QUERY),
            OpenApiParameter("to", OpenApiTypes.DATE, OpenApiParameter.QUERY),
        ],
        responses=OpenApiTypes.OBJECT,
    )
    def get(self, request):
        params = request.query_params
        rows = SecurityAuditLog.objects.filter(org=request.profile.org)

        event_type = params.get("event_type")
        if event_type:
            if event_type not in EVENT_LABELS:
                raise ValidationError({"event_type": ["Unknown event type."]})
            rows = rows.filter(event_type=event_type)
        actor = uuid_param(params, "actor")
        if actor:
            rows = rows.filter(user_id=actor)
        start = date_param(params, "from")
        end = date_param(params, "to")
        if start and end and start > end:
            raise ValidationError({"from": ["Must be on or before 'to'."]})
        if start:
            rows = rows.filter(created_at__date__gte=start)
        if end:
            rows = rows.filter(created_at__date__lte=end)

        rows = rows.select_related("user").order_by("-created_at", "-id")
        paginator = AuditLogPagination()
        page = paginator.paginate_queryset(rows, request, view=self)
        response = paginator.get_paginated_response([_entry(row) for row in page])
        response.data["event_types"] = [
            {"value": value, "label": label}
            for value, label in SecurityAuditLog.EVENT_TYPES
        ]
        return response
