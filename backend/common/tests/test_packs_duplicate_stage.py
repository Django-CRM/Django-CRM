"""A vertical pack may not define a ticket stage that maps to Duplicate.

A ticket becomes Duplicate only by being merged. `CaseStageSerializer` refuses
such a stage when one is made through the API, but a pack writes its stages
without that serializer, so the manifest is refused at validation instead.
"""

import pytest

from cases.workflow import DUPLICATE_BY_MERGE_ONLY
from common.packs.schema import PackValidationError, validate_manifest


def _case_pack(maps_to_status):
    return {
        "id": "demo",
        "version": 1,
        "name": "Demo",
        "case_pipeline": {
            "name": "Support",
            "stages": [
                {"name": "Open"},
                {"name": "Dupes", "maps_to_status": maps_to_status},
            ],
        },
    }


def test_a_case_stage_mapped_to_duplicate_is_refused():
    with pytest.raises(PackValidationError) as exc:
        validate_manifest(_case_pack("Duplicate"))
    message = str(exc.value)
    assert (
        "case_pipeline.stages[1]: maps_to_status 'Duplicate' is not allowed" in message
    )
    assert DUPLICATE_BY_MERGE_ONLY in message


@pytest.mark.parametrize("status", ["Closed", "Pending", None])
def test_a_case_stage_mapped_to_any_other_status_is_accepted(status):
    raw = _case_pack(status)
    assert validate_manifest(raw) is raw


def test_a_task_stage_named_duplicate_is_not_this_rule():
    """Task stages have no status vocabulary; the rule is about tickets."""
    raw = {
        "id": "demo",
        "version": 1,
        "name": "Demo",
        "task_pipeline": {
            "name": "Work",
            "stages": [{"name": "Dupes", "maps_to_status": "Duplicate"}],
        },
    }
    assert validate_manifest(raw) is raw
