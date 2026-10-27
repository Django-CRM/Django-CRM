"""Crediting writes to a profile no request names, as a running rule does."""

from contextlib import contextmanager

from crum import set_current_request
from django.test import RequestFactory

from accounts.models import Account
from cases.models import Case
from common.actor import acting_for, current_actor
from common.models import Activity
from common.signals import create_activity


@contextmanager
def _request(profile):
    request = RequestFactory().get("/")
    request.profile = profile
    set_current_request(request)
    try:
        yield request
    finally:
        set_current_request(None)


def _rows(record):
    return Activity.objects.filter(org=record.org, entity_id=record.pk)


def test_activity_outside_a_request_is_written_under_an_override(org_a, admin_profile):
    assert current_actor() is None
    with acting_for(admin_profile, "Deal won"):
        assert current_actor() == (admin_profile, "Deal won")
        account = Account.objects.create(org=org_a, name="Acme")
    assert current_actor() is None

    [row] = _rows(account)
    assert (row.user, row.action, row.entity_type) == (
        admin_profile,
        "CREATE",
        "Account",
    )
    assert row.metadata == {"automation": "Deal won"}


def test_without_an_override_activity_is_unchanged(org_a, admin_profile, user_profile):
    quiet = Account.objects.create(org=org_a, name="No request")
    assert not _rows(quiet).exists()

    with _request(user_profile):
        loud = Account.objects.create(org=org_a, name="In a request")
    [row] = _rows(loud)
    assert (row.user, row.metadata) == (user_profile, {})


def test_an_override_inside_a_request_wins_over_its_profile(
    org_a, admin_profile, user_profile
):
    with _request(user_profile), acting_for(admin_profile, "Tidy names"):
        account = Account.objects.create(org=org_a, name="Acme")
    [row] = _rows(account)
    assert (row.user, row.metadata) == (admin_profile, {"automation": "Tidy names"})


def test_a_ticket_change_under_an_override_names_its_profile(
    org_a, admin_profile, user_profile
):
    ticket = Case.objects.create(org=org_a, name="Login bug", status="New")
    with _request(user_profile), acting_for(admin_profile, "Escalate"):
        ticket.status = "Pending"
        ticket.save()
    row = _rows(ticket).get(action="STATUS_CHANGED")
    assert row.user == admin_profile

    with acting_for(admin_profile, "Escalate"):
        ticket.priority = "Urgent"
        ticket.save()
    assert _rows(ticket).get(action="PRIORITY_CHANGED").user == admin_profile


def test_create_activity_keeps_metadata(org_a, admin_profile, user_profile):
    account = Account.objects.create(org=org_a, name="Acme")
    with _request(user_profile):
        create_activity(account, "UPDATE", "Account", metadata={"object": "site"})
        create_activity(account, "ASSIGN", "Account")
    with acting_for(admin_profile, "Tidy names"):
        create_activity(account, "DELETE", "Account", metadata={"object": "site"})
    assert {row.action: row.metadata for row in _rows(account)} == {
        "UPDATE": {"object": "site"},
        "ASSIGN": {},
        "DELETE": {"automation": "Tidy names", "object": "site"},
    }
