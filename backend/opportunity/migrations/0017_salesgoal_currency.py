"""Give every sales goal a currency, and every existing goal its org's default.

A goal's target is one number, so it is in one currency, and a REVENUE goal now
counts only the won deals in that currency. Before this column existed progress
added every won deal's amount whatever its currency. The honest reading of an
existing goal is the one the web and mobile clients already printed: the org's
default currency (or USD, through `common.money.org_currency`, the fallback new goals use).

THE ORG LOOP IS NOT DECORATION

`sales_goal` is in ORG_SCOPED_TABLES, so it carries `FORCE ROW LEVEL SECURITY`
and its policy matches `NULLIF(current_setting('app.current_org', true), '')`,
which is empty during a migration. An unscoped UPDATE therefore touches nothing
in production while touching everything on a superuser dev database and on
SQLite. This walks orgs and sets `app.current_org` for each, the shape
`webforms/0002` and `common/0042` document; `org` is not org-scoped, so the
outer loop sees every org.

REVERSE

Dropping the column is the whole rollback, so the data step reverses as a no-op.
"""

from django.db import migrations, models

# The live rule, not a frozen copy: it reads one attribute, and the default a
# goal is backfilled with must be the default a new goal would get.
from common.money import org_currency


def backfill_goal_currency(apps, schema_editor):
    connection = schema_editor.connection
    # RLS and `set_config` are Postgres-only; on SQLite the loop still runs
    # and only the context call is skipped.
    is_postgres = connection.vendor == "postgresql"

    Org = apps.get_model("common", "Org")
    SalesGoal = apps.get_model("opportunity", "SalesGoal")

    def set_context(value):
        if not is_postgres:
            return
        with connection.cursor() as cursor:
            cursor.execute("SELECT set_config('app.current_org', %s, false)", [value])

    try:
        for org in Org.objects.only("id", "default_currency").iterator():
            set_context(str(org.id))
            SalesGoal.objects.filter(
                models.Q(currency__isnull=True) | models.Q(currency=""),
                org_id=org.id,
            ).update(currency=org_currency(org))
    finally:
        set_context("")


class Migration(migrations.Migration):
    dependencies = [
        ("opportunity", "0016_salesgoal_type_weights_alter_salesgoal_goal_type"),
        ("common", "0007_add_currency_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="salesgoal",
            name="currency",
            field=models.CharField(
                blank=True,
                choices=[
                    ("USD", "USD, Dollar"),
                    ("EUR", "EUR, Euro"),
                    ("GBP", "GBP, Pound"),
                    ("INR", "INR, Rupee"),
                    ("CAD", "CAD, Dollar"),
                    ("AUD", "AUD, Dollar"),
                    ("JPY", "JPY, Yen"),
                    ("CNY", "CNY, Yuan"),
                    ("CHF", "CHF, Franc"),
                    ("SGD", "SGD, Dollar"),
                    ("AED", "AED, Dirham"),
                    ("BRL", "BRL, Real"),
                    ("MXN", "MXN, Peso"),
                ],
                max_length=3,
                null=True,
                verbose_name="Currency",
            ),
        ),
        migrations.RunPython(backfill_goal_currency, migrations.RunPython.noop),
    ]
