"""What a customer is allowed to see of their own case.

A deliberately narrow projection rather than a reuse of the agent serializers.
Assignment, teams, watchers, SLA counters, tags and internal notes are all
invisible here, and they stay invisible by never being listed rather than by
being stripped out later. A field that is not declared cannot be leaked by a
future edit to a shared serializer, and cannot be mass-assigned on the way in.
"""

from rest_framework import serializers

from cases.models import Case
from common.models import COMMENT_MAX_LENGTH, Comment


class PortalCommentSerializer(serializers.ModelSerializer):
    author = serializers.SerializerMethodField()
    is_mine = serializers.SerializerMethodField()

    class Meta:
        model = Comment
        fields = ("id", "comment", "commented_on", "author", "is_mine")

    def get_author(self, obj):
        """Name the customer, but never name the agent.

        Which colleague replied is internal information; that support replied
        is what the customer needs.
        """
        if obj.commented_by_contact_id:
            contact = obj.commented_by_contact
            return f"{contact.first_name} {contact.last_name}".strip()
        return "Support"

    def get_is_mine(self, obj):
        contact = self.context.get("portal_contact")
        return bool(contact and obj.commented_by_contact_id == contact.id)


class PortalCaseSerializer(serializers.ModelSerializer):
    class Meta:
        model = Case
        fields = ("id", "name", "status", "priority", "created_at", "closed_on")


class PortalCaseDetailSerializer(serializers.ModelSerializer):
    class Meta:
        model = Case
        fields = (
            "id",
            "name",
            "status",
            "priority",
            "description",
            "created_at",
            "closed_on",
        )


class PortalCaseCreateSerializer(serializers.ModelSerializer):
    """Three fields. Everything else about a new case is the server's to decide.

    `org`, `contacts`, `status` and `created_by` are absent on purpose. A field
    that is not declared cannot be mass-assigned, which is a stronger guarantee
    than remembering to pop it before `save()`.
    """

    class Meta:
        model = Case
        fields = ("name", "description", "priority")

    def validate_name(self, value):
        value = (value or "").strip()
        if not value:
            raise serializers.ValidationError("Give the request a short summary.")
        return value

    def validate_priority(self, value):
        # Left to the model's choices, which the field already enforces. Kept
        # explicit so the next reader does not add a second source of truth.
        return value


class PortalCommentCreateSerializer(serializers.Serializer):
    """A reply body, and nothing else.

    `is_internal` is not a field here. A customer must not be able to write a
    note that their own portal then hides from them, and more importantly must
    not be able to inject text into the agents' private thread.
    """

    comment = serializers.CharField(max_length=COMMENT_MAX_LENGTH)

    def validate_comment(self, value):
        value = (value or "").strip()
        if not value:
            raise serializers.ValidationError("Write a message before sending.")
        return value
