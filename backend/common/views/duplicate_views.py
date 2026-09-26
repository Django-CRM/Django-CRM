"""Possible duplicates and merging, for leads, contacts and accounts (G19).

Three views, each subclassed once per module with a `DuplicateSpec` that names
the module's model and its own read and delete rules:

POST /api/<module>/duplicates/                    while a create form is typed
GET  /api/<module>/<id>/duplicates/               the detail page's panel
POST /api/<module>/<keeper id>/merge/             {"merge_id": "<loser id>"}

Nothing here decides who may see what. Every search starts from the module's
read-rule queryset, so a record the caller cannot open is neither returned nor
counted, and a hidden id answers the same 404 as a missing one. Hits carry
list-level fields only (id, display name, email, phone), never the record.

Merge authorization (owner decision): the caller must be able to read both
records, must hold the module's write rule on the keeper, and its delete rule
on the loser, because the merge destroys the loser. For leads, contacts and
accounts the write rule is the read rule (anyone who may open the record may
edit it), so readability covers the keeper; the delete rule is narrower
(`may_delete_lead` and friends in each module's `access.py`).
"""

from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from common.audit_log import audit_log
from common.duplicate_detection import find_duplicates, merge_records
from common.permissions import HasOrgContext


class DuplicateSpec:
    """What the three views need to know about one module. Subclassed once
    per module; every method is a thin call into that module's own rules."""

    model = None
    entity = ""

    @staticmethod
    def visible(profile):
        """The records ``profile`` may open, scoped to their org."""
        raise NotImplementedError

    @staticmethod
    def may_delete(profile, record):
        raise NotImplementedError

    @staticmethod
    def display_name(record):
        raise NotImplementedError

    @staticmethod
    def criteria_of(record):
        """The values a saved record is matched on."""
        raise NotImplementedError

    @staticmethod
    def merge_refusal(record):
        """Why ``record`` cannot take part in a merge, or None."""
        return None


class DuplicateQuerySerializer(serializers.Serializer):
    """What a create form may ask about. Every field is optional; the lengths
    are the model's, so a query no record could match is refused cheaply."""

    email = serializers.CharField(required=False, allow_blank=True, max_length=254)
    phone = serializers.CharField(required=False, allow_blank=True, max_length=25)
    first_name = serializers.CharField(required=False, allow_blank=True, max_length=255)
    last_name = serializers.CharField(required=False, allow_blank=True, max_length=255)
    company_name = serializers.CharField(
        required=False, allow_blank=True, max_length=255
    )
    name = serializers.CharField(required=False, allow_blank=True, max_length=255)
    website = serializers.CharField(required=False, allow_blank=True, max_length=255)


class DuplicateHitSerializer(serializers.Serializer):
    """One possible duplicate, for the schema. Built by `_hits`."""

    id = serializers.UUIDField()
    name = serializers.CharField()
    email = serializers.CharField()
    phone = serializers.CharField()
    matched_on = serializers.ListField(child=serializers.CharField())
    can_delete = serializers.BooleanField()


class DuplicatesResponseSerializer(serializers.Serializer):
    duplicates = DuplicateHitSerializer(many=True)


class RecordDuplicatesResponseSerializer(DuplicatesResponseSerializer):
    can_delete = serializers.BooleanField()


class MergeRequestSerializer(serializers.Serializer):
    merge_id = serializers.UUIDField()


class MergeResponseSerializer(serializers.Serializer):
    error = serializers.BooleanField()
    message = serializers.CharField()
    id = serializers.UUIDField()


class DuplicateCheckThrottle(UserRateThrottle):
    """Create forms ask while somebody types. The clients debounce; this is
    the backstop for a client that does not."""

    scope = "duplicate_check"
    rate = "120/minute"


def _hits(spec, profile, found):
    return [
        {
            "id": str(record.pk),
            "name": spec.display_name(record),
            "email": record.email or "",
            "phone": record.phone or "",
            "matched_on": reasons,
            # So a client can tell, before it offers a merge, which record the
            # caller could merge away. The API enforces it either way.
            "can_delete": spec.may_delete(profile, record),
        }
        for record, reasons in found
    ]


class DuplicateCheckView(APIView):
    """Possible duplicates of a record that has not been saved yet.

    A POST that writes nothing. The criteria are an email address, a phone
    number and a name as somebody types them, and in a query string they land
    in every proxy and server access log on the way; in a body they do not.
    """

    permission_classes = (IsAuthenticated, HasOrgContext)
    throttle_classes = (DuplicateCheckThrottle,)
    spec = DuplicateSpec

    def post(self, request, **kwargs):
        query = DuplicateQuerySerializer(data=request.data)
        if not query.is_valid():
            return Response(
                {"error": True, "errors": query.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        profile = request.profile
        found = find_duplicates(self.spec.visible(profile), query.validated_data)
        return Response({"duplicates": _hits(self.spec, profile, found)})


class RecordDuplicatesView(APIView):
    """Possible duplicates of a saved record, for its detail page."""

    permission_classes = (IsAuthenticated, HasOrgContext)
    spec = DuplicateSpec

    def get(self, request, pk, **kwargs):
        profile = request.profile
        visible = self.spec.visible(profile)
        record = get_object_or_404(visible, pk=pk)
        found = []
        if self.spec.merge_refusal(record) is None:
            found = find_duplicates(
                visible, self.spec.criteria_of(record), exclude_id=record.pk
            )
        return Response(
            {
                "can_delete": self.spec.may_delete(profile, record),
                "duplicates": _hits(self.spec, profile, found),
            }
        )


class MergeView(APIView):
    """Merge the record named in the body into the record in the URL."""

    permission_classes = (IsAuthenticated, HasOrgContext)
    spec = DuplicateSpec

    def post(self, request, pk, **kwargs):
        body = MergeRequestSerializer(data=request.data)
        if not body.is_valid():
            return Response(
                {"error": True, "errors": body.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        loser_id = str(body.validated_data["merge_id"])
        if loser_id == pk:
            return Response(
                {
                    "error": True,
                    "errors": {"merge_id": ["A record cannot be merged into itself."]},
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        spec, profile = self.spec, request.profile
        model = spec.model
        # The same text `get_object_or_404` gives the detail view for an id
        # that does not exist, so neither id can tell a hidden record apart.
        not_found = f"No {model._meta.object_name} matches the given query."

        with transaction.atomic():
            # Both rows locked, in a fixed order so two merges of the same pair
            # cannot deadlock, and only inside the caller's org.
            locked = {
                str(record.pk): record
                for record in model.objects.select_for_update()
                .filter(org=profile.org, pk__in=[pk, loser_id])
                .order_by("pk")
            }
            readable = {
                str(value)
                for value in spec.visible(profile)
                .filter(pk__in=list(locked))
                .values_list("pk", flat=True)
            }
            if pk not in readable or loser_id not in readable:
                raise Http404(not_found)
            keeper, loser = locked[pk], locked[loser_id]

            if not spec.may_delete(profile, loser):
                # Both records are readable by now, so this is an honest 403,
                # the one the module's own delete answers.
                return Response(
                    {
                        "error": True,
                        "errors": "You may not delete the record being merged "
                        "away, so you cannot merge it.",
                    },
                    status=status.HTTP_403_FORBIDDEN,
                )
            refusal = spec.merge_refusal(keeper) or spec.merge_refusal(loser)
            if refusal:
                return Response(
                    {"error": True, "errors": {"merge_id": [refusal]}},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            kept = {"id": str(keeper.pk), "name": spec.display_name(keeper)}
            merged = {"id": loser_id, "name": spec.display_name(loser)}
            merge_records(keeper, loser)

        # After the commit, not inside it. `audit_log` swallows its own write
        # errors, and a failed insert inside the transaction would leave it
        # aborted: the merge would roll back at commit while this view still
        # answered 200.
        audit_log.record_merged(
            request.user, profile.org, spec.entity, kept, merged, request=request
        )
        return Response(
            {
                "error": False,
                "message": f"Merged {merged['name']} into {spec.display_name(keeper)}.",
                "id": kept["id"],
            }
        )
