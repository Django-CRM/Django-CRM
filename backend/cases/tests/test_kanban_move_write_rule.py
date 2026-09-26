"""The board move takes the ticket's write rule, exactly (D42).

`CaseMoveView.patch` rewrites a ticket's status, stage and kanban order. It used
to gate on ``is_org_admin(...) or request.user.is_superuser`` plus creator or
assignee, a superuser clause of its own that `CaseDetailView.patch` did not
have. The move now asks `assert_case_write_access`, and a superuser is an org
admin through `is_org_admin` on both paths alike (owner decision, 1.11.0). A
ticket the caller may not even open answers 404, as its detail page does.
"""

import pytest

from cases.models import Case, CaseWatcher
from conftest import rls_org


def _move(client, case, status="Assigned"):
    return client.patch(
        f"/api/cases/{case.id}/move/", {"status": status}, format="json"
    )


@pytest.fixture
def superuser(regular_user):
    """The `user_client` caller, still a plain USER profile, made superuser."""
    regular_user.is_superuser = True
    regular_user.save(update_fields=["is_superuser"])
    return regular_user


@pytest.fixture
def others_case(admin_user, org_a):
    """A ticket the `user_client` caller neither created nor was handed."""
    return Case.objects.create(
        name="Someone else's ticket",
        status="New",
        priority="Normal",
        org=org_a,
        created_by=admin_user,
    )


def _assert_unchanged(case):
    case.refresh_from_db()
    assert case.status == "New"


class TestSuperuserPlainMember:
    def test_admin_on_the_board_as_on_the_detail(
        self, superuser, user_client, others_case
    ):
        """A superuser on a USER profile may move a ticket they neither
        created nor hold, and the detail PATCH agrees."""
        response = _move(user_client, others_case)
        assert response.status_code == 200, response.content
        others_case.refresh_from_db()
        assert others_case.status == "Assigned"
        assert (
            user_client.patch(
                f"/api/cases/{others_case.id}/", {"status": "New"}, format="json"
            ).status_code
            == 200
        )

    def test_allowed_on_a_ticket_they_created(
        self, superuser, user_client, regular_user, org_a
    ):
        case = Case.objects.create(
            name="Mine",
            status="New",
            priority="Normal",
            org=org_a,
            created_by=regular_user,
        )
        response = _move(user_client, case)
        assert response.status_code == 200, response.content
        case.refresh_from_db()
        assert case.status == "Assigned"

    def test_allowed_on_a_ticket_they_are_assigned(
        self, superuser, user_client, user_profile, others_case
    ):
        others_case.assigned_to.add(user_profile)
        response = _move(user_client, others_case)
        assert response.status_code == 200, response.content
        others_case.refresh_from_db()
        assert others_case.status == "Assigned"


class TestOrdinaryRoles:
    def test_admin_may_move_any_ticket(self, admin_client, regular_user, org_a):
        case = Case.objects.create(
            name="A member's ticket",
            status="New",
            priority="Normal",
            org=org_a,
            created_by=regular_user,
        )
        response = _move(admin_client, case)
        assert response.status_code == 200, response.content
        case.refresh_from_db()
        assert case.status == "Assigned"

    def test_member_with_write_access_may_move(
        self, user_client, user_profile, others_case
    ):
        others_case.assigned_to.add(user_profile)
        response = _move(user_client, others_case)
        assert response.status_code == 200, response.content
        others_case.refresh_from_db()
        assert others_case.status == "Assigned"

    def test_member_who_cannot_open_it_gets_a_missing_tickets_answer(
        self, user_client, others_case
    ):
        response = _move(user_client, others_case)
        missing = _move(user_client, Case(id="00000000-0000-0000-0000-000000000000"))
        assert response.status_code == 404, response.content
        assert (response.status_code, response.json()) == (
            missing.status_code,
            missing.json(),
        )
        _assert_unchanged(others_case)

    def test_watcher_reads_but_may_not_move(
        self, user_client, user_profile, others_case, org_a
    ):
        """Watching is read access, not write access, on the board as elsewhere."""
        CaseWatcher.objects.create(case=others_case, profile=user_profile, org=org_a)
        assert user_client.get(f"/api/cases/{others_case.id}/").status_code == 200
        response = _move(user_client, others_case)
        assert response.status_code == 403, response.content
        _assert_unchanged(others_case)


@pytest.mark.parametrize("caller", ["member", "admin", "superuser"])
def test_another_orgs_ticket_is_404_for_everyone(
    caller, request, user_client, admin_client, user_b, org_b
):
    if caller == "superuser":
        request.getfixturevalue("superuser")
    client = admin_client if caller == "admin" else user_client
    with rls_org(org_b):
        foreign = Case.objects.create(
            name="Foreign",
            status="New",
            priority="Normal",
            org=org_b,
            created_by=user_b,
        )
    response = _move(client, foreign)
    assert response.status_code == 404, response.content
    with rls_org(org_b):
        _assert_unchanged(foreign)
