"""A merged-away record's `.deleted` payload lists the assignees it had.

`merge_records` moves the loser's assignees to the keeper when the keeper has
none, and does that before deleting the loser. The payload used to be built at
`pre_delete`, after the move, so it listed nobody. The merge now takes the
snapshot before anything moves, and the `pre_delete` receiver keeps it.
The payload carries `assigned_to` only; teams are not part of it.
"""

from unittest import mock

import pytest

from accounts.models import Account
from contacts.models import Contact
from leads.models import Lead
from webhooks import signals
from webhooks.models import WebhookDelivery, WebhookEndpoint

MAKERS = {
    "leads": ("lead", lambda org, n: Lead.objects.create(org=org, first_name=n)),
    "contacts": (
        "contact",
        lambda org, n: Contact.objects.create(org=org, first_name=n, last_name="L"),
    ),
    "accounts": ("account", lambda org, n: Account.objects.create(org=org, name=n)),
}


@pytest.fixture(autouse=True)
def no_broker():
    with mock.patch("webhooks.emit.deliver_webhook.delay") as delay:
        yield delay


def _hook(org, admin_profile, prefix):
    return WebhookEndpoint.objects.create(
        org=org,
        url="https://hooks.example.com/in",
        events=[f"{prefix}.deleted", f"{prefix}.updated"],
        created_by=admin_profile.user,
    )


def _deleted(hook, prefix):
    return WebhookDelivery.objects.get(endpoint=hook, event=f"{prefix}.deleted")


@pytest.mark.parametrize("module", sorted(MAKERS))
class TestMergedAway:
    def test_the_keeper_had_nobody(
        self, module, admin_client, org_a, admin_profile, user_profile
    ):
        prefix, make = MAKERS[module]
        hook = _hook(org_a, admin_profile, prefix)
        keeper, loser = make(org_a, "Kay"), make(org_a, "Lou")
        loser.assigned_to.add(user_profile)

        response = admin_client.post(
            f"/api/{module}/{keeper.id}/merge/",
            {"merge_id": str(loser.id)},
            format="json",
        )
        assert response.status_code == 200, response.content

        data = _deleted(hook, prefix).payload["data"]
        assert data["id"] == str(loser.id)
        assert data["assigned_to"] == [str(user_profile.id)]
        # The assignee moved to the keeper, which is why the snapshot had to
        # be taken first.
        assert list(keeper.assigned_to.all()) == [user_profile]

    def test_the_keeper_had_its_own(
        self, module, admin_client, org_a, admin_profile, user_profile
    ):
        prefix, make = MAKERS[module]
        hook = _hook(org_a, admin_profile, prefix)
        keeper, loser = make(org_a, "Kay"), make(org_a, "Lou")
        keeper.assigned_to.add(admin_profile)
        loser.assigned_to.add(user_profile)

        response = admin_client.post(
            f"/api/{module}/{keeper.id}/merge/",
            {"merge_id": str(loser.id)},
            format="json",
        )
        assert response.status_code == 200, response.content

        data = _deleted(hook, prefix).payload["data"]
        assert data["assigned_to"] == [str(user_profile.id)]
        assert list(keeper.assigned_to.all()) == [admin_profile]


class TestOrdinaryDelete:
    def test_a_plain_delete_still_lists_its_assignees(
        self, admin_client, org_a, admin_profile, user_profile
    ):
        hook = _hook(org_a, admin_profile, "lead")
        lead = Lead.objects.create(org=org_a, first_name="Ada")
        lead.assigned_to.add(user_profile)

        response = admin_client.delete(f"/api/leads/{lead.id}/")
        assert response.status_code in (200, 204), response.content

        data = _deleted(hook, "lead").payload["data"]
        assert data["id"] == str(lead.id)
        assert data["first_name"] == "Ada"
        assert data["assigned_to"] == [str(user_profile.id)]

    def test_pre_delete_builds_a_snapshot_when_none_is_set(self, org_a):
        lead = Lead.objects.create(org=org_a, first_name="Ada")
        signals.on_pre_delete(Lead, lead)
        assert lead._webhook_snapshot["first_name"] == "Ada"

    def test_pre_delete_keeps_a_snapshot_already_set(self, org_a):
        lead = Lead.objects.create(org=org_a, first_name="Ada")
        lead._webhook_snapshot = {"kept": True}
        signals.on_pre_delete(Lead, lead)
        assert lead._webhook_snapshot == {"kept": True}
