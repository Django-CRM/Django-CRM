from dataclasses import dataclass

from django.db import transaction
from django.db.models import Count, IntegerField, OuterRef, QuerySet, Subquery
from django.db.models.functions import Coalesce
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from cases.models import Solution
from common import swagger_params
from common.lookups import get_scoped_or_404
from common.models import APISettings, Tags
from common.modules import available_modules, model_module
from common.object_registry import BUILT_IN, all_objects
from common.pagination import CappedLimitOffsetPagination
from common.permissions import HasOrgContext, may_configure
from common.serializer import TagsSerializer
from leads.models import LeadIntake
from macros.models import Macro
from webforms.models import WebForm

# Models with a `tags` M2M that are not record objects, so the registry does not
# list them: (usage key, label, model). Records come from each registry entry's
# `tags_key`. `test_taggable_covers_every_model_with_a_tags_m2m` walks the live
# models, so a model in neither list fails it instead of reading as unused.
NON_OBJECT_TAG_HOLDERS = (
    ("api_settings", "API keys", APISettings),
    ("solutions", "Articles", Solution),
    ("web_forms", "Web forms", WebForm),
    ("lead_intakes", "Lead webhooks", LeadIntake),
    # A macro that adds a tag uses it; archiving the tag makes the macro skip
    # it, and a merge must move the macro onto the surviving tag.
    ("macros", "Macros", Macro),
)


@dataclass(frozen=True)
class _Holder:
    key: str
    label: str
    rows: QuerySet
    shown: bool


def _holders(org):
    """Every tag holder of `org`: registry entries with a `tags_key`, then the
    named holders. Off-module ones are counted in the totals and merged, as
    before the registry, but not `shown` in the usage map.
    """
    on = all_objects(org)
    shown_keys = {e.key for e in on}
    entries = {e.key: e for e in (*BUILT_IN, *on)}.values()
    shown_modules = {None, *available_modules(org)}
    return [
        *(
            _Holder(e.tags_key, e.label_plural, e.records(org), e.key in shown_keys)
            for e in entries
            if e.tags_key
        ),
        *(
            _Holder(
                key,
                label,
                model.objects.filter(org=org),
                model_module(model._meta.label) in shown_modules,
            )
            for key, label, model in NON_OBJECT_TAG_HOLDERS
        ),
    ]


def _usage_subquery(rows):
    """Per-tag count of `rows` (already org-filtered) that carry the tag.

    One correlated subquery per holder rather than `Count`s on a single query:
    joining every M2M through-table at once multiplies rows, and a subquery
    keeps each count independent (the subquery-rollup rule). The org filter is
    explicit: usage is a per-org fact and must not lean on RLS.
    """
    return Coalesce(
        Subquery(
            rows.filter(tags=OuterRef("pk"))
            .order_by()
            .values("tags")
            .annotate(c=Count("pk"))
            .values("c"),
            output_field=IntegerField(),
        ),
        0,
    )


def _annotated_tags(org, holders):
    """Org tags annotated with `_u_<n>`, the usage count of `holders[n]`.

    By position, since a key such as `obj:<key>` is not a safe alias.
    """
    return Tags.objects.filter(org=org).annotate(
        **{f"_u_{n}": _usage_subquery(h.rows) for n, h in enumerate(holders)}
    )


def _usage_map(tag, holders):
    return {h.key: getattr(tag, f"_u_{n}") for n, h in enumerate(holders) if h.shown}


def _tag_totals(org, holders):
    """Counts over ALL org tags, independent of the list's active/name filters,
    so the stat cards stay meaningful when the list is filtered."""
    all_tags = list(_annotated_tags(org, holders))
    active = [t for t in all_tags if t.is_active]

    def total_usage(tag):
        return sum(getattr(tag, f"_u_{n}") for n in range(len(holders)))

    return {
        "count": len(all_tags),
        "active": len(active),
        # Active tags applied to nothing at all, off modules included: the
        # "unused" figure on the settings stat card. Nothing hard-deletes a tag
        # (TagsDetailView.delete only archives it), so this counts what is not
        # referenced, not what is safe to remove.
        "unused": sum(1 for t in active if total_usage(t) == 0),
    }


def _non_text_error(params):
    """A 400 naming the first text field in `params` that is not a string.

    A JSON body can carry a number, list, object or null where these views
    expect text. `.strip()` on `name` then raised a 500, a null `description`
    broke the NOT NULL column, and anything else was stored as its `str()`.
    """
    for field in ("name", "color", "description"):
        if field in params and not isinstance(params[field], str):
            return Response(
                {"error": True, "errors": {field: [f"{field} must be text."]}},
                status=status.HTTP_400_BAD_REQUEST,
            )
    return None


class TagsListRowSerializer(TagsSerializer):
    """Schema only: a tag as the list returns it, with its per-model usage."""

    # One count per key of the response's `usage_labels`.
    usage = serializers.DictField(child=serializers.IntegerField(), read_only=True)

    class Meta(TagsSerializer.Meta):
        fields = (*TagsSerializer.Meta.fields, "usage")


class TagsListView(APIView, CappedLimitOffsetPagination):
    model = Tags
    permission_classes = (IsAuthenticated, HasOrgContext)

    def get_queryset(self, holders):
        """Get tags queryset (with usage annotations) and optional filtering."""
        params = self.request.query_params
        queryset = _annotated_tags(self.request.profile.org, holders)

        # By default, only show active tags
        # Admin can see archived tags with ?include_archived=true
        include_archived = params.get("include_archived", "").lower() == "true"
        if not include_archived:
            queryset = queryset.filter(is_active=True)

        # Filter by name if provided
        if params.get("name"):
            queryset = queryset.filter(name__icontains=params.get("name"))

        return queryset.order_by("name")

    @extend_schema(
        tags=["Tags"],
        operation_id="tags_list",
        parameters=swagger_params.organization_params,
        responses={
            200: inline_serializer(
                name="TagsListResponse",
                fields={
                    "tags_count": serializers.IntegerField(),
                    "tags": TagsListRowSerializer(many=True),
                    # Each usage key's plural label, such as "Deals"
                    "usage_labels": serializers.DictField(
                        child=serializers.CharField()
                    ),
                    "totals": inline_serializer(
                        name="TagsListTotals",
                        fields={
                            "count": serializers.IntegerField(),
                            "active": serializers.IntegerField(),
                            "unused": serializers.IntegerField(),
                        },
                    ),
                },
            )
        },
    )
    def get(self, request, *args, **kwargs):
        """List tags for the org, each with per-model usage counts, plus totals."""
        org = request.profile.org
        holders = _holders(org)
        tags_qs = list(self.get_queryset(holders))
        rows = TagsSerializer(tags_qs, many=True).data
        # Attach usage from the annotations (serializer order matches the qs).
        for row, obj in zip(rows, tags_qs):
            row["usage"] = _usage_map(obj, holders)
        return Response(
            {
                "tags_count": len(rows),
                "tags": rows,
                "usage_labels": {h.key: h.label for h in holders if h.shown},
                "totals": _tag_totals(org, holders),
            }
        )

    @extend_schema(
        tags=["Tags"],
        operation_id="tags_create",
        request=TagsSerializer,
        parameters=swagger_params.organization_params,
        # 201 for a new tag, 200 when an archived one of the same name is revived.
        responses=dict.fromkeys(
            (200, 201),
            inline_serializer(
                name="TagCreateResponse",
                fields={
                    "error": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "tag": TagsSerializer(),
                },
            ),
        ),
    )
    def post(self, request, *args, **kwargs):
        """Create a new tag (admin only)."""
        # Admin only for create
        if not may_configure(request.profile):
            return Response(
                {"error": True, "errors": "Only admins can create tags"},
                status=status.HTTP_403_FORBIDDEN,
            )

        params = request.data
        non_text = _non_text_error(params)
        if non_text:
            return non_text
        name = params.get("name", "").strip()

        if not name:
            return Response(
                {"error": True, "errors": {"name": ["This field is required."]}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        name_error = Tags.name_error(name)
        if name_error:
            return Response(
                {"error": True, "errors": {"name": [name_error]}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        slug = Tags.slug_for(name)
        valid_colors = [c[0] for c in Tags.COLOR_CHOICES]

        # Check for duplicate tag in this org (including archived)
        existing = Tags.objects.filter(slug=slug, org=request.profile.org).first()
        if existing:
            if not existing.is_active:
                # Reactivate archived tag with same name. A colour outside the
                # palette keeps the stored one, as on rename.
                existing.is_active = True
                if params.get("color") in valid_colors:
                    existing.color = params.get("color")
                existing.description = params.get("description", existing.description)
                existing.updated_by = request.user
                existing.save()
                return Response(
                    {
                        "error": False,
                        "message": "Tag reactivated successfully",
                        "tag": TagsSerializer(existing).data,
                    },
                    status=status.HTTP_200_OK,
                )
            return Response(
                {
                    "error": True,
                    "errors": {"name": ["A tag with this name already exists."]},
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Get color and description from request
        color = params.get("color", "blue")
        description = params.get("description", "")

        # Validate color
        if color not in valid_colors:
            color = "blue"

        tag = Tags.objects.create(
            name=name,
            slug=slug,
            color=color,
            description=description,
            org=request.profile.org,
            created_by=request.user,
        )

        return Response(
            {
                "error": False,
                "message": "Tag Created Successfully",
                "tag": TagsSerializer(tag).data,
            },
            status=status.HTTP_201_CREATED,
        )


class TagsDetailView(APIView):
    model = Tags
    permission_classes = (IsAuthenticated, HasOrgContext)

    def get_object(self, pk):
        return get_scoped_or_404(self.model, pk, self.request.profile.org)

    @extend_schema(
        tags=["Tags"],
        operation_id="tags_retrieve",
        parameters=swagger_params.organization_params,
        responses={
            200: inline_serializer(
                name="TagDetailResponse", fields={"tag": TagsSerializer()}
            )
        },
    )
    def get(self, request, pk, **kwargs):
        """Get a single tag by ID."""
        try:
            tag_obj = self.get_object(pk)
        except Tags.DoesNotExist:
            return Response(
                {"error": True, "errors": "Tag not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response({"tag": TagsSerializer(tag_obj).data})

    @extend_schema(
        tags=["Tags"],
        operation_id="tags_update",
        request=TagsSerializer,
        parameters=swagger_params.organization_params,
        responses={
            200: inline_serializer(
                name="TagUpdateResponse",
                fields={
                    "error": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "tag": TagsSerializer(),
                },
            )
        },
    )
    def put(self, request, pk, *args, **kwargs):
        """Update a tag (admin only)."""
        # Admin only
        if not may_configure(request.profile):
            return Response(
                {"error": True, "errors": "Only admins can update tags"},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            tag_obj = self.get_object(pk)
        except Tags.DoesNotExist:
            return Response(
                {"error": True, "errors": "Tag not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        params = request.data
        non_text = _non_text_error(params)
        if non_text:
            return non_text
        name = params.get("name", "").strip()

        if not name:
            return Response(
                {"error": True, "errors": {"name": ["This field is required."]}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        name_error = Tags.name_error(name)
        if name_error:
            return Response(
                {"error": True, "errors": {"name": [name_error]}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        slug = Tags.slug_for(name)

        # Check for duplicate tag in this org (excluding current tag)
        if (
            Tags.objects.filter(slug=slug, org=request.profile.org)
            .exclude(pk=pk)
            .exists()
        ):
            return Response(
                {
                    "error": True,
                    "errors": {"name": ["A tag with this name already exists."]},
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        tag_obj.name = name
        tag_obj.slug = slug

        # Update color if provided
        if "color" in params:
            color = params.get("color")
            valid_colors = [c[0] for c in Tags.COLOR_CHOICES]
            if color in valid_colors:
                tag_obj.color = color

        # Update description if provided
        if "description" in params:
            tag_obj.description = params.get("description", "")

        tag_obj.updated_by = request.user
        tag_obj.save()

        return Response(
            {
                "error": False,
                "message": "Tag Updated Successfully",
                "tag": TagsSerializer(tag_obj).data,
            },
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        tags=["Tags"],
        operation_id="tags_archive",
        parameters=swagger_params.organization_params,
        responses={
            200: inline_serializer(
                name="TagArchiveResponse",
                fields={
                    "error": serializers.BooleanField(),
                    "message": serializers.CharField(),
                },
            )
        },
    )
    def delete(self, request, pk, **kwargs):
        """Archive a tag - soft delete (admin only)."""
        # Admin only
        if not may_configure(request.profile):
            return Response(
                {"error": True, "errors": "Only admins can archive tags"},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            tag_obj = self.get_object(pk)
        except Tags.DoesNotExist:
            return Response(
                {"error": True, "errors": "Tag not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Soft delete - archive the tag
        tag_obj.is_active = False
        tag_obj.updated_by = request.user
        tag_obj.save()

        return Response(
            {"error": False, "message": "Tag archived successfully"},
            status=status.HTTP_200_OK,
        )


class TagsRestoreView(APIView):
    """Restore an archived tag."""

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        tags=["Tags"],
        operation_id="tags_restore",
        request=None,
        parameters=swagger_params.organization_params,
        responses={
            200: inline_serializer(
                name="TagRestoreResponse",
                fields={
                    "error": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "tag": TagsSerializer(),
                },
            )
        },
    )
    def post(self, request, pk, **kwargs):
        """Restore an archived tag (admin only)."""
        # Admin only
        if not may_configure(request.profile):
            return Response(
                {"error": True, "errors": "Only admins can restore tags"},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            tag_obj = Tags.objects.get(pk=pk, org=request.profile.org)
        except Tags.DoesNotExist:
            return Response(
                {"error": True, "errors": "Tag not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        tag_obj.is_active = True
        tag_obj.updated_by = request.user
        tag_obj.save()

        return Response(
            {
                "error": False,
                "message": "Tag restored successfully",
                "tag": TagsSerializer(tag_obj).data,
            },
            status=status.HTTP_200_OK,
        )


class TagsMergeView(APIView):
    """Move every record off one tag and onto another, then archive the source.

    The settings page has long shown a "these two look like the same tag"
    banner with a Merge button that did nothing, because this endpoint did not
    exist. Duplicates are the tag problem that matters: anyone filtering by
    "Invoice" silently misses everything tagged "Invoices".

    The move below is two queries per record rather than a bulk repoint of the
    through tables. Django has no bulk M2M move, and doing it by hand means
    naming each model's through-table FK column, which is exactly the kind of
    per-model detail that goes stale. A merge is a rare admin action bounded
    by one org's records, so the loop is the cheaper thing to be right about.
    """

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        tags=["Tags"],
        operation_id="tags_merge",
        parameters=swagger_params.organization_params,
        request=inline_serializer(
            name="TagMergeRequest",
            fields={"into": serializers.UUIDField()},
        ),
        responses={
            200: inline_serializer(
                name="TagMergeResponse",
                fields={
                    "error": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "tag": TagsSerializer(),
                    "moved": serializers.IntegerField(),
                },
            )
        },
    )
    def post(self, request, pk, **kwargs):
        """Merge the tag at `pk` into the tag named by `into` (admin only)."""
        if not may_configure(request.profile):
            return Response(
                {"error": True, "errors": "Only admins can merge tags"},
                status=status.HTTP_403_FORBIDDEN,
            )

        org = request.profile.org
        # Both ends are looked up inside the caller's org. `into` arrives from
        # the request body, so without the org filter it would be the whole
        # point of failure: an id from another tenant would let this endpoint
        # stamp that tenant's tag onto this org's records. RLS would catch it
        # in production and does not in dev, where the app's DB role is a
        # superuser, so the explicit filter is the contract.
        source = get_scoped_or_404(Tags, pk, org)

        into = request.data.get("into")
        if not into:
            return Response(
                {"error": True, "errors": {"into": ["This field is required."]}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        target = get_scoped_or_404(Tags, into, org)

        if target.pk == source.pk:
            return Response(
                {
                    "error": True,
                    "errors": {"into": ["A tag cannot merge into itself."]},
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not target.is_active:
            # Validate the destination state, not just that it resolves.
            # Merging onto an archived tag hides every moved record behind a
            # tag the page shows as "Off", which reads as data loss.
            return Response(
                {
                    "error": True,
                    "errors": {
                        "into": ["Restore that tag before merging records onto it."]
                    },
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        moved = 0
        # Once per model, not per holder: entries can share a model, and walking
        # the whole model also moves rows that no available entry covers.
        models = dict.fromkeys(h.rows.model for h in _holders(org))
        with transaction.atomic():
            for model in models:
                # Org-filtered for the same reason as the lookups above.
                for obj in model.objects.filter(tags=source, org=org):
                    obj.tags.add(target)
                    obj.tags.remove(source)
                    moved += 1

            # Archived, not deleted. Nothing hard-deletes a tag anywhere in
            # this file, and a merge is the case where that matters most: if
            # the merge was a mistake, the name still exists to restore.
            source.is_active = False
            source.updated_by = request.user
            source.save()

        return Response(
            {
                "error": False,
                "message": (
                    f"Merged {source.name!r} into {target.name!r}. "
                    f"{moved} record(s) moved."
                ),
                "tag": TagsSerializer(target).data,
                "moved": moved,
            },
            status=status.HTTP_200_OK,
        )
