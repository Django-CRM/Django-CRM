"""Deterministic lead duplicate candidate tests."""

import pytest

from leads.deduplication import find_duplicate_lead
from leads.models import Lead

pytestmark = pytest.mark.django_db


def make_lead(org, **values):
    return Lead.objects.create(org=org, first_name="Synthetic", **values)


def test_email_match_is_case_insensitive_and_org_scoped(org_a, org_b):
    expected = make_lead(org_a, email="lead@example.invalid")
    make_lead(org_b, email="other@example.invalid")

    result = find_duplicate_lead(org=org_a, email=" LEAD@EXAMPLE.INVALID ")

    assert result.lead == expected
    assert result.matched_by == "email"
    assert result.ambiguous is False


def test_unique_normalized_phone_is_a_candidate(org_a):
    expected = make_lead(org_a, phone="+55 (11) 99999-0000")

    result = find_duplicate_lead(org=org_a, phone="5511999990000")

    assert result.lead == expected
    assert result.matched_by == "phone"


def test_shared_phone_fails_closed_as_ambiguous(org_a):
    make_lead(org_a, phone="+55 11 3333-4444")
    make_lead(org_a, phone="+55 (11) 3333-4444")

    result = find_duplicate_lead(org=org_a, phone="551133334444")

    assert result.lead is None
    assert result.matched_by is None
    assert result.ambiguous is True


def test_short_phone_is_not_used_for_matching(org_a):
    make_lead(org_a, phone="1234567")
    assert find_duplicate_lead(org=org_a, phone="1234567").lead is None
