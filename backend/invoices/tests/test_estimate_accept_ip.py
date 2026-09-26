"""The IP recorded when a customer accepts an estimate is evidence of who
authorised it, so the customer cannot choose it with `X-Forwarded-For`.

`common.request_meta.client_ip` believes only the entries our own proxies
appended (`REST_FRAMEWORK["NUM_PROXIES"]`), counted from the right.
"""

import datetime

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from invoices.models import Estimate


@pytest.fixture
def sent_estimate(org_a):
    return Estimate.objects.create(
        title="Fit-out",
        client_name="Dana Buyer",
        client_email="dana@buyer.example",
        currency="USD",
        status="Sent",
        issue_date=timezone.localdate(),
        expiry_date=timezone.localdate() + datetime.timedelta(days=30),
        org=org_a,
    )


def _accept(estimate, **meta):
    response = APIClient().post(
        f"/api/public/estimate/{estimate.public_token}/accept/",
        {"name": "Dana Buyer", "email": "dana@buyer.example"},
        format="json",
        **meta,
    )
    assert response.status_code == 200, response.content
    estimate.refresh_from_db()
    return estimate.accepted_ip


def test_a_forged_header_is_not_recorded(sent_estimate):
    ip = _accept(
        sent_estimate, HTTP_X_FORWARDED_FOR="6.6.6.6", REMOTE_ADDR="198.51.100.7"
    )
    assert ip == "198.51.100.7"


def test_behind_one_proxy_its_entry_is_recorded(sent_estimate, settings):
    settings.REST_FRAMEWORK = {**settings.REST_FRAMEWORK, "NUM_PROXIES": 1}
    ip = _accept(
        sent_estimate,
        HTTP_X_FORWARDED_FOR="6.6.6.6, 203.0.113.9",
        REMOTE_ADDR="127.0.0.1",
    )
    assert ip == "203.0.113.9"
