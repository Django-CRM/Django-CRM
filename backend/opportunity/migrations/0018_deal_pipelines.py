# Deal pipelines and stages (the schema half).
#
# `Opportunity.stage` loses its fixed choices: a stage is now a `DealStage`
# code of the deal's pipeline. `pipeline` is added nullable so the data
# migration (0020) can fill it for every existing deal before 0021 makes it
# required. RLS for the two new tables is 0019.

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("common", "0042_recompute_tag_slugs"),
        ("opportunity", "0017_salesgoal_currency"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name="opportunity",
            name="stage",
            field=models.CharField(blank=True, max_length=64, verbose_name="Stage"),
        ),
        migrations.CreateModel(
            name="DealPipeline",
            fields=[
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True, verbose_name="Created At"),
                ),
                (
                    "updated_at",
                    models.DateTimeField(
                        auto_now=True, verbose_name="Last Modified At"
                    ),
                ),
                (
                    "id",
                    models.UUIDField(
                        db_index=True,
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                        unique=True,
                    ),
                ),
                (
                    "name",
                    models.CharField(max_length=100, verbose_name="Pipeline Name"),
                ),
                ("is_default", models.BooleanField(default=False)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(class)s_created_by",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Created By",
                    ),
                ),
                (
                    "org",
                    models.ForeignKey(
                        help_text="Organization this record belongs to",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="%(class)s_set",
                        to="common.org",
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(class)s_updated_by",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Last Modified By",
                    ),
                ),
            ],
            options={
                "verbose_name": "Deal Pipeline",
                "verbose_name_plural": "Deal Pipelines",
                "db_table": "opportunity_pipeline",
                "ordering": ("-is_default", "name"),
            },
        ),
        migrations.AddField(
            model_name="opportunity",
            name="pipeline",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.RESTRICT,
                related_name="opportunities",
                to="opportunity.dealpipeline",
            ),
        ),
        migrations.CreateModel(
            name="DealStage",
            fields=[
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True, verbose_name="Created At"),
                ),
                (
                    "updated_at",
                    models.DateTimeField(
                        auto_now=True, verbose_name="Last Modified At"
                    ),
                ),
                (
                    "id",
                    models.UUIDField(
                        db_index=True,
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                        unique=True,
                    ),
                ),
                ("code", models.CharField(max_length=64, verbose_name="Code")),
                ("label", models.CharField(max_length=100, verbose_name="Label")),
                ("order", models.PositiveIntegerField(default=0)),
                (
                    "kind",
                    models.CharField(
                        choices=[("open", "Open"), ("won", "Won"), ("lost", "Lost")],
                        default="open",
                        max_length=10,
                    ),
                ),
                (
                    "expected_days",
                    models.PositiveIntegerField(
                        blank=True, null=True, verbose_name="Expected Days"
                    ),
                ),
                (
                    "warning_days",
                    models.PositiveIntegerField(
                        blank=True, null=True, verbose_name="Warning Days"
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(class)s_created_by",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Created By",
                    ),
                ),
                (
                    "org",
                    models.ForeignKey(
                        help_text="Organization this record belongs to",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="%(class)s_set",
                        to="common.org",
                    ),
                ),
                (
                    "pipeline",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="stages",
                        to="opportunity.dealpipeline",
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(class)s_updated_by",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Last Modified By",
                    ),
                ),
            ],
            options={
                "verbose_name": "Deal Stage",
                "verbose_name_plural": "Deal Stages",
                "db_table": "opportunity_pipeline_stage",
                "ordering": ("order", "created_at"),
            },
        ),
        migrations.AddIndex(
            model_name="dealpipeline",
            index=models.Index(
                fields=["org", "-created_at"], name="opportunity_org_id_598ce5_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="dealstage",
            index=models.Index(
                fields=["org", "-created_at"], name="opportunity_org_id_93e696_idx"
            ),
        ),
        migrations.AddConstraint(
            model_name="dealpipeline",
            constraint=models.UniqueConstraint(
                condition=models.Q(("is_default", True)),
                fields=("org",),
                name="unique_default_deal_pipeline_per_org",
            ),
        ),
        migrations.AddConstraint(
            model_name="dealpipeline",
            constraint=models.UniqueConstraint(
                fields=("org", "name"), name="unique_deal_pipeline_name_per_org"
            ),
        ),
        migrations.AddConstraint(
            model_name="dealstage",
            constraint=models.UniqueConstraint(
                fields=("pipeline", "code"), name="unique_deal_stage_code_per_pipeline"
            ),
        ),
    ]
