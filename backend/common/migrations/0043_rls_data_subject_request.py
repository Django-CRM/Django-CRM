"""Enable tenant RLS for the LGPD request intake ledger."""

from django.db import migrations

from common.rls import get_check_table_exists_sql, get_enable_policy_sql

TABLES = ["data_subject_request", "data_subject_request_event"]


def stamp_dsr_rls(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        for table in TABLES:
            cursor.execute(get_check_table_exists_sql(), [table])
            if not cursor.fetchone()[0]:
                raise RuntimeError(f"Required org-scoped table is missing: {table}")
            cursor.execute(get_enable_policy_sql(table))
        cursor.execute(
            """
            CREATE OR REPLACE FUNCTION prevent_dsr_event_mutation()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'data_subject_request_event is append-only';
            END;
            $$;
            DROP TRIGGER IF EXISTS dsr_event_no_update ON data_subject_request_event;
            CREATE TRIGGER dsr_event_no_update
            BEFORE UPDATE OR DELETE ON data_subject_request_event
            FOR EACH ROW EXECUTE FUNCTION prevent_dsr_event_mutation();
            """
        )


class Migration(migrations.Migration):
    atomic = False
    dependencies = [
        ("common", "0042_rls_marketing_attribution"),
        ("leads", "0019_datasubjectrequestevent_and_more"),
    ]
    operations = [migrations.RunPython(stamp_dsr_rls, migrations.RunPython.noop)]
