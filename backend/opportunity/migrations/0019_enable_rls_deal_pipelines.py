# Enable Row-Level Security on the two deal pipeline tables.
#
# Both carry org_id directly (BaseOrgModel) and are registered in
# ORG_SCOPED_TABLES. Stamped here, from source, so a database built purely
# from migrations has the policies too; mirrors opportunity/0012 and 0013.
# get_enable_policy_sql() opens with DROP POLICY IF EXISTS, so re-running it
# is harmless.

from django.db import connection, migrations

from common.rls import get_disable_policy_sql, get_enable_policy_sql

TABLES = ("opportunity_pipeline", "opportunity_pipeline_stage")


def enable_rls(apps, schema_editor):
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        for table in TABLES:
            cursor.execute(get_enable_policy_sql(table))


def disable_rls(apps, schema_editor):
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        for table in TABLES:
            cursor.execute(get_disable_policy_sql(table))


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("opportunity", "0018_deal_pipelines"),
    ]

    operations = [
        migrations.RunPython(enable_rls, disable_rls),
    ]
