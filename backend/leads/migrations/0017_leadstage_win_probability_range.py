"""Bound `LeadStage.win_probability` to 0-100 at the database (tracker D11).

The board move copies a stage's win probability onto `Lead.probability`, which
has the `lead_probability_range` check. A stage stored at 150 or -5 therefore
turned every later move into it into an IntegrityError 500. The API now refuses
such a value, and this adds the matching check so no other writer can store one.

CLAMP FIRST

A row already out of range would make `AddConstraint` fail, since Postgres
validates every existing row when the check is added. So the data step runs
first and clamps: below 0 becomes 0, above 100 becomes 100. Clamping keeps the
stage and its nearest meaningful value; nothing is deleted.

THE ORG LOOP IS NOT DECORATION

`lead_stage` is in ORG_SCOPED_TABLES, so it has `FORCE ROW LEVEL SECURITY` and
an unscoped update during a migration (where `app.current_org` is empty)
matches no row in production. The loop sets the context per org, the shape
`common/0042_recompute_tag_slugs` documents. Constraint validation is not
subject to RLS, which is exactly why every org has to be clamped.

REVERSE

The constraint is dropped. The clamp is a no-op: the original out-of-range
values were the defect, and nothing needs them back.
"""

from django.db import migrations, models


def clamp_win_probability(apps, schema_editor):
    connection = schema_editor.connection
    # RLS and `set_config` are Postgres-only; on SQLite the loop still runs
    # and only the context call is skipped.
    is_postgres = connection.vendor == "postgresql"

    Org = apps.get_model("common", "Org")
    LeadStage = apps.get_model("leads", "LeadStage")

    def set_context(value):
        if not is_postgres:
            return
        with connection.cursor() as cursor:
            cursor.execute("SELECT set_config('app.current_org', %s, false)", [value])

    try:
        for org_id in Org.objects.values_list("id", flat=True).iterator():
            set_context(str(org_id))
            stages = LeadStage.objects.filter(org_id=org_id)
            stages.filter(win_probability__lt=0).update(win_probability=0)
            stages.filter(win_probability__gt=100).update(win_probability=100)
    finally:
        set_context("")


class Migration(migrations.Migration):
    dependencies = [
        ("common", "0042_recompute_tag_slugs"),
        ("leads", "0016_alter_leadpipeline_is_default"),
    ]

    operations = [
        migrations.RunPython(clamp_win_probability, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="leadstage",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("win_probability__gte", 0), ("win_probability__lte", 100)
                ),
                name="lead_stage_win_probability_range",
            ),
        ),
    ]
