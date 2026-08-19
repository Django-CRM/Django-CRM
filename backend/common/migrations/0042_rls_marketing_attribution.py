"""Enable tenant RLS for NEXTTHOUSE campaign and attribution tables."""

from django.db import migrations

from common.rls import get_check_table_exists_sql, get_enable_policy_sql

TABLES = ["marketing_campaign", "lead_attribution_touch"]


def stamp_marketing_rls(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return

    with schema_editor.connection.cursor() as cursor:
        for table in TABLES:
            cursor.execute(get_check_table_exists_sql(), [table])
            if not cursor.fetchone()[0]:
                raise RuntimeError(f"Required org-scoped table is missing: {table}")
            cursor.execute(get_enable_policy_sql(table))


def noop_reverse(apps, schema_editor):
    """Never remove tenant-isolation policies during a code rollback."""


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("common", "0041_clear_stale_rls_force_flags"),
        ("leads", "0017_marketingcampaign_leadattributiontouch_and_more"),
    ]

    operations = [migrations.RunPython(stamp_marketing_rls, noop_reverse)]
