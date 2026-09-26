"""Take card assignees off boards they cannot open.

A card may be assigned only to someone who can open its board: the owner or a
member (`tasks.views.board_views._set_card_assignees`). That was checked when
an assignee was written and never again, so two kinds of stale row exist: cards
assigned before the check existed, and members removed from a board after
being assigned. `tasks.signals.unassign_removed_member` stops the second kind
from here on; this clears what is already stored.

The org loop is required, not decoration. `board`, `board_column`,
`board_task` and `board_member` are in ORG_SCOPED_TABLES with FORCE ROW LEVEL
SECURITY, and `app.current_org` is empty during a migration, so an unscoped
query sees no rows in production (see `common/0042` and `webforms/0002`).

Reverse is a no-op: the removed assignments named people who could not open
the card, and putting them back would restore the defect.
"""

from django.db import migrations
from django.db.models import Exists, F, OuterRef


def unassign_non_members(apps, schema_editor):
    connection = schema_editor.connection
    is_postgres = connection.vendor == "postgresql"

    Org = apps.get_model("common", "Org")
    BoardMember = apps.get_model("tasks", "BoardMember")
    BoardTask = apps.get_model("tasks", "BoardTask")
    Assignment = BoardTask.assigned_to.through

    def set_context(value):
        if not is_postgres:
            return
        with connection.cursor() as cursor:
            cursor.execute("SELECT set_config('app.current_org', %s, false)", [value])

    member = BoardMember.objects.filter(
        board_id=OuterRef("boardtask__column__board_id"),
        profile_id=OuterRef("profile_id"),
    )
    try:
        for org_id in Org.objects.values_list("id", flat=True).iterator():
            set_context(str(org_id))
            Assignment.objects.filter(boardtask__org_id=org_id).exclude(
                profile_id=F("boardtask__column__board__owner_id")
            ).exclude(Exists(member)).delete()
    finally:
        set_context("")


class Migration(migrations.Migration):
    dependencies = [
        ("tasks", "0013_alter_taskpipeline_is_default"),
    ]

    operations = [
        migrations.RunPython(unassign_non_members, migrations.RunPython.noop),
    ]
