"""Register every existing inbound mailbox in the unscoped token -> org lookup.

The inbound email webhook (`/api/cases/inbound/<mailbox_id>/`) is anonymous,
so it now resolves the mailbox's org from `portal_access_token` before it sets
the RLS context and reads `inbound_mailbox`. New mailboxes are registered when
they are created (cases/signals.py); this gives the existing ones their row,
keyed on the SHA-256 of the mailbox id in canonical UUID form, exactly as
`common.portal_tokens.portal_token_hash(str(mailbox.id))` computes it.

THE ORG LOOP IS NOT DECORATION

`inbound_mailbox` is under FORCE ROW LEVEL SECURITY keyed on
`app.current_org`, which is empty during a migration, so an unscoped read sees
no mailbox in production while working on a superuser dev database and on
SQLite. This walks the orgs, which are not org-scoped, and sets the context for
each, the same shape as opportunity/0020. `portal_access_token` itself has no
policy, so the writes need no context.

Idempotent (an upsert on `token_hash`) and reversible: the reverse deletes the
inbound mailbox rows and nothing else. Deactivated mailboxes are registered
too; the webhook's own scoped query filters on `is_active`.
"""

import hashlib

from django.db import migrations, models

INBOUND_MAILBOX = "inbound_mailbox"


def register_mailboxes(apps, schema_editor):
    connection = schema_editor.connection
    is_postgres = connection.vendor == "postgresql"

    def set_context(value):
        if not is_postgres:
            return
        with connection.cursor() as cursor:
            cursor.execute("SELECT set_config('app.current_org', %s, false)", [value])

    Org = apps.get_model("common", "Org")
    InboundMailbox = apps.get_model("cases", "InboundMailbox")
    PortalAccessToken = apps.get_model("common", "PortalAccessToken")

    try:
        for org_id in Org.objects.values_list("id", flat=True).iterator():
            set_context(str(org_id))
            for mailbox_id in InboundMailbox.objects.filter(org_id=org_id).values_list(
                "id", flat=True
            ):
                PortalAccessToken.objects.update_or_create(
                    token_hash=hashlib.sha256(str(mailbox_id).encode()).hexdigest(),
                    defaults={
                        "org_id": org_id,
                        "resource_type": INBOUND_MAILBOX,
                        "resource_id": mailbox_id,
                    },
                )
    finally:
        set_context("")


def unregister_mailboxes(apps, schema_editor):
    PortalAccessToken = apps.get_model("common", "PortalAccessToken")
    PortalAccessToken.objects.filter(resource_type=INBOUND_MAILBOX).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("common", "0048_securityauditlog_credential_events"),
        ("cases", "0031_escalationpolicy_next_response_hours"),
    ]

    operations = [
        migrations.AlterField(
            model_name="portalaccesstoken",
            name="resource_type",
            field=models.CharField(
                choices=[
                    ("invoice", "Invoice"),
                    ("estimate", "Estimate"),
                    ("csat", "CSAT survey"),
                    ("inbound_mailbox", "Inbound mailbox"),
                ],
                max_length=16,
            ),
        ),
        migrations.RunPython(register_mailboxes, unregister_mailboxes),
    ]
