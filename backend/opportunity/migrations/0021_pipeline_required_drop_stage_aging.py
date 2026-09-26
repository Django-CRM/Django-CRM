# Every deal now has a pipeline (0020), so the column becomes required, and
# `stage_aging_config` goes: its numbers live on `DealStage` since 0020, and
# nothing reads the old table any more. Dropping it drops its RLS policies.
#
# The first step repeats 0020's backfill. The old code keeps serving while
# migrations run, so a deal (or a whole org) it creates after 0020 commits has
# no pipeline, and SET NOT NULL on it would fail on every retry with 0020
# already recorded. The backfill is idempotent and runs per org under that
# org's RLS context, exactly as in 0020. The migration is not atomic (updating
# the FK and altering its column in one transaction trips Postgres's "pending
# trigger events"), so if the ALTER does fail, a rerun backfills again first.
#
# The RunPython sits between the two schema operations so that, run in
# reverse, it stamps RLS on `stage_aging_config` right after DeleteModel's
# reverse has recreated the table, rather than leaving a tenant table with no
# policies until someone notices.

from importlib import import_module

from django.db import connection, migrations, models

from common.rls import get_enable_policy_sql

create_default_pipelines = import_module(
    "opportunity.migrations.0020_default_deal_pipelines"
).create_default_pipelines


def restamp_stage_aging_rls(apps, schema_editor):
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        cursor.execute(get_enable_policy_sql("stage_aging_config"))


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("opportunity", "0020_default_deal_pipelines"),
    ]

    operations = [
        migrations.RunPython(create_default_pipelines, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="opportunity",
            name="pipeline",
            field=models.ForeignKey(
                on_delete=models.deletion.RESTRICT,
                related_name="opportunities",
                to="opportunity.dealpipeline",
            ),
        ),
        migrations.RunPython(migrations.RunPython.noop, restamp_stage_aging_rls),
        migrations.DeleteModel(name="StageAgingConfig"),
    ]
