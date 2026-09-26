from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.permissions import HasOrgContext, is_org_admin
from opportunity.models import DealPipeline
from opportunity.workflow import OPEN

# The same ceiling `DealStageSerializer` applies: ten years in one stage is
# already not a threshold anyone means.
MAX_DAYS = 3650


def _row(stage):
    return {
        "id": str(stage.id),
        "stage": stage.code,
        "label": stage.label,
        "expected_days": stage.expected_days,
        "warning_days": stage.warning_days,
    }


class StageAgingConfigView(APIView):
    """Rotting days for the default pipeline's open stages.

    The contract predates pipelines, when each org had one fixed set of stages,
    so it reads and writes the default pipeline's `DealStage` rows, which are
    now the only store of these numbers. Other pipelines are configured
    through `/opportunities/pipelines/`.
    """

    permission_classes = (IsAuthenticated, HasOrgContext)

    def _open_stages(self, org):
        return DealPipeline.default_for(org).stages.filter(kind=OPEN)

    def get(self, request):
        """Return aging config for the default pipeline's open stages."""
        return Response([_row(s) for s in self._open_stages(request.profile.org)])

    def put(self, request):
        """Bulk update stage aging configs (admin only).

        Rows naming a stage that is not an open stage of the default pipeline,
        or carrying an unusable `expected_days`, are skipped, as they always
        were.
        """
        if not is_org_admin(request.profile) and not request.user.is_superuser:
            return Response(
                {"error": True, "errors": "Only admins can update aging config"},
                status=status.HTTP_403_FORBIDDEN,
            )

        configs_data = request.data
        if not isinstance(configs_data, list):
            return Response(
                {"error": True, "errors": "Expected a list of stage configs"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        stages = {s.code: s for s in self._open_stages(request.profile.org)}
        results = []
        for item in configs_data:
            if not isinstance(item, dict) or not isinstance(item.get("stage"), str):
                continue
            stage = stages.get(item["stage"])
            if stage is None:
                continue

            try:
                expected_days = int(item.get("expected_days", 14))
            except (TypeError, ValueError):
                continue
            if not 1 <= expected_days <= MAX_DAYS:
                continue

            warning_days = item.get("warning_days")
            if warning_days is not None:
                try:
                    warning_days = int(warning_days)
                except (TypeError, ValueError):
                    warning_days = None
            if warning_days is not None and not 1 <= warning_days <= MAX_DAYS:
                warning_days = None

            stage.expected_days = expected_days
            stage.warning_days = warning_days
            stage.save(
                update_fields=[
                    "expected_days",
                    "warning_days",
                    "updated_at",
                    "updated_by",
                ]
            )
            results.append(_row(stage))

        return Response(
            {"error": False, "message": "Aging config updated", "configs": results},
            status=status.HTTP_200_OK,
        )
