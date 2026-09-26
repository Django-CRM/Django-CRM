"""Give every org its default deal pipeline and put every deal in it.

The pipeline holds the six stages `Opportunity.stage` has always offered, with
the same codes, labels and order, so no stored value changes meaning. Closed
Won is the won stage and Closed Lost the lost one; the rest are open. Each
open stage takes the org's `StageAgingConfig` row for that code when there is
one (expected and warning days) and today's default expected days otherwise.
An old expected of 0 meant "red at once"; on `DealStage` an empty or zero
expected means "never ages" (`workflow.aging_thresholds`), so a 0 carries over
as 1, the smallest value the settings API accepts, which keeps the deal aging.
That makes `DealStage` the only store of the rotting thresholds; 0021 drops
the old table.

The stage list is frozen here rather than imported, so a later change to the
seed a new pipeline gets cannot rewrite what this migration did.

THE ORG LOOP IS NOT DECORATION

`opportunity`, `stage_aging_config` and the two new tables are all under
FORCE ROW LEVEL SECURITY, keyed on `app.current_org`, which is empty during a
migration. An unscoped query would read and update nothing in production while
working on a superuser dev database and on SQLite (see common/0042 for the
same shape and the backfill it once cost). So this walks the orgs, which are
not org-scoped, and sets the context for each.

REVERSE

Writes each default pipeline's rotting days back to `stage_aging_config` for
the six original codes, clears every deal's pipeline and deletes the
pipelines. A deal an admin moved into a custom stage keeps that code, which
the older code will show as it is.
"""

from django.db import migrations

DEFAULT_STAGES = (
    ("PROSPECTING", "Prospecting", "open", 14),
    ("QUALIFICATION", "Qualification", "open", 14),
    ("PROPOSAL", "Proposal", "open", 10),
    ("NEGOTIATION", "Negotiation", "open", 10),
    ("CLOSED_WON", "Closed Won", "won", None),
    ("CLOSED_LOST", "Closed Lost", "lost", None),
)


def _org_loop(apps, schema_editor, per_org):
    connection = schema_editor.connection
    is_postgres = connection.vendor == "postgresql"

    def set_context(value):
        if not is_postgres:
            return
        with connection.cursor() as cursor:
            cursor.execute("SELECT set_config('app.current_org', %s, false)", [value])

    Org = apps.get_model("common", "Org")
    try:
        for org_id in Org.objects.values_list("id", flat=True).iterator():
            set_context(str(org_id))
            per_org(org_id)
    finally:
        set_context("")


def create_default_pipelines(apps, schema_editor):
    DealPipeline = apps.get_model("opportunity", "DealPipeline")
    DealStage = apps.get_model("opportunity", "DealStage")
    Opportunity = apps.get_model("opportunity", "Opportunity")
    StageAgingConfig = apps.get_model("opportunity", "StageAgingConfig")

    def per_org(org_id):
        pipeline = DealPipeline.objects.filter(org_id=org_id, is_default=True).first()
        if pipeline is None:
            aging = {
                config.stage: config
                for config in StageAgingConfig.objects.filter(org_id=org_id)
            }
            pipeline = DealPipeline.objects.create(
                org_id=org_id, name="Sales", is_default=True
            )
            stages = []
            for order, (code, label, kind, default_days) in enumerate(
                DEFAULT_STAGES, start=1
            ):
                config = aging.get(code) if kind == "open" else None
                stages.append(
                    DealStage(
                        org_id=org_id,
                        pipeline=pipeline,
                        code=code,
                        label=label,
                        kind=kind,
                        order=order,
                        expected_days=max(config.expected_days, 1)
                        if config
                        else default_days,
                        warning_days=config.warning_days if config else None,
                    )
                )
            DealStage.objects.bulk_create(stages)
        Opportunity.objects.filter(org_id=org_id, pipeline__isnull=True).update(
            pipeline=pipeline
        )

    _org_loop(apps, schema_editor, per_org)


def remove_default_pipelines(apps, schema_editor):
    DealPipeline = apps.get_model("opportunity", "DealPipeline")
    DealStage = apps.get_model("opportunity", "DealStage")
    Opportunity = apps.get_model("opportunity", "Opportunity")
    StageAgingConfig = apps.get_model("opportunity", "StageAgingConfig")
    original = {code for code, _label, kind, _days in DEFAULT_STAGES if kind == "open"}

    def per_org(org_id):
        for stage in DealStage.objects.filter(
            org_id=org_id,
            pipeline__is_default=True,
            code__in=original,
            expected_days__isnull=False,
        ):
            StageAgingConfig.objects.update_or_create(
                org_id=org_id,
                stage=stage.code,
                defaults={
                    "expected_days": stage.expected_days,
                    "warning_days": stage.warning_days,
                },
            )
        Opportunity.objects.filter(org_id=org_id).update(pipeline=None)
        DealStage.objects.filter(org_id=org_id).delete()
        DealPipeline.objects.filter(org_id=org_id).delete()

    _org_loop(apps, schema_editor, per_org)


class Migration(migrations.Migration):
    dependencies = [
        ("opportunity", "0019_enable_rls_deal_pipelines"),
    ]

    operations = [
        migrations.RunPython(create_default_pipelines, remove_default_pipelines),
    ]
