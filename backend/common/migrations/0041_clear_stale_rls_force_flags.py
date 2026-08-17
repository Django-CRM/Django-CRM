# Clear `FORCE ROW LEVEL SECURITY` from tables where RLS is disabled.
#
# `get_enable_policy_sql` sets both ENABLE and FORCE. Until the change that
# accompanies this migration, `get_disable_policy_sql` cleared neither the force
# flag nor the policies' effect on it: it dropped the two policies and ran
# DISABLE, leaving the table at `relrowsecurity=false, relforcerowsecurity=true`.
#
# That state is inert. Forcing does nothing while RLS is off, so no row was ever
# filtered differently because of it. It is still worth clearing, because it is
# not what it looks like: reading `pg_class` shows a table with a force flag set
# and no policies, which reads as a control someone half applied and abandoned.
# `security_audit_log` was misread exactly that way, as missing tenant isolation,
# when `common/0036` had removed its policies deliberately and correctly (see the
# comment on ORG_SCOPED_TABLES in common/rls/__init__.py; enabling RLS there
# silently drops LOGIN_FAILURE and CROSS_ORG_ATTEMPT rows, which is the bug 0036
# exists to fix).
#
# This sweeps by state rather than naming a table, so it also cleans up any
# table left this way by reversing an enable migration, which is where nearly
# every call to get_disable_policy_sql comes from.
#
# atomic = False, matching common/0028, 0034 and 0036: ALTER TABLE takes an
# ACCESS EXCLUSIVE lock, so committing per statement keeps each lock brief.

from django.db import migrations

FIND_STALE = """
    SELECT c.relname
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public'
      AND c.relkind = 'r'
      AND c.relforcerowsecurity
      AND NOT c.relrowsecurity
    ORDER BY c.relname
"""


def clear_stale_force_flags(apps, schema_editor):
    """Drop the force flag anywhere RLS is off, so the two agree."""
    if schema_editor.connection.vendor != "postgresql":
        print("RLS is only supported on PostgreSQL. Skipping.")
        return

    with schema_editor.connection.cursor() as cursor:
        cursor.execute(FIND_STALE)
        tables = [row[0] for row in cursor.fetchall()]

        if not tables:
            print("  No stale force flags found")
            return

        for table in tables:
            # Identifier comes from pg_class, not from user input, and is quoted.
            cursor.execute(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY')
            print(f"  Cleared stale FORCE on {table}")


def noop_reverse(apps, schema_editor):
    """Deliberately does nothing.

    The forward direction removes a flag that had no behaviour, so there is no
    prior behaviour to restore. Re-setting FORCE on a table whose RLS is
    disabled would only recreate the misleading state this migration exists to
    remove, and would tell a future reader that something meaningful was
    reversed. Reversing is therefore a no-op rather than an error, so migrating
    backwards past this point still works.
    """


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("common", "0040_comment_max_length"),
    ]

    operations = [
        migrations.RunPython(clear_stale_force_flags, noop_reverse),
    ]
