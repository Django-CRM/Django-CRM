"""PostgreSQL RLS coverage for marketing attribution tables."""

import pytest
from django.db import connection

from common.rls import ORG_SCOPED_TABLES
from conftest import rls_org
from leads.models import MarketingCampaign

TABLES = [
    "marketing_campaign",
    "lead_attribution_touch",
    "data_subject_request",
    "data_subject_request_event",
    "lead_conversion",
]


def test_marketing_tables_are_registered_as_org_scoped():
    assert not [table for table in TABLES if table not in ORG_SCOPED_TABLES]


@pytest.mark.postgres_only
@pytest.mark.django_db
def test_marketing_tables_have_rls_policies():
    if connection.vendor != "postgresql":
        pytest.skip("RLS requires PostgreSQL")

    with connection.cursor() as cursor:
        for table in TABLES:
            cursor.execute(
                """
                SELECT c.relrowsecurity,
                       (SELECT COUNT(*) FROM pg_policy p WHERE p.polrelid = c.oid)
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public' AND c.relname = %s
                """,
                [table],
            )
            row = cursor.fetchone()
            assert row is not None, f"{table} does not exist"
            assert row[0], f"{table} does not have RLS enabled"
            assert row[1] >= 2, f"{table} has only {row[1]} RLS policies"


@pytest.mark.postgres_only
@pytest.mark.django_db
def test_campaign_rows_are_isolated_by_active_org(org_a, org_b):
    if connection.vendor != "postgresql":
        pytest.skip("RLS requires PostgreSQL")

    with rls_org(org_a):
        MarketingCampaign.objects.create(
            org=org_a,
            code="org-a-campaign",
            name="Org A campaign",
            primary_channel="organic_search",
        )
    with rls_org(org_b):
        MarketingCampaign.objects.create(
            org=org_b,
            code="org-b-campaign",
            name="Org B campaign",
            primary_channel="paid_social",
        )

    with rls_org(org_a):
        assert list(MarketingCampaign.objects.values_list("code", flat=True)) == [
            "org-a-campaign"
        ]
