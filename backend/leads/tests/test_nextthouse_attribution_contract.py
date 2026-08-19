"""Machine-readable Portal-to-CRM attribution contract tests."""

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker, ValidationError

SCHEMA_PATH = (
    Path(__file__).resolve().parents[3]
    / "docs/api/nextthouse-attribution-v1.schema.json"
)


@pytest.fixture(scope="module")
def validator():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def valid_command():
    return {
        "schema_version": "1.0",
        "lead_ref": "lead_opaque_01",
        "campaign_ref": "campaign_opaque_01",
        "touch_type": "first",
        "occurred_at": "2026-08-18T18:00:00Z",
        "source": "google",
        "medium": "organic",
        "campaign_key": "future-campaign",
        "landing_page_ref": "page_opaque_01",
        "referrer_domain": "nextthouse.com.br",
        "lawful_basis": "consent",
        "privacy_notice_version": "privacy-v1",
        "consent_evidence_ref": "evidence_opaque_01",
    }


def test_valid_command(validator):
    validator.validate(valid_command())


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source", "https://tracker.invalid"),
        ("campaign_key", "customer@example.invalid"),
        ("referrer_domain", "host.invalid/path"),
        ("lead_ref", "raw email@example.invalid"),
    ],
)
def test_contract_rejects_raw_urls_email_like_values_and_paths(validator, field, value):
    command = valid_command()
    command[field] = value
    with pytest.raises(ValidationError):
        validator.validate(command)


def test_consent_requires_evidence_reference(validator):
    command = valid_command()
    command.pop("consent_evidence_ref")
    with pytest.raises(ValidationError):
        validator.validate(command)


def test_unknown_fields_fail_closed(validator):
    command = valid_command()
    command["provider_payload"] = {"private": "not allowed"}
    with pytest.raises(ValidationError):
        validator.validate(command)
