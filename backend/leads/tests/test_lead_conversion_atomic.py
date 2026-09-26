"""Converting a lead commits whole or not at all, and only once.

Conversion saves the lead, creates or joins an account, creates or links a
contact, creates a deal and marks the lead converted. It used to run with no
transaction, so a failure part-way left an account and a contact behind a lead
that still read as unconverted, and converting again built them twice. And two
conversions of one lead at the same moment could both pass the "already
converted?" check and each create a deal. See `_atomic_when_converting`.
"""

import threading
import time
from unittest import mock

import pytest
from django.db import connection

from accounts.models import Account
from common.testing import _make_authenticated_client
from contacts.models import Contact
from leads.models import Lead
from leads.services import convert_lead_to_account
from opportunity.models import Opportunity

EMAIL = "ada@convert.test"


def _lead(org):
    return Lead.objects.create(
        org=org,
        first_name="Ada",
        last_name="Byron",
        email=EMAIL,
        company_name="Analytical Engines",
        opportunity_amount=500,
        status="assigned",
    )


def _nothing_downstream(org):
    return (
        not Account.objects.filter(org=org).exists()
        and not Contact.objects.filter(org=org).exists()
        and not Opportunity.objects.filter(org=org).exists()
    )


@pytest.mark.parametrize("verb", ["put", "patch"])
def test_a_failure_after_the_account_leaves_nothing_behind(admin_client, org_a, verb):
    lead = _lead(org_a)
    admin_client.raise_request_exception = False
    body = {
        "status": "converted",
        "first_name": "Ada",
        "email": EMAIL,
        "job_title": "Countess",
    }
    with mock.patch(
        "leads.services.Opportunity.objects.create",
        side_effect=RuntimeError("the deal could not be written"),
    ):
        response = getattr(admin_client, verb)(
            f"/api/leads/{lead.id}/", body, format="json"
        )

    assert response.status_code == 500, response.content
    assert _nothing_downstream(org_a)
    lead.refresh_from_db()
    assert lead.status == "assigned"
    # The fields sent with the conversion were rolled back with it.
    assert lead.job_title is None


def test_a_failed_conversion_on_create_leaves_no_lead_either(admin_client, org_a):
    admin_client.raise_request_exception = False
    with mock.patch(
        "leads.services.Opportunity.objects.create",
        side_effect=RuntimeError("the deal could not be written"),
    ):
        response = admin_client.post(
            "/api/leads/",
            {
                "first_name": "Ada",
                "last_name": "Byron",
                "email": EMAIL,
                "company_name": "Analytical Engines",
                "status": "converted",
            },
            format="json",
        )
    assert response.status_code == 500
    assert not Lead.objects.filter(org=org_a).exists()
    assert _nothing_downstream(org_a)


def test_the_assignee_email_waits_for_the_commit(admin_client, org_a, admin_profile):
    lead = _lead(org_a)
    lead.assigned_to.add(admin_profile)
    admin_client.raise_request_exception = False
    with (
        mock.patch("leads.views.lead_views.send_email_to_assigned_user") as sender,
        mock.patch(
            "leads.services.Opportunity.objects.create",
            side_effect=RuntimeError("boom"),
        ),
    ):
        admin_client.patch(
            f"/api/leads/{lead.id}/", {"status": "converted"}, format="json"
        )
    sender.delay.assert_not_called()


def test_a_successful_conversion_commits_everything_and_emails(
    admin_client, org_a, admin_profile, django_capture_on_commit_callbacks
):
    lead = _lead(org_a)
    lead.assigned_to.add(admin_profile)
    with (
        mock.patch("leads.views.lead_views.send_email_to_assigned_user") as sender,
        django_capture_on_commit_callbacks(execute=True),
    ):
        response = admin_client.patch(
            f"/api/leads/{lead.id}/", {"status": "converted"}, format="json"
        )
    assert response.status_code == 200, response.content
    lead.refresh_from_db()
    assert lead.status == "converted"
    assert Account.objects.filter(org=org_a).count() == 1
    assert Contact.objects.filter(org=org_a).count() == 1
    assert Opportunity.objects.filter(org=org_a).count() == 1
    sender.delay.assert_called_once()


@pytest.mark.parametrize(
    "body", [{"status": "converted"}, {"is_converted": True}], ids=["status", "flag"]
)
def test_a_second_conversion_is_refused(admin_client, org_a, body):
    lead = _lead(org_a)
    first = admin_client.patch(
        f"/api/leads/{lead.id}/", {"status": "converted"}, format="json"
    )
    assert first.status_code == 200
    again = admin_client.patch(f"/api/leads/{lead.id}/", body, format="json")
    assert again.status_code == 400
    assert Opportunity.objects.filter(org=org_a).count() == 1


@pytest.mark.postgres_only
@pytest.mark.django_db(transaction=True)
def test_two_conversions_at_once_build_one_deal(admin_user, org_a, admin_profile):
    """Both requests start before either commits. The second waits on the
    lead's row lock, then reads it as converted and is refused. Without the
    lock both read "assigned" and each built a deal."""
    if connection.vendor != "postgresql":
        pytest.skip("row locks need PostgreSQL")

    lead = _lead(org_a)
    barrier = threading.Barrier(2)
    statuses, errors = [], []

    def slow_convert(*args, **kwargs):
        time.sleep(0.5)
        return convert_lead_to_account(*args, **kwargs)

    def worker():
        try:
            client = _make_authenticated_client(admin_user, org_a, admin_profile)
            barrier.wait()
            response = client.patch(
                f"/api/leads/{lead.id}/", {"status": "converted"}, format="json"
            )
            statuses.append(response.status_code)
        except Exception as exc:  # recorded and asserted on below
            errors.append(exc)
        finally:
            connection.close()

    with mock.patch("leads.services.convert_lead_to_account", slow_convert):
        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

    assert errors == []
    assert sorted(statuses) == [200, 400]
    assert Opportunity.objects.filter(org=org_a).count() == 1
