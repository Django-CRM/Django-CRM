"""Enable tenant RLS and append-only enforcement for conversion provenance."""

from django.db import migrations

from common.rls import get_check_table_exists_sql, get_enable_policy_sql


def stamp_conversion_rls(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(get_check_table_exists_sql(), ["lead_conversion"])
        if not cursor.fetchone()[0]:
            raise RuntimeError("Required org-scoped table is missing: lead_conversion")
        cursor.execute(get_enable_policy_sql("lead_conversion"))
        cursor.execute(
            """
            CREATE OR REPLACE FUNCTION prevent_lead_conversion_mutation()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'lead_conversion is append-only';
            END;
            $$;
            DROP TRIGGER IF EXISTS lead_conversion_no_update ON lead_conversion;
            CREATE TRIGGER lead_conversion_no_update
            BEFORE UPDATE OR DELETE ON lead_conversion
            FOR EACH ROW EXECUTE FUNCTION prevent_lead_conversion_mutation();
            """
        )


class Migration(migrations.Migration):
    atomic = False
    dependencies = [
        ("common", "0043_rls_data_subject_request"),
        ("leads", "0021_leadconversion"),
    ]
    operations = [migrations.RunPython(stamp_conversion_rls, migrations.RunPython.noop)]
