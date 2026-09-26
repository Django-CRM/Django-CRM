"""A deal PATCH tells the people it newly assigns, and nobody else.

PUT has always emailed newly added assignees. PATCH, which is what the web and
the phone edit with, never did, so an assignment made there went unannounced.
"""

from unittest.mock import patch

import pytest

from opportunity.models import Opportunity

pytestmark = pytest.mark.django_db

MAIL = "opportunity.views.opportunity_views.send_email_to_assigned_user.delay"


@pytest.fixture
def deal(org_a, admin_user, admin_profile):
    deal = Opportunity.objects.create(
        name="Assigned deal", stage="QUALIFICATION", org=org_a
    )
    Opportunity.objects.filter(pk=deal.pk).update(created_by=admin_user)
    deal.assigned_to.add(admin_profile)
    return deal


def _patch(client, deal, body):
    with patch(MAIL) as mail:
        response = client.patch(f"/api/opportunities/{deal.id}/", body, format="json")
    assert response.status_code == 200, response.content
    return mail


def test_adding_an_assignee_emails_only_them(
    admin_client, deal, org_a, admin_profile, user_profile
):
    mail = _patch(
        admin_client,
        deal,
        {"assigned_to": [str(admin_profile.id), str(user_profile.id)]},
    )
    mail.assert_called_once_with([user_profile.id], deal.id, str(org_a.id))


def test_no_assigned_to_key_sends_nothing(admin_client, deal):
    mail = _patch(admin_client, deal, {"name": "Renamed"})
    mail.assert_not_called()


def test_unchanged_assignees_send_nothing(admin_client, deal, admin_profile):
    mail = _patch(admin_client, deal, {"assigned_to": [str(admin_profile.id)]})
    mail.assert_not_called()


def test_removing_an_assignee_sends_nothing(admin_client, deal):
    mail = _patch(admin_client, deal, {"assigned_to": []})
    mail.assert_not_called()
    assert deal.assigned_to.count() == 0
