import pytest

from common.packs.loader import get_pack
from common.packs.schema import PackValidationError, validate_manifest


def _minimal():
    return {"id": "demo", "version": 1, "name": "Demo"}


def test_minimal_manifest_validates():
    assert validate_manifest(_minimal())["id"] == "demo"


def test_unknown_top_level_key_is_rejected():
    raw = _minimal() | {"wat": 1}
    with pytest.raises(PackValidationError, match="wat"):
        validate_manifest(raw)


def test_is_default_is_rejected_anywhere():
    raw = _minimal() | {
        "lead_pipeline": {"name": "Sales", "is_default": True, "stages": []}
    }
    with pytest.raises(PackValidationError, match="is_default"):
        validate_manifest(raw)


def test_product_without_sku_is_rejected():
    raw = _minimal() | {"products": [{"name": "Course", "price": "100.00"}]}
    with pytest.raises(PackValidationError, match="sku"):
        validate_manifest(raw)


def test_unknown_custom_field_target_is_rejected():
    raw = _minimal() | {
        "custom_fields": [
            {"target": "Property", "key": "x", "label": "X", "field_type": "text"}
        ]
    }
    with pytest.raises(PackValidationError, match="Property"):
        validate_manifest(raw)


def test_win_probability_rejected_on_non_lead_stage():
    raw = _minimal() | {
        "case_pipeline": {
            "name": "Support",
            "stages": [{"name": "New", "win_probability": 50}],
        }
    }
    with pytest.raises(PackValidationError, match="win_probability"):
        validate_manifest(raw)


def _lead_stage(**stage):
    return _minimal() | {
        "lead_pipeline": {"name": "Sales", "stages": [{"name": "New", **stage}]}
    }


# A lead stage's win_probability is copied onto Lead.probability by the board
# move, and both columns carry a 0-100 check constraint (D11).
@pytest.mark.parametrize("value", [150, 101, -1, "50", True, False, 50.0, None])
def test_lead_stage_win_probability_out_of_range_or_not_an_int_is_rejected(value):
    with pytest.raises(
        PackValidationError,
        match=r"stages\[0\]: win_probability .* must be an integer from 0 to 100",
    ):
        validate_manifest(_lead_stage(win_probability=value))


@pytest.mark.parametrize("value", [0, 50, 100])
def test_lead_stage_win_probability_in_range_is_accepted(value):
    raw = _lead_stage(win_probability=value)
    assert validate_manifest(raw) is raw


def test_lead_stage_without_win_probability_is_accepted():
    raw = _lead_stage()
    assert validate_manifest(raw) is raw


def test_unknown_tag_color_is_rejected():
    raw = _minimal() | {"tags": [{"name": "Hot", "color": "chartreuse"}]}
    with pytest.raises(PackValidationError, match="chartreuse"):
        validate_manifest(raw)


def test_lead_stage_accepts_won():
    raw = _minimal() | {
        "lead_pipeline": {
            "name": "Sales",
            "stages": [{"name": "Closed Won", "stage_type": "won"}],
        }
    }
    stages = validate_manifest(raw)["lead_pipeline"]["stages"]
    assert stages[0]["stage_type"] == "won"


def test_case_stage_accepts_closed():
    raw = _minimal() | {
        "case_pipeline": {
            "name": "Support",
            "stages": [{"name": "Resolved", "stage_type": "closed"}],
        }
    }
    stages = validate_manifest(raw)["case_pipeline"]["stages"]
    assert stages[0]["stage_type"] == "closed"


def test_case_stage_rejects_won():
    raw = _minimal() | {
        "case_pipeline": {
            "name": "Support",
            "stages": [{"name": "Resolved", "stage_type": "won"}],
        }
    }
    with pytest.raises(PackValidationError, match="stage_type"):
        validate_manifest(raw)


def test_task_stage_accepts_in_progress():
    raw = _minimal() | {
        "task_pipeline": {
            "name": "Delivery",
            "stages": [{"name": "Doing", "stage_type": "in_progress"}],
        }
    }
    stages = validate_manifest(raw)["task_pipeline"]["stages"]
    assert stages[0]["stage_type"] == "in_progress"


def _with_lead(lead: dict) -> dict:
    return _minimal() | {"sample_data": {"leads": [{"title": "An enquiry"} | lead]}}


def test_sample_lead_source_outside_lead_source_is_rejected():
    # "website" reads as obviously valid and is not: LEAD_SOURCE has no such
    # entry, and Django does not enforce choices on .create(). Three shipped
    # packs carried this before the guard existed, the rows wrote cleanly and
    # surfaced only as a blank source column no filter could match.
    with pytest.raises(PackValidationError, match="source"):
        validate_manifest(_with_lead({"source": "website"}))


def test_sample_lead_source_from_lead_source_is_accepted():
    manifest = validate_manifest(_with_lead({"source": "partner"}))
    assert manifest["sample_data"]["leads"][0]["source"] == "partner"


def test_sample_lead_status_outside_lead_status_is_rejected():
    with pytest.raises(PackValidationError, match="status"):
        validate_manifest(_with_lead({"status": "qualified"}))


def test_registry_lookup_of_unknown_id_returns_none():
    assert get_pack("no-such-pack") is None


def test_registry_cannot_be_traversed():
    assert get_pack("../../etc/passwd") is None
    assert get_pack("real-estate/../demo") is None
