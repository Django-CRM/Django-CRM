"""Deal stages as a request reads them: one query for the org, reused per row.

Kind filters that need no per-request read live on the model module
(`stage_kind_q`). What is here needs the stage rows themselves: labels and
kinds for serializing many deals, and the per-stage aging cutoffs.
"""

from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from opportunity.models import DealStage
from opportunity.workflow import aging_thresholds


def stage_index(org_id):
    """`{(pipeline_id, code): DealStage}` for every deal stage in the org."""
    return {
        (stage.pipeline_id, stage.code): stage
        for stage in DealStage.objects.filter(org_id=org_id)
    }


def stage_choices(pipeline):
    """`[(code, label), ...]` in board order: the shape `common.utils.STAGES` had."""
    return [(stage.code, stage.label) for stage in pipeline.stages.all()]


def aging_q(stages, level, now=None):
    """A filter for deals at or past `level` ("yellow" or "red") in their stage.

    One clause per aging stage, since the threshold differs per stage; the
    cutoffs come from `workflow.aging_thresholds`, the same whole-day rule
    `Opportunity.get_aging_status` applies to one row, so a count built from
    this and the pills on the rows it counts agree. Matches nothing when no
    stage ages.
    """
    now = now or timezone.now()
    query = Q(pk__in=[])
    for stage in stages:
        thresholds = aging_thresholds(
            stage.kind, stage.expected_days, stage.warning_days
        )
        if thresholds is None:
            continue
        days = thresholds[0] if level == "yellow" else thresholds[1]
        query |= Q(
            pipeline_id=stage.pipeline_id,
            stage=stage.code,
            stage_changed_at__lte=now - timedelta(days=days),
        )
    return query
