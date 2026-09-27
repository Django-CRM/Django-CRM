"""DRF serializers for the macros REST API."""

from rest_framework import serializers

from common.models import Profile, Tags
from macros.models import Macro
from macros.render import find_unknown_placeholders


class MacroSerializer(serializers.ModelSerializer):
    """Serialize Macro for read and write paths.

    Scope/owner enforcement happens in the view because it depends on the
    requesting profile (admin can create org macros, others cannot).

    `set_assignees` and `add_tags` take id lists and resolve them only among
    the caller's own org (`context["org"]`), so another org's id is refused
    with a 400 like an id that does not exist. `profile` is outside
    ORG_SCOPED_TABLES, so there is no RLS policy behind this lookup: the
    filter is the control. With no org in context both lookups are empty and
    every id is refused.
    """

    owner_name = serializers.SerializerMethodField()
    unknown_placeholders = serializers.SerializerMethodField()
    set_assignees = serializers.PrimaryKeyRelatedField(
        many=True, required=False, queryset=Profile.objects.none()
    )
    add_tags = serializers.PrimaryKeyRelatedField(
        source="tags", many=True, required=False, queryset=Tags.objects.none()
    )
    # Who and what the ids name, with `is_active`, so a client can show a
    # deactivated assignee or an archived tag the macro still carries rather
    # than dropping it from a picker built from active rows only.
    set_assignees_details = serializers.SerializerMethodField()
    add_tags_details = serializers.SerializerMethodField()

    class Meta:
        model = Macro
        fields = (
            "id",
            "title",
            "body",
            "scope",
            "owner",
            "owner_name",
            "is_active",
            "usage_count",
            "unknown_placeholders",
            "set_status",
            "set_priority",
            "set_assignees",
            "add_tags",
            "set_assignees_details",
            "add_tags_details",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "owner",
            "owner_name",
            "usage_count",
            "unknown_placeholders",
            "created_at",
            "updated_at",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        org = self.context.get("org")
        if org is not None:
            self.fields[
                "set_assignees"
            ].child_relation.queryset = Profile.objects.filter(org=org)
            self.fields["add_tags"].child_relation.queryset = Tags.objects.filter(
                org=org
            )

    def _stored_ids(self, name):
        if self.instance is None:
            return set()
        return set(getattr(self.instance, name).values_list("id", flat=True))

    def validate_set_assignees(self, value):
        """Only active members may be newly named.

        One the macro already carries is kept even after they are deactivated:
        both clients resend the whole list on every save, so refusing it would
        make the macro unsaveable until somebody noticed, and applying skips a
        deactivated assignee anyway (`MacroApplyView`). The same allowance
        `WebFormDetailSerializer.validate_assign_to` makes.
        """
        stored = self._stored_ids("set_assignees")
        for profile in value:
            if not profile.is_active and profile.id not in stored:
                raise serializers.ValidationError(
                    "This user is deactivated. Choose an active member."
                )
        return value

    def validate_add_tags(self, value):
        """Only active tags may be newly added; a stored archived one is kept,
        for the reason `validate_set_assignees` gives."""
        stored = self._stored_ids("tags")
        for tag in value:
            if not tag.is_active and tag.id not in stored:
                raise serializers.ValidationError(
                    "This tag is archived. Choose an active tag."
                )
        return value

    def validate(self, attrs):
        """A macro has to do something: say something, act, or both."""
        attrs = super().validate(attrs)

        def current(name):
            if name in attrs:
                return attrs[name]
            if self.instance is None:
                return None
            value = getattr(self.instance, name)
            return list(value.all()) if name in ("set_assignees", "tags") else value

        has_body = bool((current("body") or "").strip())
        has_action = any(
            current(name)
            for name in ("set_status", "set_priority", "set_assignees", "tags")
        )
        if not has_body and not has_action:
            raise serializers.ValidationError(
                {"body": "A macro needs a body, an action, or both."}
            )
        return attrs

    def get_owner_name(self, obj):
        # `User` only stores `email`, no first_name/last_name on this model
        # , so we surface the email directly. Frontend can display whatever
        # form it wants from there.
        if obj.owner is None or obj.owner.user is None:
            return None
        return obj.owner.user.email or None

    def get_unknown_placeholders(self, obj):
        # Soft signal, not a save-blocker: unknown tokens render literally
        # on the ticket (see render_macro). Surfacing them here gives mobile
        # and API consumers the same warning the web UI computes client-side.
        return find_unknown_placeholders(obj.body)

    def get_set_assignees_details(self, obj):
        return [
            {
                "id": str(profile.id),
                "email": profile.user.email,
                "name": profile.user.name,
                "is_active": profile.is_active,
            }
            for profile in obj.set_assignees.all()
        ]

    def get_add_tags_details(self, obj):
        return [
            {
                "id": str(tag.id),
                "name": tag.name,
                "color": tag.color,
                "is_active": tag.is_active,
            }
            for tag in obj.tags.all()
        ]
