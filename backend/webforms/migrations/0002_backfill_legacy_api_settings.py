"""Give every existing APISettings row a backing WebForm.

`CreateLeadFromSite` now routes through `webforms.service.submit_form`, so it
needs a form to describe what the legacy endpoint collects. This creates one
per API key, preserving the key's own configuration.

THE ORG LOOP IS NOT DECORATION

An earlier version of this read `APISettings.objects.all()` once. That query
returns NOTHING on a correctly configured deployment: `apiSettings` carries
`FORCE ROW LEVEL SECURITY` (see `common/rls/get_enable_policy_sql`) and its
policy matches on `NULLIF(current_setting('app.current_org', true), '')`, which
is empty during a migration. So the backfill silently created nothing, and
every legacy key then answered 409. It looked correct in both places anyone
would have checked: the dev database runs as a Postgres superuser, which
bypasses RLS entirely, and the test suite runs on SQLite, which has no RLS at
all.

Walking orgs and setting `app.current_org` for each is the same shape
`common/migrations/0031_backfill_portal_tokens.py` documents. This one always
walks rather than probing for RLS first, because `apiSettings` holds at most a
handful of rows per org and one code path that works everywhere beats two that
each work somewhere. `org` is not an org-scoped table, so the outer loop sees
every row regardless of context.

Setting the context is also what lets the INSERT succeed: `web_form` has a
`WITH CHECK (org_id = current_setting(...))` policy, so creating a row with an
empty context is refused outright.

This is belt and braces, not the only guarantee. `webforms.legacy
.ensure_web_form` provisions a form on first use, which covers the keys minted
after this migration ran.
"""

from django.db import migrations

from webforms.legacy import build_web_form


def backfill(apps, schema_editor):
    connection = schema_editor.connection
    # RLS and `set_config` are Postgres-only; the SQLite test backend hides
    # nothing, so the loop still runs and only the context call is skipped.
    is_postgres = connection.vendor == "postgresql"

    Org = apps.get_model("common", "Org")
    APISettings = apps.get_model("common", "APISettings")
    Profile = apps.get_model("common", "Profile")
    WebForm = apps.get_model("webforms", "WebForm")
    WebFormField = apps.get_model("webforms", "WebFormField")

    def set_context(value):
        if not is_postgres:
            return
        with connection.cursor() as cursor:
            cursor.execute("SELECT set_config('app.current_org', %s, false)", [value])

    for org_id in Org.objects.values_list("id", flat=True).iterator():
        set_context(str(org_id))
        for setting in APISettings.objects.filter(org_id=org_id):
            if WebForm.objects.filter(legacy_api_setting=setting).exists():
                continue
            build_web_form(
                setting,
                web_form_model=WebForm,
                field_model=WebFormField,
                profile_model=Profile,
            )
    set_context("")


def unbackfill(apps, schema_editor):
    """Remove only the forms this migration created.

    Filtered on `legacy_api_setting`, so a form an admin built by hand is never
    deleted by a rollback.
    """
    WebForm = apps.get_model("webforms", "WebForm")
    WebForm.objects.filter(legacy_api_setting__isnull=False).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("webforms", "0001_initial"),
        ("common", "0040_comment_max_length"),
    ]

    operations = [
        migrations.RunPython(backfill, reverse_code=unbackfill),
    ]
