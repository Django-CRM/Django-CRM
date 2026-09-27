from django.db import models

from common.base import BaseModel
from common.models import Org, Profile, Tags
from common.utils import PRIORITY_CHOICE, STATUS_CHOICE

# A macro may move a ticket to any status a person may choose, which is every
# one but Duplicate: that status belongs to the merge, which also records where
# the ticket went (`cases.workflow.duplicate_refusal`).
MACRO_STATUS_CHOICES = [c for c in STATUS_CHOICE if c[0] != "Duplicate"]

# The four actions, by the names `POST /macros/<id>/apply/` takes in `only`.
ACTION_KEYS = ("status", "priority", "assignees", "tags")


class Macro(BaseModel):
    """Reusable canned response template applied to a case comment composer.

    Per `docs/cases/COORDINATION_DECISIONS.md` D2 we inherit BaseModel and
    declare our own org FK rather than using BaseOrgModel. Macros are either
    `org`-scoped (visible to everyone in the org, admin-managed) or
    `personal` (visible only to the owning Profile).

    Optional actions ride along with the text: a status, a priority, a set of
    assignees (replacing the ticket's) and tags (added to the ticket's). They
    are applied through `cases.updates.update_case`, the ticket PATCH's own
    path, by `POST /macros/<id>/apply/`. Blank or empty means "no change".
    A macro may be actions only, with an empty body.
    """

    SCOPE_ORG = "org"
    SCOPE_PERSONAL = "personal"
    SCOPE_CHOICES = [
        (SCOPE_ORG, "Org"),
        (SCOPE_PERSONAL, "Personal"),
    ]

    title = models.CharField(max_length=255)
    body = models.TextField(blank=True, default="")
    scope = models.CharField(max_length=10, choices=SCOPE_CHOICES, default=SCOPE_ORG)
    owner = models.ForeignKey(
        Profile,
        on_delete=models.CASCADE,
        related_name="personal_macros",
        null=True,
        blank=True,
    )
    is_active = models.BooleanField(default=True)
    usage_count = models.PositiveIntegerField(default=0)

    set_status = models.CharField(
        max_length=64, choices=MACRO_STATUS_CHOICES, blank=True, default=""
    )
    set_priority = models.CharField(
        max_length=64, choices=PRIORITY_CHOICE, blank=True, default=""
    )
    set_assignees = models.ManyToManyField(
        Profile, blank=True, related_name="macros_assigning"
    )
    # `add_tags` in the API. Named `tags` here because the tag registry
    # (`common.views.tags_views._TAGGABLE`) counts usage and merges tags
    # through a `tags` relation, and a macro that adds a tag is a use of it.
    tags = models.ManyToManyField(Tags, blank=True, related_name="macros_tagging")

    org = models.ForeignKey(Org, on_delete=models.CASCADE, related_name="macros")

    class Meta:
        verbose_name = "Macro"
        verbose_name_plural = "Macros"
        db_table = "macro"
        ordering = ("-updated_at",)
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(scope="org", owner__isnull=True)
                    | models.Q(scope="personal", owner__isnull=False)
                ),
                name="macro_scope_owner_consistent",
            ),
        ]
        indexes = [
            models.Index(fields=["org", "scope", "is_active"]),
            models.Index(fields=["owner", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.title} ({self.scope})"

    def action_keys(self):
        """The actions this macro carries, as `ACTION_KEYS` names, in order."""
        keys = []
        if self.set_status:
            keys.append("status")
        if self.set_priority:
            keys.append("priority")
        if self.set_assignees.exists():
            keys.append("assignees")
        if self.tags.exists():
            keys.append("tags")
        return keys
