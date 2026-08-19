"""Hermetic guarantees for the read-only historical reconciliation report."""

import json
import os
from io import StringIO

import pytest
from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext

from accounts.models import Account
from contacts.models import Contact
from leads.historical_reconciliation import (
    HistoricalReconciliationError,
    build_historical_conversion_report,
)
from leads.models import Lead, LeadConversion
from opportunity.models import Opportunity

pytestmark = pytest.mark.django_db


def _lead(org, suffix):
    return Lead.objects.create(
        org=org,
        first_name="Synthetic",
        email=f"{suffix}@example.invalid",
        company_name=f"Synthetic {suffix}",
        status="converted",
    )


def _fixtures(org_a, org_b):
    proven = _lead(org_a, "proven")
    account = Account.objects.create(org=org_a, name="Proven account")
    contact = Contact.objects.create(
        org=org_a,
        first_name="Proven",
        last_name="Contact",
        email="proven-contact@example.invalid",
        account=account,
    )
    opportunity = Opportunity.objects.create(
        org=org_a, name="Proven opportunity", account=account
    )
    LeadConversion.objects.create(
        org=org_a,
        lead=proven,
        account=account,
        contact=contact,
        opportunity=opportunity,
        account_created=True,
        contact_created=True,
        opportunity_created=True,
    )

    ambiguous = _lead(org_a, "ambiguous")
    ambiguous_account = Account.objects.create(org=org_a, name="Ambiguous account")
    ambiguous_contact = Contact.objects.create(
        org=org_a,
        first_name="Ambiguous",
        last_name="Contact",
        email="ambiguous-contact@example.invalid",
        account=ambiguous_account,
    )
    ambiguous.contacts.add(ambiguous_contact)
    ambiguous_opportunity = Opportunity.objects.create(
        org=org_a, name="Ambiguous opportunity", account=ambiguous_account
    )
    ambiguous_opportunity.contacts.add(ambiguous_contact)

    no_evidence = _lead(org_a, "none")

    conflict = _lead(org_a, "conflict")
    foreign_contact = Contact.objects.create(
        org=org_b,
        first_name="Foreign",
        last_name="Contact",
        email="foreign-contact@example.invalid",
    )
    conflict.contacts.add(foreign_contact)
    return proven, ambiguous, no_evidence, conflict


def test_report_classifies_without_writes_or_raw_identifiers(org_a, org_b):
    leads = _fixtures(org_a, org_b)
    raw_ids = {str(item.id) for item in leads}

    with CaptureQueriesContext(connection) as queries:
        report = build_historical_conversion_report(reference_key=b"k" * 32)

    assert report["counts"] == {
        "proven": 1,
        "ambiguous": 1,
        "no_evidence": 1,
        "cross_org_conflict": 1,
    }
    assert report["mode"] == "dry_run"
    assert report["write_authorized"] is False
    serialized = json.dumps(report)
    assert not any(raw_id in serialized for raw_id in raw_ids)
    assert "@example.invalid" not in serialized
    assert not any(
        query["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
        for query in queries.captured_queries
    )


def test_report_is_bounded_and_requires_strong_key(org_a):
    _lead(org_a, "first")
    _lead(org_a, "second")
    report = build_historical_conversion_report(reference_key=b"r" * 32, max_records=1)
    assert report["total_converted"] == 2
    assert report["scanned"] == 1
    assert report["truncated"] is True
    with pytest.raises(HistoricalReconciliationError, match="256 bits"):
        build_historical_conversion_report(reference_key=b"weak")


def test_management_command_emits_json_and_has_no_apply_mode(tmp_path, org_a):
    _lead(org_a, "command")
    key_file = tmp_path / "reference.key"
    key_file.write_bytes(b"z" * 32)
    os.chmod(key_file, 0o600)
    output = StringIO()

    call_command(
        "reconcile_converted_leads",
        reference_key_file=str(key_file),
        stdout=output,
    )

    report = json.loads(output.getvalue())
    assert report["mode"] == "dry_run"
    assert report["counts"]["no_evidence"] == 1
    with pytest.raises(TypeError):
        call_command(
            "reconcile_converted_leads",
            reference_key_file=str(key_file),
            apply=True,
        )


def test_command_rejects_non_private_key_file(tmp_path, org_a):
    _lead(org_a, "permissions")
    key_file = tmp_path / "public.key"
    key_file.write_bytes(b"p" * 32)
    os.chmod(key_file, 0o644)

    with pytest.raises(Exception, match="private permissions"):
        call_command("reconcile_converted_leads", reference_key_file=str(key_file))
