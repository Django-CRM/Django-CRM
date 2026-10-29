"""No delete reaches an off module's records.

Deleting an account, a ticket or a person cascades into, or is blocked by,
records in other modules. While such a module is off the delete answers 409
and names what it would reach; with the module on it behaves as it always has.
"""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.apps import apps
from django.db import connection, models
from django.utils import timezone

from accounts.models import Account
from cases.approvals import Approval, ApprovalRule
from cases.models import Case, CaseWatcher, TimeEntry
from common.models import Profile
from common.module_deletes import DELETE_GUARDS, ENTRY_POINTS, Guard, cascades
from common.modules import model_module
from common.testing import set_modules_off
from invoices.models import Estimate, Invoice, RecurringInvoice
from macros.models import Macro
from opportunity.models import Opportunity
from tasks.models import Board, BoardMember

pytestmark = pytest.mark.django_db

LEAVE_THE_CHILD = (
    models.SET_NULL,
    models.SET_DEFAULT,
    models.DO_NOTHING,
    models.DB_SET_NULL,
    models.DB_SET_DEFAULT,
)


def _reaches_child(on_delete):
    """Fails closed: any handler but these, one added later included, deletes
    the child or blocks the delete, so it must be guarded."""
    if on_delete in LEAVE_THE_CHILD:
        return False
    deconstruct = getattr(on_delete, "deconstruct", None)
    return not (deconstruct and deconstruct()[0] == "django.db.models.SET")


def _cross_module_links():
    """Every (parent, child, field) whose delete reaches another module's rows."""
    links = set()
    for model in apps.get_models():
        child = model._meta.label
        child_module = model_module(child)
        if child_module is None:
            continue
        for field in model._meta.get_fields():
            if not isinstance(field, models.ForeignKey) or not field.concrete:
                continue
            if not _reaches_child(field.remote_field.on_delete):
                continue
            parent = field.related_model._meta.label
            if parent == "common.Org" or model_module(parent) == child_module:
                continue
            links.add((parent, child, field.name))
    return links


def _guarded(guards):
    return {(parent, g.child, g.field) for parent, row in guards.items() for g in row}


def test_every_cross_module_cascade_is_guarded():
    """Exactly the links the walk finds are guarded: a new one fails here by
    name, and so does a guard that names no such link."""
    links, guarded = _cross_module_links(), _guarded(DELETE_GUARDS)
    assert sorted(links - guarded) == []
    assert sorted(guarded - links) == []


def test_the_walk_can_fail():
    """Dropping a guard surfaces its link, so the walk above is not vacuous."""
    dropped = ("cases.Case", "cases.TimeEntry", "case")
    assert dropped in _cross_module_links()
    trimmed = {
        parent: tuple(g for g in row if (parent, g.child, g.field) != dropped)
        for parent, row in DELETE_GUARDS.items()
    }
    assert sorted(_cross_module_links() - _guarded(trimmed)) == [dropped]


def test_only_the_handlers_that_leave_the_child_are_skipped():
    for handler in (models.CASCADE, models.DB_CASCADE, models.PROTECT, models.RESTRICT):
        assert _reaches_child(handler), handler
    for handler in (*LEAVE_THE_CHILD, models.SET(None), models.SET(lambda: None)):
        assert not _reaches_child(handler), handler


def test_a_database_cascade_is_followed_like_a_django_one(monkeypatch):
    guard = DELETE_GUARDS["cases.Case"][0]
    remote = TimeEntry._meta.get_field("case").remote_field
    for handler, expected in (
        (models.CASCADE, True),
        (models.DB_CASCADE, True),
        (models.PROTECT, False),
        (models.SET_NULL, False),
    ):
        monkeypatch.setattr(remote, "on_delete", handler)
        assert cascades(guard) is expected, handler


def _unreached(guards, entry_points):
    """Guarded models no delete consults: neither an entry point nor reached
    from one through cascading guards, as `delete_refusal` recurses."""
    reached, todo = set(entry_points), list(entry_points)
    while todo:
        for guard in guards.get(todo.pop(), ()):
            if guard.child not in reached and cascades(guard):
                reached.add(guard.child)
                todo.append(guard.child)
    return sorted(set(guards) - reached)


def test_every_guarded_model_is_checked_when_deleted():
    assert _unreached(DELETE_GUARDS, ENTRY_POINTS) == [], (
        "Nothing consults these models' guards. Call delete_refusal from their "
        "delete path and add them to ENTRY_POINTS, or extend the recursion so a "
        "guarded delete cascades into them."
    )


def test_the_entry_point_check_can_fail():
    stray = {**DELETE_GUARDS, "contacts.Contact": (Guard("x.Y", "contact", "y", "ys"),)}
    assert _unreached(stray, ENTRY_POINTS) == ["contacts.Contact"]
    assert _unreached(DELETE_GUARDS, ENTRY_POINTS - {"common.Profile"}) == [
        "common.Profile"
    ]
    # An account's delete cascades into its tickets, so they are reached anyway.
    assert _unreached(DELETE_GUARDS, ENTRY_POINTS - {"cases.Case"}) == []


# Accounts


@pytest.fixture(params=["admin", "member creator"])
def deleter(request, admin_user, regular_user, admin_client, user_client):
    """Who deletes the account or ticket: an admin, or a member who made it."""
    if request.param == "admin":
        return admin_client, admin_user
    return user_client, regular_user


def _account(org, creator, name="Acme"):
    return Account.objects.create(name=name, org=org, created_by=creator)


def _ticket(org, creator=None, account=None, name="Ticket"):
    return Case.objects.create(
        name=name,
        status="New",
        priority="Normal",
        org=org,
        account=account,
        created_by=creator,
    )


def _entry(case, profile, minutes_ago=60):
    started = timezone.now() - timedelta(minutes=minutes_ago)
    return TimeEntry.objects.create(
        org=case.org,
        case=case,
        profile=profile,
        started_at=started,
        ended_at=started + timedelta(minutes=30),
    )


def test_an_account_with_tickets_is_kept_while_tickets_is_off(org_a, deleter):
    client, creator = deleter
    account = _account(org_a, creator)
    for n in range(3):
        _ticket(org_a, account=account, name=f"T{n}")
    set_modules_off(org_a, "tickets")

    response = client.delete(f"/api/accounts/{account.id}/")

    assert response.status_code == 409
    assert response.json() == {
        "error": True,
        "errors": (
            "This account has 3 tickets. Tickets is switched off, so switch it "
            "on to review them before deleting."
        ),
        "code": "module_off",
        "module": "tickets",
    }
    assert Account.objects.filter(id=account.id).exists()
    assert Case._base_manager.filter(account=account).count() == 3

    set_modules_off(org_a)
    response = client.delete(f"/api/accounts/{account.id}/")

    assert response.status_code == 200
    assert not Account.objects.filter(id=account.id).exists()
    assert not Case._base_manager.filter(org=org_a).exists()


def test_an_account_with_one_deal_is_kept_while_deals_is_off(org_a, deleter):
    client, creator = deleter
    account = _account(org_a, creator)
    Opportunity.objects.create(name="D", org=org_a, account=account, stage="PROPOSAL")
    set_modules_off(org_a, "deals")

    response = client.delete(f"/api/accounts/{account.id}/")

    assert response.status_code == 409
    assert response.json()["errors"] == (
        "This account has 1 deal. Pipeline is switched off, so switch it on to "
        "review it before deleting."
    )
    assert response.json()["module"] == "deals"
    assert Opportunity._base_manager.filter(account=account).count() == 1

    set_modules_off(org_a)
    response = client.delete(f"/api/accounts/{account.id}/")

    assert response.status_code == 200
    assert not Opportunity._base_manager.filter(org=org_a).exists()


def test_the_first_guard_in_order_is_the_one_named(admin_client, admin_user, org_a):
    account = _account(org_a, admin_user)
    Opportunity.objects.create(name="D", org=org_a, account=account, stage="PROPOSAL")
    _ticket(org_a, account=account)
    set_modules_off(org_a, "deals", "tickets")

    response = admin_client.delete(f"/api/accounts/{account.id}/")

    assert response.status_code == 409
    assert response.json()["module"] == "tickets"


@pytest.mark.parametrize(
    ("model", "extra", "noun"),
    [
        (Invoice, {"invoice_number": "INV-1"}, "invoice"),
        (Estimate, {"estimate_number": "EST-1"}, "estimate"),
        (RecurringInvoice, {"title": "Monthly retainer"}, "recurring invoice"),
    ],
    ids=["invoice", "estimate", "recurring-invoice"],
)
def test_an_invoiced_account_names_the_off_module(org_a, deleter, model, extra, noun):
    client, creator = deleter
    account = _account(org_a, creator)
    model.objects.create(org=org_a, account=account, **extra)
    set_modules_off(org_a, "invoices")

    response = client.delete(f"/api/accounts/{account.id}/")

    assert response.status_code == 409
    assert response.json() == {
        "error": True,
        "errors": (
            f"This account has 1 {noun}. Invoices is switched off, so switch it "
            "on to review it before deleting."
        ),
        "code": "module_off",
        "module": "invoices",
    }

    set_modules_off(org_a)
    response = client.delete(f"/api/accounts/{account.id}/")

    assert response.status_code == 409
    assert response.json() == {
        "error": True,
        "errors": (
            "This account can't be deleted while it still has projects, "
            "invoices, estimates or recurring invoices linked to it."
        ),
    }
    assert Account.objects.filter(id=account.id).exists()


def test_an_account_whose_tickets_hold_time_entries_is_kept_while_the_timesheet_is_off(
    admin_client, admin_user, admin_profile, org_a
):
    """The account's tickets would go with it, and their time entries with them."""
    account = _account(org_a, admin_user)
    ticket = _ticket(org_a, account=account)
    _entry(ticket, admin_profile, minutes_ago=120)
    _entry(ticket, admin_profile)
    set_modules_off(org_a, "timesheet")

    response = admin_client.delete(f"/api/accounts/{account.id}/")

    assert response.status_code == 409
    assert response.json()["errors"] == (
        "This account has 2 time entries. Timesheet is switched off, so switch "
        "it on to review them before deleting."
    )
    assert response.json()["module"] == "timesheet"
    assert TimeEntry.objects.filter(case=ticket).count() == 2

    set_modules_off(org_a)
    response = admin_client.delete(f"/api/accounts/{account.id}/")

    assert response.status_code == 200
    assert not TimeEntry.objects.filter(org=org_a).exists()


# Tickets


def test_a_ticket_with_time_entries_is_kept_while_the_timesheet_is_off(
    org_a, deleter, admin_profile
):
    client, creator = deleter
    ticket = _ticket(org_a, creator=creator)
    _entry(ticket, admin_profile, minutes_ago=120)
    _entry(ticket, admin_profile)
    set_modules_off(org_a, "timesheet")

    response = client.delete(f"/api/cases/{ticket.id}/")

    assert response.status_code == 409
    assert response.json() == {
        "error": True,
        "errors": (
            "This ticket has 2 time entries. Timesheet is switched off, so switch "
            "it on to review them before deleting."
        ),
        "code": "module_off",
        "module": "timesheet",
    }
    assert Case._base_manager.filter(id=ticket.id).exists()
    assert TimeEntry.objects.filter(case=ticket).count() == 2

    set_modules_off(org_a)
    response = client.delete(f"/api/cases/{ticket.id}/")

    assert response.status_code == 200
    assert not Case._base_manager.filter(id=ticket.id).exists()
    assert not TimeEntry.objects.filter(org=org_a).exists()


def test_pin_bulk_ticket_delete_keeps_time_entries(admin_client, admin_profile, org_a):
    """Pin: the bulk delete is a soft delete, so it reaches no time entry."""
    ticket = _ticket(org_a)
    _entry(ticket, admin_profile)
    set_modules_off(org_a, "timesheet")

    response = admin_client.post(
        "/api/cases/bulk/delete/",
        {"ids": [str(ticket.id)]},
        content_type="application/json",
    )

    assert response.status_code == 200
    assert response.json()["deleted"] == 1
    ticket.refresh_from_db()
    assert ticket.is_active is False
    assert TimeEntry.objects.filter(case=ticket).count() == 1


# People


@pytest.fixture
def send_email():
    with patch("common.views.user_views.send_email_user_delete") as mocked:
        yield mocked


def _person_url(user):
    return f"/api/user/{user.id}/"


def test_a_person_with_off_module_records_is_kept(
    admin_client, admin_profile, regular_user, user_profile, org_a, send_email
):
    board = Board.objects.create(org=org_a, name="Launch", owner=user_profile)
    set_modules_off(org_a, "tasks")

    response = admin_client.delete(_person_url(regular_user))

    assert response.status_code == 409
    assert response.json() == {
        "error": True,
        "errors": (
            "This person has 1 board. Tasks is switched off, so switch it on to "
            "review it before deleting."
        ),
        "code": "module_off",
        "module": "tasks",
    }
    assert Profile.objects.filter(id=user_profile.id).exists()
    assert Board.objects.filter(id=board.id).exists()
    send_email.delay.assert_not_called()

    board.delete()
    _entry(_ticket(org_a), user_profile)
    set_modules_off(org_a, "timesheet")

    response = admin_client.delete(_person_url(regular_user))

    assert response.status_code == 409
    assert response.json()["errors"] == (
        "This person has 1 time entry. Timesheet is switched off, so switch it "
        "on to review it before deleting."
    )
    assert response.json()["module"] == "timesheet"
    send_email.delay.assert_not_called()


def test_a_person_with_time_entries_is_not_hidden_by_tickets_alone(
    admin_client, regular_user, user_profile, org_a, send_email
):
    """Timesheet needs no other module since projects, so with Tickets off the
    time is still in sight and the delete gets the ordinary refusal."""
    _entry(_ticket(org_a), user_profile)
    set_modules_off(org_a, "tickets")

    response = admin_client.delete(_person_url(regular_user))

    assert response.status_code == 409
    assert "module" not in response.json()
    assert response.json()["errors"].startswith("This user can't be deleted")
    send_email.delay.assert_not_called()


def _approval_requested(org, profile, admin_profile):
    rule = ApprovalRule.objects.create(name="Sign-off", org=org)
    Approval.objects.create(org=org, case=_ticket(org), rule=rule, requested_by=profile)


def _approval_assigned(org, profile, admin_profile):
    rule = ApprovalRule.objects.create(name="Sign-off", org=org)
    Approval.objects.create(
        org=org,
        case=_ticket(org),
        rule=rule,
        requested_by=admin_profile,
        approver=profile,
    )


def _watching(org, profile, admin_profile):
    CaseWatcher.objects.create(org=org, case=_ticket(org), profile=profile)


def _saved_reply(org, profile, admin_profile):
    Macro.objects.create(
        org=org, title="Thanks", scope=Macro.SCOPE_PERSONAL, owner=profile
    )


def _board_member(org, profile, admin_profile):
    board = Board.objects.create(org=org, name="Shared", owner=admin_profile)
    BoardMember.objects.create(org=org, board=board, profile=profile)


@pytest.mark.parametrize(
    ("make", "module", "noun"),
    [
        (_approval_requested, "tickets", "approval"),
        (_approval_assigned, "tickets", "approval"),
        (_watching, "tickets", "watched ticket"),
        (_saved_reply, "tickets", "saved reply"),
        (_board_member, "tasks", "board membership"),
    ],
    ids=["requested", "approver", "watcher", "saved-reply", "board-member"],
)
def test_each_record_a_person_holds_names_its_module(
    admin_client,
    admin_profile,
    regular_user,
    user_profile,
    org_a,
    send_email,
    make,
    module,
    noun,
):
    make(org_a, user_profile, admin_profile)
    set_modules_off(org_a, module)

    response = admin_client.delete(_person_url(regular_user))

    assert response.status_code == 409
    label = {"tickets": "Tickets", "tasks": "Tasks"}[module]
    assert response.json()["errors"] == (
        f"This person has 1 {noun}. {label} is switched off, so switch it on to "
        "review it before deleting."
    )
    assert response.json()["module"] == module
    assert Profile.objects.filter(id=user_profile.id).exists()
    send_email.delay.assert_not_called()


def test_pin_with_the_modules_on_a_person_is_deleted_as_before(
    admin_client, regular_user, user_profile, org_a, send_email
):
    board = Board.objects.create(org=org_a, name="Launch", owner=user_profile)
    Macro.objects.create(
        org=org_a, title="Thanks", scope=Macro.SCOPE_PERSONAL, owner=user_profile
    )

    response = admin_client.delete(_person_url(regular_user))

    assert response.status_code == 200
    assert not Profile.objects.filter(id=user_profile.id).exists()
    assert not Board.objects.filter(id=board.id).exists()
    send_email.delay.assert_called_once()


def test_pin_with_the_timesheet_on_time_entries_keep_the_existing_refusal(
    admin_client, regular_user, user_profile, org_a, send_email
):
    _entry(_ticket(org_a), user_profile)

    response = admin_client.delete(_person_url(regular_user))

    assert response.status_code == 409
    assert response.json() == {
        "error": True,
        "errors": (
            "This user can't be deleted while they still have time entries or "
            "approvals linked to them."
        ),
    }
    send_email.delay.assert_not_called()


# Permission first


def test_permission_comes_before_the_module_refusal(
    admin_user, admin_profile, user_client, user_profile, org_a, send_email
):
    """Pin: callers who may not delete get the 403 they got before, not the 409."""
    account = _account(org_a, admin_user)
    account.assigned_to.add(user_profile)
    _ticket(org_a, account=account)
    ticket = _ticket(org_a, creator=admin_user)
    ticket.assigned_to.add(user_profile)
    _entry(ticket, admin_profile)
    Board.objects.create(org=org_a, name="Launch", owner=admin_profile)
    set_modules_off(org_a, "tickets", "timesheet", "tasks")

    assert user_client.delete(f"/api/accounts/{account.id}/").status_code == 403

    set_modules_off(org_a, "timesheet", "tasks")
    assert user_client.delete(f"/api/cases/{ticket.id}/").status_code == 403

    response = user_client.delete(_person_url(admin_user))
    assert response.status_code == 403
    assert Account.objects.filter(id=account.id).exists()
    assert Profile.objects.filter(id=admin_profile.id).exists()
    send_email.delay.assert_not_called()


# Cost


def _delete_url(target, org, admin_user, admin_profile, regular_user, user_profile):
    """One record of ``target`` holding a row behind each guard it can carry
    without being protected, and the URL that deletes it."""
    if target == "account":
        account = _account(org, admin_user)
        _entry(_ticket(org, account=account), admin_profile)
        Opportunity.objects.create(name="D", org=org, account=account, stage="PROPOSAL")
        return f"/api/accounts/{account.id}/"
    if target == "ticket":
        ticket = _ticket(org)
        _entry(ticket, admin_profile)
        return f"/api/cases/{ticket.id}/"
    Board.objects.create(org=org, name="Launch", owner=user_profile)
    Macro.objects.create(
        org=org, title="Thanks", scope=Macro.SCOPE_PERSONAL, owner=user_profile
    )
    return _person_url(regular_user)


@pytest.mark.parametrize(
    ("target", "sqlite", "postgresql"),
    [("account", 66, 66), ("ticket", 23, 23), ("person", 65, 71)],
)
def test_with_every_module_on_the_guard_adds_no_query(
    admin_client,
    admin_user,
    admin_profile,
    regular_user,
    user_profile,
    org_a,
    send_email,
    django_assert_num_queries,
    target,
    sqlite,
    postgresql,
):
    """Each count is the delete's own, measured with `delete_refusal` patched
    to return None. A person's delete runs more queries on PostgreSQL."""
    url = _delete_url(
        target, org_a, admin_user, admin_profile, regular_user, user_profile
    )
    expected = {"sqlite": sqlite, "postgresql": postgresql}[connection.vendor]

    with django_assert_num_queries(expected):
        response = admin_client.delete(url)

    assert response.status_code == 200
