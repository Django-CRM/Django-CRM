"""
Solution (Knowledge Base) Serializers
"""

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from cases.models import Solution
from common.serializer import OrganizationSerializer, TagsSerializer


def _author_name(obj):
    """A name to print, not a foreign key to resolve.

    `created_by` serialized as a bare `User` UUID, which no client can render.
    The list page has an Author column and the only honest thing it could
    have shown was a UUID. `User.name` falls back to the email local part on
    first save, so it is populated for every real account.
    """
    user = obj.created_by
    if user is None:
        return ""
    return user.name or user.email


class SolutionSerializer(serializers.ModelSerializer):
    """Serializer for Solution model"""

    org = OrganizationSerializer(read_only=True)
    case_count = serializers.SerializerMethodField()
    author = serializers.SerializerMethodField()
    # Read-only here and written in the view, so the org filter on the tag ids
    # cannot be skipped. See `_apply_tags` in `solution_views`.
    tags = TagsSerializer(read_only=True, many=True)

    class Meta:
        model = Solution
        fields = [
            "id",
            "title",
            "description",
            "status",
            "is_published",
            "org",
            "tags",
            "case_count",
            "author",
            "created_at",
            "updated_at",
            "created_by",
            "updated_by",
        ]
        read_only_fields = [
            "id",
            "org",
            "created_at",
            "updated_at",
            "created_by",
            "updated_by",
        ]

    @extend_schema_field(int)
    def get_case_count(self, obj):
        """Get number of cases using this solution"""
        # `SolutionListView` annotates this so a list of N articles costs one
        # query rather than N. The fallback keeps the serializer usable on a
        # single un-annotated instance: the create and publish responses.
        annotated = getattr(obj, "linked_case_count", None)
        if annotated is not None:
            return annotated
        return obj.cases.count()

    @extend_schema_field(str)
    def get_author(self, obj):
        return _author_name(obj)


class SolutionCreateUpdateSerializer(serializers.ModelSerializer):
    """Serializer for creating/updating solutions"""

    class Meta:
        model = Solution
        fields = [
            "title",
            "description",
            "status",
            "is_published",
        ]

    def validate(self, attrs):
        """One invariant, checked on every path into the fields.

        **Published implies approved.** The model says so (`Solution.publish()`
        refuses otherwise) and the publish endpoint said so, but the serializer
        only half did:

        * `validate_is_published` was guarded by `if value and self.instance`,
          so on **create**, where `self.instance` is None, it did not run at
          all. `POST {"status": "draft", "is_published": true}` returned 201
          with a published draft: the review workflow skipped in one request.
          Proven against the running server.
        * On update it read the **stored** status, never the incoming one, and
          it only ran when `is_published` was in the body. So the demotion was
          free, `PATCH {"status": "draft"}` on an approved, published article
          left `is_published` true. Proven the same way. That is the CLAUDE.md
          rule about validating the *source* state of a transition rather than
          only its target, in miniature.

        Resolving both fields against `self.instance` first is what makes a
        partial update judge itself on the article that will exist afterwards
        rather than on whichever two keys happened to be sent.
        """
        attrs = super().validate(attrs)

        # Absent means unchanged, for validation as much as for saving. An
        # article created *before* this rule existed can already be a
        # published draft, and judging the stored combination on a request
        # that touches neither field would make those rows uneditable: a
        # `PATCH {"description": ...}` refused with an error about
        # `is_published`, which is both baffling and the opposite of helpful,
        # since editing is how somebody would fix it. Any request that does
        # move either field still has to land on a legal combination, so the
        # first such edit repairs the row.
        if "status" not in attrs and "is_published" not in attrs:
            return attrs

        new_status = attrs.get("status", getattr(self.instance, "status", "draft"))
        is_published = attrs.get(
            "is_published", getattr(self.instance, "is_published", False)
        )

        if is_published and new_status != "approved":
            # Named against the field the caller can act on. Somebody demoting
            # a published article needs to hear about the publication, not
            # about the status they deliberately asked for.
            if "status" in attrs and "is_published" not in attrs:
                raise serializers.ValidationError(
                    {
                        "status": (
                            "This article is published, so it cannot be moved back "
                            f"to '{new_status}'. Unpublish it first."
                        )
                    }
                )
            raise serializers.ValidationError(
                {
                    "is_published": (
                        "Only approved solutions can be published. Please set status "
                        "to 'approved' first."
                    )
                }
            )
        return attrs


class SolutionDetailSerializer(serializers.ModelSerializer):
    """Detailed serializer for solution with linked cases"""

    org = OrganizationSerializer(read_only=True)
    linked_cases = serializers.SerializerMethodField()
    case_count = serializers.SerializerMethodField()
    author = serializers.SerializerMethodField()
    tags = TagsSerializer(read_only=True, many=True)

    class Meta:
        model = Solution
        fields = [
            "id",
            "title",
            "description",
            "status",
            "is_published",
            "org",
            "tags",
            "linked_cases",
            "case_count",
            "author",
            "created_at",
            "updated_at",
            "created_by",
            "updated_by",
        ]
        read_only_fields = fields

    @extend_schema_field(list)
    def get_linked_cases(self, obj):
        """Linked tickets the *requester* is allowed to open.

        This used to be `CaseSerializer(obj.cases.all()[:10], many=True)`,
        the full 33-field case payload, with no access check of any kind. A
        member of the org refused a case with a 403 could link an article to
        that very case (the link endpoint checked org and nothing else) and
        then read the case's name, description, account and contacts back
        through here. Proven end to end against the running server: 403 on the
        case, 201 on the link, the case's contents in the article.

        So: filtered through the same `visible_cases_qs` the queue and the case
        detail use, and slimmed to what a rail of links actually needs.

        `case_count` stays the *total*. It is the honest usage figure for an
        article and is already public on the list endpoint, so a reader seeing
        fewer rows than the count is being told plainly that some of those
        tickets are not theirs, rather than shown a number that quietly means
        something different for each reader.
        """
        from cases.access import visible_cases_qs

        profile = self.context.get("profile")
        if profile is None:
            # No profile in context means no way to establish who is asking.
            # An empty list is the safe answer; the alternative is the leak
            # this method existed as.
            return []

        cases = obj.cases.filter(pk__in=visible_cases_qs(profile).values("pk"))

        return [
            {
                "id": str(case.id),
                "name": case.name,
                "status": case.status,
                "priority": case.priority,
                "created_at": case.created_at.isoformat() if case.created_at else None,
            }
            for case in cases.order_by("-created_at", "-id")[:10]
        ]

    @extend_schema_field(int)
    def get_case_count(self, obj):
        return obj.cases.count()

    @extend_schema_field(str)
    def get_author(self, obj):
        return _author_name(obj)
