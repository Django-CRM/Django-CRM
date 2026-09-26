"""D40: every list, search, picker and count answers with the detail read rule.

Several paths carried their own copy of "who may see this record" and had
drifted from the detail view's, so a user could open a record their list,
search or dashboard hid, or be shown one that answered 403:

* global search scoped accounts without the superuser clause;
* the account list restated the account rule inline (it agreed; it now calls
  ``visible_accounts_qs`` so it cannot drift);
* the task list's account picker had no superuser clause;
* the dashboard counted contacts without account assignment, and counted
  tasks for a superuser that the task detail view refuses them;
* the Today queue showed a superuser tickets and tasks they cannot open (it
  also leaves out tickets a watcher can open, on purpose since D44: its ticket
  action is a reply, which only the write rule allows);
* the ticket and task boards admitted superusers the detail views refuse,
  the ticket board left watchers out, and the task board repeated a card
  once per assignee on a task its caller created.

Rules differ on purpose. Accounts, contacts, deals, leads and invoices admit a
Django superuser; tickets and tasks do not, because their detail views do not.
Each test here pins one path against the detail answer for the same caller.
"""

import pytest
from django.utils import timezone
from rest_framework import status

from accounts.models import Account
from cases.models import Case, CaseWatcher
from conftest import rls_org
from contacts.models import Contact
from tasks.models import Task

pytestmark = pytest.mark.django_db


def _stamp(model, obj, user):
    """Set ``created_by`` without the thread-local request user restamping it."""
    model.objects.filter(pk=obj.pk).update(created_by=user)
    obj.refresh_from_db()
    return obj


@pytest.fixture
def superuser(regular_user):
    """The ``user_client`` caller, still a plain USER profile, made superuser."""
    regular_user.is_superuser = True
    regular_user.save(update_fields=["is_superuser"])
    return regular_user


@pytest.fixture
def others_account(org_a, admin_user):
    return _stamp(
        Account, Account.objects.create(name="Zyx Others", org=org_a), admin_user
    )


@pytest.fixture
def foreign_account(org_b, user_b):
    with rls_org(org_b):
        return _stamp(
            Account, Account.objects.create(name="Zyx Foreign", org=org_b), user_b
        )


@pytest.fixture
def others_case(org_a, admin_user):
    case = Case.objects.create(
        name="Zyx ticket", status="New", priority="Normal", org=org_a
    )
    return _stamp(Case, case, admin_user)


@pytest.fixture
def foreign_case(org_b, user_b):
    with rls_org(org_b):
        case = Case.objects.create(
            name="Zyx foreign ticket", status="New", priority="Normal", org=org_b
        )
        return _stamp(Case, case, user_b)


def _watch(case, profile):
    CaseWatcher.objects.create(case=case, profile=profile, org=case.org)


def _task(org, user, title, **extra):
    task = Task.objects.create(
        title=title, status="New", priority="Low", org=org, **extra
    )
    return _stamp(Task, task, user)


@pytest.fixture
def others_task(org_a, admin_user):
    return _task(org_a, admin_user, "Zyx task", due_date=timezone.localdate())


@pytest.fixture
def foreign_task(org_b, user_b):
    with rls_org(org_b):
        return _task(org_b, user_b, "Zyx foreign task", due_date=timezone.localdate())


# ── accounts: search, list, task picker ───────────────────────────────────


def _search_ids(client, kind):
    response = client.get("/api/search/", {"q": "Zyx"})
    assert response.status_code == status.HTTP_200_OK
    return {r["id"] for r in response.data["results"] if r["type"] == kind}


def _account_list_ids(client):
    response = client.get("/api/accounts/")
    assert response.status_code == status.HTTP_200_OK
    return {str(row["id"]) for row in response.data["active_accounts"]["open_accounts"]}


def _task_picker_account_ids(client):
    response = client.get("/api/tasks/")
    assert response.status_code == status.HTTP_200_OK, response.content
    return {str(row["id"]) for row in response.data["accounts_list"]}


ACCOUNT_PATHS = [_search_ids, _account_list_ids, _task_picker_account_ids]


def _account_ids(path, client):
    if path is _search_ids:
        return _search_ids(client, "account")
    return path(client)


class TestAccountsFollowTheDetailRule:
    @pytest.mark.parametrize("path", ACCOUNT_PATHS)
    def test_plain_member_is_not_shown_it(self, path, user_client, others_account):
        response = user_client.get(f"/api/accounts/{others_account.pk}/")
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert str(others_account.id) not in _account_ids(path, user_client)

    @pytest.mark.parametrize("path", ACCOUNT_PATHS)
    def test_superuser_is_shown_it(self, path, superuser, user_client, others_account):
        response = user_client.get(f"/api/accounts/{others_account.pk}/")
        assert response.status_code == status.HTTP_200_OK
        assert str(others_account.id) in _account_ids(path, user_client)

    @pytest.mark.parametrize("path", ACCOUNT_PATHS)
    def test_superuser_never_sees_another_orgs(
        self, path, superuser, user_client, others_account, foreign_account
    ):
        ids = _account_ids(path, user_client)
        assert str(others_account.id) in ids
        assert str(foreign_account.id) not in ids


# ── dashboard counts ──────────────────────────────────────────────────────


def _home(client):
    response = client.get("/api/dashboard/")
    assert response.status_code == status.HTTP_200_OK
    return response.data


class TestDashboardContactCount:
    @pytest.fixture
    def others_contact(self, org_a, admin_user):
        return _stamp(
            Contact,
            Contact.objects.create(first_name="Zyx", last_name="C", org=org_a),
            admin_user,
        )

    @pytest.fixture
    def foreign_contact(self, org_b, user_b):
        with rls_org(org_b):
            return _stamp(
                Contact,
                Contact.objects.create(first_name="Zyx", last_name="F", org=org_b),
                user_b,
            )

    def test_plain_member_does_not_count_it(self, user_client, others_contact):
        assert _home(user_client)["contacts_count"] == 0

    def test_member_assigned_to_its_account_counts_it(
        self, org_a, user_profile, user_client, others_contact
    ):
        account = Account.objects.create(name="Via account", org=org_a)
        account.assigned_to.add(user_profile)
        account.contacts.add(others_contact)
        # The detail view and the list already admit this member.
        response = user_client.get(f"/api/contacts/{others_contact.pk}/")
        assert response.status_code == status.HTTP_200_OK
        assert _home(user_client)["contacts_count"] == 1

    def test_another_orgs_contact_is_never_counted(
        self, superuser, user_client, others_contact, foreign_contact
    ):
        assert _home(user_client)["contacts_count"] == 1


class TestDashboardAccountCount:
    def test_superuser_counts_it_and_a_member_does_not(
        self, regular_user, user_client, others_account, foreign_account
    ):
        assert _home(user_client)["accounts_count"] == 0
        regular_user.is_superuser = True
        regular_user.save(update_fields=["is_superuser"])
        assert _home(user_client)["accounts_count"] == 1


class TestDashboardTasks:
    """Tasks have no superuser clause, so the dashboard must not invent one."""

    def test_superuser_does_not_get_a_task_they_cannot_open(
        self, superuser, user_client, others_task
    ):
        response = user_client.get(f"/api/tasks/{others_task.pk}/")
        assert response.status_code == status.HTTP_403_FORBIDDEN
        data = _home(user_client)
        assert str(others_task.id) not in {str(t["id"]) for t in data["tasks"]}
        assert data["urgent_counts"]["tasks_due_today"] == 0

    def test_assignee_gets_it(self, user_profile, user_client, others_task):
        others_task.assigned_to.add(user_profile)
        data = _home(user_client)
        assert str(others_task.id) in {str(t["id"]) for t in data["tasks"]}
        assert data["urgent_counts"]["tasks_due_today"] == 1

    def test_another_orgs_task_is_never_counted(
        self, user_profile, user_client, others_task, foreign_task
    ):
        others_task.assigned_to.add(user_profile)
        data = _home(user_client)
        assert str(foreign_task.id) not in {str(t["id"]) for t in data["tasks"]}
        assert data["urgent_counts"]["tasks_due_today"] == 1


# ── Today queue ───────────────────────────────────────────────────────────


def _queue_ids(client):
    response = client.get("/api/dashboard/today/")
    assert response.status_code == status.HTTP_200_OK
    return {row["id"] for row in response.data["queue"]}


class TestTodayFollowsTheDetailRule:
    def test_watcher_can_open_the_ticket_but_is_not_asked_to_reply(
        self, user_profile, user_client, others_case
    ):
        """D44: every queue row carries an action, and a ticket's is "Reply",
        which the write rule refuses a watcher. The read rule still lets them
        open it; the queue just does not offer them a button that 403s. The
        full pair lives in ``test_today_actionable.py``."""
        _watch(others_case, user_profile)
        response = user_client.get(f"/api/cases/{others_case.pk}/")
        assert response.status_code == status.HTTP_200_OK
        assert f"case-{others_case.id}" not in _queue_ids(user_client)

    def test_plain_member_does_not_see_it(self, user_client, others_case):
        assert f"case-{others_case.id}" not in _queue_ids(user_client)

    def test_superuser_does_not_get_rows_they_cannot_open(
        self, superuser, user_client, others_case, others_task
    ):
        assert (
            user_client.get(f"/api/cases/{others_case.pk}/").status_code
            == status.HTTP_403_FORBIDDEN
        )
        ids = _queue_ids(user_client)
        assert f"case-{others_case.id}" not in ids
        assert f"task-{others_task.id}" not in ids

    def test_another_orgs_rows_never_appear(
        self, user_profile, user_client, others_case, foreign_case, foreign_task
    ):
        others_case.assigned_to.add(user_profile)
        ids = _queue_ids(user_client)
        assert f"case-{others_case.id}" in ids
        assert f"case-{foreign_case.id}" not in ids
        assert f"task-{foreign_task.id}" not in ids


# ── boards ────────────────────────────────────────────────────────────────


def _board_ids(client, url, key):
    response = client.get(url)
    assert response.status_code == status.HTTP_200_OK, response.content
    return [str(card["id"]) for col in response.data["columns"] for card in col[key]]


def _case_board(client):
    return _board_ids(client, "/api/cases/kanban/", "cases")


def _task_board(client):
    return _board_ids(client, "/api/tasks/kanban/", "tasks")


class TestCaseBoard:
    def test_watcher_sees_it(self, user_profile, user_client, others_case):
        _watch(others_case, user_profile)
        assert str(others_case.id) in _case_board(user_client)

    def test_plain_member_does_not(self, user_client, others_case):
        assert str(others_case.id) not in _case_board(user_client)

    def test_superuser_does_not_see_a_ticket_they_cannot_open(
        self, superuser, user_client, others_case
    ):
        assert str(others_case.id) not in _case_board(user_client)

    def test_another_orgs_ticket_never_appears(
        self, admin_client, others_case, foreign_case
    ):
        ids = _case_board(admin_client)
        assert str(others_case.id) in ids
        assert str(foreign_case.id) not in ids


class TestTaskBoard:
    def test_creator_with_two_assignees_sees_one_card(
        self, regular_user, user_profile, admin_profile, user_client, others_task
    ):
        _stamp(Task, others_task, regular_user)
        others_task.assigned_to.add(user_profile, admin_profile)
        assert _task_board(user_client).count(str(others_task.id)) == 1

    def test_assignee_sees_it_once(
        self, user_profile, admin_profile, user_client, others_task
    ):
        others_task.assigned_to.add(user_profile, admin_profile)
        assert _task_board(user_client).count(str(others_task.id)) == 1

    def test_plain_member_does_not(self, user_client, others_task):
        assert str(others_task.id) not in _task_board(user_client)

    def test_superuser_does_not_see_a_task_they_cannot_open(
        self, superuser, user_client, others_task
    ):
        assert str(others_task.id) not in _task_board(user_client)

    def test_another_orgs_task_never_appears(
        self, admin_client, others_task, foreign_task
    ):
        ids = _task_board(admin_client)
        assert str(others_task.id) in ids
        assert str(foreign_task.id) not in ids
