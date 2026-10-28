"""Team membership reaches the team's records.

`update_team_users` assigns every active member of a team to the team's
accounts, contacts, leads, deals, tickets, documents (as shared people), tasks
and invoices. `remove_users` takes the people who left, active or not, off the
same records, except a record where one of its teams (this one included) still
includes them. A team edit works out who left by comparing member ids before
and after the save, and leaves the members alone when it names none.
"""

import pytest
from django.db import connection
from django.urls import resolve
from rest_framework.permissions import IsAuthenticated
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import Account
from cases.models import Case
from common.models import Document, Profile, Teams, User
from common.permissions import HasOrgContext
from common.tasks import remove_users, update_team_users
from common.testing import clear_rls_context, restore_rls_context, rls_org
from contacts.models import Contact
from invoices.models import Invoice
from leads.models import Lead
from opportunity.models import Opportunity
from tasks.models import Task

KINDS = {"account", "contact", "lead", "deal", "ticket", "document", "task", "invoice"}


def _member(org, email, role="USER"):
    user = User.objects.create_user(email=email, password="x")
    return Profile.objects.create(user=user, org=org, role=role, is_active=True)


def _team(org, name, *members):
    team = Teams.objects.create(name=name, org=org)
    team.users.add(*members)
    return team


def _records(org, team):
    """One record of each of the eight kinds, all on ``team``."""
    account = Account.objects.create(name=f"Acme {team.name}", org=org)
    records = {
        "account": account,
        "contact": Contact.objects.create(first_name="Ada", last_name="L", org=org),
        "lead": Lead.objects.create(first_name="Ada", org=org),
        "deal": Opportunity.objects.create(name="Deal", org=org),
        "ticket": Case.objects.create(
            name="Ticket", status="New", priority="Normal", org=org
        ),
        "document": Document.objects.create(title="Doc", org=org),
        "task": Task.objects.create(
            title="Call", status="New", priority="Low", org=org
        ),
        "invoice": Invoice.objects.create(
            org=org, account=account, invoice_title="Work"
        ),
    }
    for record in records.values():
        record.teams.add(team)
    return records


def _people(record):
    people = record.shared_to if isinstance(record, Document) else record.assigned_to
    return set(people.values_list("id", flat=True))


def _kinds_with(records, profile):
    return {kind for kind, record in records.items() if profile.id in _people(record)}


@pytest.fixture
def asha(org_a):
    return _member(org_a, "asha@test.com")


@pytest.fixture
def ravi(org_a):
    return _member(org_a, "ravi@test.com")


# ------------------------------------------------------------------ the tasks


def test_adding_a_member_assigns_them_on_all_eight_record_kinds(org_a, asha):
    team = _team(org_a, "Field")
    records = _records(org_a, team)
    team.users.add(asha)

    update_team_users(str(team.id), str(org_a.id))

    assert _kinds_with(records, asha) == KINDS


def test_removing_a_member_unassigns_them_on_all_eight_record_kinds(org_a, asha):
    team = _team(org_a, "Field", asha)
    records = _records(org_a, team)
    update_team_users(str(team.id), str(org_a.id))
    assert _kinds_with(records, asha) == KINDS

    team.users.remove(asha)
    remove_users([str(asha.id)], str(team.id), str(org_a.id))

    assert _kinds_with(records, asha) == set()


def test_removal_keeps_a_person_another_team_on_the_record_includes(org_a, asha):
    field = _team(org_a, "Field", asha)
    desk = _team(org_a, "Desk", asha)
    records = _records(org_a, field)
    records["lead"].teams.add(desk)
    records["document"].teams.add(desk)
    update_team_users(str(field.id), str(org_a.id))

    field.users.remove(asha)
    remove_users([str(asha.id)], str(field.id), str(org_a.id))

    assert _kinds_with(records, asha) == {"lead", "document"}


def test_removal_leaves_the_other_members(org_a, asha, ravi):
    team = _team(org_a, "Field", asha, ravi)
    records = _records(org_a, team)
    update_team_users(str(team.id), str(org_a.id))

    team.users.remove(asha)
    remove_users([str(asha.id)], str(team.id), str(org_a.id))

    assert _kinds_with(records, ravi) == KINDS
    assert _kinds_with(records, asha) == set()


def test_adding_skips_inactive_members(org_a, asha, ravi):
    team = _team(org_a, "Field", asha, ravi)
    records = _records(org_a, team)
    Profile.objects.filter(pk=ravi.pk).update(is_active=False)

    update_team_users(str(team.id), str(org_a.id))

    assert _kinds_with(records, asha) == KINDS
    assert _kinds_with(records, ravi) == set()


def test_removing_takes_off_a_deactivated_member(org_a, asha):
    team = _team(org_a, "Field", asha)
    records = _records(org_a, team)
    update_team_users(str(team.id), str(org_a.id))
    Profile.objects.filter(pk=asha.pk).update(is_active=False)

    team.users.remove(asha)
    remove_users([str(asha.id)], str(team.id), str(org_a.id))

    assert _kinds_with(records, asha) == set()


def test_stale_removal_keeps_a_current_member(org_a, asha):
    """Removed and added back quickly: the first edit's removal can run after
    the second edit's add. It must not strip someone who is a member again."""
    team = _team(org_a, "Field", asha)
    records = _records(org_a, team)
    update_team_users(str(team.id), str(org_a.id))

    remove_users([str(asha.id)], str(team.id), str(org_a.id))

    assert _kinds_with(records, asha) == KINDS


def test_remove_ignores_malformed_and_other_org_ids(org_a, org_b, asha, profile_b):
    team = _team(org_a, "Field", asha)
    records = _records(org_a, team)
    update_team_users(str(team.id), str(org_a.id))
    # Another org's profile planted on this org's record: the id is well formed
    # but names nobody in the team's org, so it stays where it is.
    records["lead"].assigned_to.add(profile_b)
    team.users.remove(asha)

    remove_users(
        ["not-a-uuid", "", 42, None, ["nested"], str(profile_b.id), str(asha.id)],
        str(team.id),
        str(org_a.id),
    )

    assert _kinds_with(records, asha) == set()
    assert _kinds_with(records, profile_b) == {"lead"}


def test_remove_with_only_bad_ids_changes_nothing(org_a, asha):
    team = _team(org_a, "Field", asha)
    records = _records(org_a, team)
    update_team_users(str(team.id), str(org_a.id))

    remove_users(["nope", 7, None], str(team.id), str(org_a.id))

    assert _kinds_with(records, asha) == KINDS


def test_team_tasks_touch_only_their_org(org_a, org_b, asha, profile_b):
    """A team is looked up inside the org the caller names, and only that
    org's records are touched, even ones linked to the team by bad data."""
    mine = _team(org_a, "Field", asha)
    my_records = _records(org_a, mine)
    with rls_org(org_b):
        theirs = _team(org_b, "Theirs", profile_b)
        their_records = _records(org_b, theirs)
        stray = Lead.objects.create(first_name="Stray", org=org_b)
        stray.teams.add(mine)

    # The other org's team, named with this org's id: nothing happens.
    update_team_users(str(theirs.id), str(org_a.id))
    with rls_org(org_b):
        assert _kinds_with(their_records, profile_b) == set()

    update_team_users(str(mine.id), str(org_a.id))
    assert _kinds_with(my_records, asha) == KINDS
    with rls_org(org_b):
        assert _people(stray) == set()

    with rls_org(org_b):
        update_team_users(str(theirs.id), str(org_b.id))
        assert _kinds_with(their_records, profile_b) == KINDS
    remove_users([str(profile_b.id)], str(theirs.id), str(org_a.id))
    with rls_org(org_b):
        assert _kinds_with(their_records, profile_b) == KINDS


def test_team_tasks_without_an_org_do_nothing(org_a, asha):
    team = _team(org_a, "Field", asha)
    records = _records(org_a, team)

    update_team_users(str(team.id))
    assert _kinds_with(records, asha) == set()

    update_team_users(str(team.id), str(org_a.id))
    remove_users([str(asha.id)], str(team.id))
    assert _kinds_with(records, asha) == KINDS


# ------------------------------------------------------------------- the view


@pytest.fixture
def queued(monkeypatch):
    calls = {"update": [], "remove": []}
    monkeypatch.setattr(
        "common.views.team_views.update_team_users.delay",
        lambda *args: calls["update"].append(args),
    )
    monkeypatch.setattr(
        "common.views.team_views.remove_users.delay",
        lambda *args: calls["remove"].append(args),
    )
    return calls


def _put(client, team, members):
    return client.put(
        f"/api/teams/{team.id}/",
        {
            "name": team.name,
            "description": "Field team",
            "assign_users": [str(member.id) for member in members],
        },
        format="json",
    )


def test_view_passes_exactly_the_members_who_left(
    org_a, admin_client, asha, ravi, queued
):
    kept = asha
    leaving = ravi
    joining = _member(org_a, "joining@test.com")
    team = _team(org_a, "Field", kept, leaving)

    response = _put(admin_client, team, [kept, joining])

    assert response.status_code == 200, response.data
    assert set(team.users.values_list("id", flat=True)) == {kept.id, joining.id}
    assert queued["update"] == [(str(team.id), str(org_a.id))]
    assert queued["remove"] == [([str(leaving.id)], str(team.id), str(org_a.id))]


def test_view_queues_no_removal_when_nobody_left(
    org_a, admin_client, asha, ravi, queued
):
    team = _team(org_a, "Field", asha)

    response = _put(admin_client, team, [asha, ravi])

    assert response.status_code == 200, response.data
    assert queued["update"] == [(str(team.id), str(org_a.id))]
    assert queued["remove"] == []


def test_view_refuses_a_malformed_member_id_before_writing(
    org_a, admin_client, asha, queued
):
    team = _team(org_a, "Field", asha)

    response = admin_client.put(
        f"/api/teams/{team.id}/",
        {
            "name": "Renamed",
            "description": "Field team",
            "assign_users": ["not-a-uuid"],
        },
        format="json",
    )

    assert response.status_code == 400
    team.refresh_from_db()
    assert team.name == "Field"
    assert list(team.users.all()) == [asha]
    assert queued == {"update": [], "remove": []}


def test_put_without_members_keeps_the_team_and_its_records(
    org_a, admin_client, asha, queued
):
    team = _team(org_a, "Field", asha)
    records = _records(org_a, team)
    update_team_users(str(team.id), str(org_a.id))

    response = admin_client.put(
        f"/api/teams/{team.id}/",
        {"name": "Renamed", "description": "Field team"},
        format="json",
    )

    assert response.status_code == 200, response.data
    team.refresh_from_db()
    assert team.name == "Renamed"
    assert list(team.users.all()) == [asha]
    assert queued == {"update": [], "remove": []}
    assert _kinds_with(records, asha) == KINDS


def test_put_with_the_documented_key_changes_members(
    org_a, admin_client, asha, ravi, queued
):
    team = _team(org_a, "Field", asha)

    response = admin_client.put(
        f"/api/teams/{team.id}/",
        {"name": "Field", "description": "Field team", "users": [str(ravi.id)]},
        format="json",
    )

    assert response.status_code == 200, response.data
    assert list(team.users.all()) == [ravi]
    assert queued["update"] == [(str(team.id), str(org_a.id))]
    assert queued["remove"] == [([str(asha.id)], str(team.id), str(org_a.id))]


def test_put_with_an_empty_list_empties_the_team(org_a, admin_client, asha, queued):
    team = _team(org_a, "Field", asha)

    response = admin_client.put(
        f"/api/teams/{team.id}/",
        {"name": "Field", "description": "Field team", "users": []},
        format="json",
    )

    assert response.status_code == 200, response.data
    assert not team.users.exists()
    assert queued["remove"] == [([str(asha.id)], str(team.id), str(org_a.id))]


def test_patch_changes_members_only_when_it_names_them(
    org_a, admin_client, asha, ravi, queued
):
    team = _team(org_a, "Field", asha)
    url = f"/api/teams/{team.id}/"

    response = admin_client.patch(url, {"description": "Moved"}, format="json")
    assert response.status_code == 200, response.data
    assert list(team.users.all()) == [asha]
    assert queued == {"update": [], "remove": []}

    response = admin_client.patch(url, {"users": [str(ravi.id)]}, format="json")
    assert response.status_code == 200, response.data
    assert list(team.users.all()) == [ravi]
    assert queued["remove"] == [([str(asha.id)], str(team.id), str(org_a.id))]


def test_create_takes_the_documented_key(org_a, admin_client, asha):
    response = admin_client.post(
        "/api/teams/",
        {"name": "Field", "description": "Field team", "users": [str(asha.id)]},
        format="json",
    )

    assert response.status_code == 200, response.data
    assert list(Teams.objects.get(org=org_a, name="Field").users.all()) == [asha]


def test_create_refuses_a_malformed_member_id(org_a, admin_client):
    response = admin_client.post(
        "/api/teams/",
        {"name": "Field", "description": "Field team", "users": ["not-a-uuid"]},
        format="json",
    )

    assert response.status_code == 400
    assert "users" in response.data
    assert not Teams.objects.filter(org=org_a).exists()


@pytest.mark.parametrize(
    "verb, body, fmt",
    [
        ("put", {"users": None}, "json"),
        ("put", {"users": ""}, "json"),
        ("put", {"users": ""}, "multipart"),
        ("patch", {"assign_users": None}, "json"),
        ("patch", {"users": "", "assign_users": None}, "json"),
    ],
)
def test_null_or_blank_members_leaves_the_team_unchanged(
    org_a, admin_client, asha, queued, verb, body, fmt
):
    team = _team(org_a, "Field", asha)

    response = getattr(admin_client, verb)(
        f"/api/teams/{team.id}/",
        {"name": "Field", "description": "Field team", **body},
        format=fmt,
    )

    assert response.status_code == 200, response.data
    assert list(team.users.all()) == [asha]
    assert queued == {"update": [], "remove": []}


def test_form_body_with_repeated_members_keeps_them_all(
    org_a, admin_client, asha, ravi, queued
):
    joining = _member(org_a, "joining@test.com")
    both = [str(asha.id), str(ravi.id)]
    team = _team(org_a, "Field")
    url = f"/api/teams/{team.id}/"

    response = admin_client.put(
        url,
        {"name": "Field", "description": "Field team", "users": both},
        format="multipart",
    )
    assert response.status_code == 200, response.data
    assert set(team.users.all()) == {asha, ravi}

    response = admin_client.patch(
        url, {"assign_users": [*both, str(joining.id)]}, format="multipart"
    )
    assert response.status_code == 200, response.data
    assert set(team.users.all()) == {asha, ravi, joining}

    response = admin_client.post(
        "/api/teams/",
        {"name": "Desk", "description": "Desk team", "users": both},
        format="multipart",
    )
    assert response.status_code == 200, response.data
    assert set(Teams.objects.get(org=org_a, name="Desk").users.all()) == {asha, ravi}


@pytest.mark.parametrize("who", ["unknown", "other org"])
def test_unknown_or_other_org_member_ids_are_400_and_change_nothing(
    org_a, admin_client, asha, ravi, profile_b, queued, who
):
    stranger = "00000000-0000-4000-8000-000000000000"
    if who == "other org":
        stranger = str(profile_b.id)
    team = _team(org_a, "Field", asha)
    url = f"/api/teams/{team.id}/"

    for verb, key in (("put", "users"), ("patch", "assign_users")):
        response = getattr(admin_client, verb)(
            url,
            {"name": "Renamed", "description": "Moved", key: [str(ravi.id), stranger]},
            format="json",
        )
        assert response.status_code == 400
        assert list(response.data) == [key]
        assert stranger in response.data[key][0]

    response = admin_client.post(
        "/api/teams/",
        {"name": "Desk", "description": "Desk team", "users": [stranger]},
        format="json",
    )
    assert response.status_code == 400
    assert list(response.data) == ["users"]

    team.refresh_from_db()
    assert (team.name, team.description) == ("Field", "")
    assert list(team.users.all()) == [asha]
    assert not Teams.objects.filter(org=org_a, name="Desk").exists()
    assert queued == {"update": [], "remove": []}


@pytest.mark.parametrize(
    "value", [[None], [""], [{}], "[null]", '[""]', [None, ""]], ids=repr
)
def test_blank_or_null_member_entries_are_400_and_change_nothing(
    org_a, admin_client, asha, queued, value
):
    team = _team(org_a, "Field", asha)

    for verb, key in (("put", "users"), ("patch", "assign_users")):
        response = getattr(admin_client, verb)(
            f"/api/teams/{team.id}/",
            {"name": "Renamed", "description": "Moved", key: value},
            format="json",
        )
        assert response.status_code == 400, response.data
        assert list(response.data) == [key]
    response = admin_client.post(
        "/api/teams/",
        {"name": "Desk", "description": "Desk team", "users": value},
        format="json",
    )
    assert response.status_code == 400, response.data

    team.refresh_from_db()
    assert (team.name, team.description) == ("Field", "")
    assert list(team.users.all()) == [asha]
    assert not Teams.objects.filter(org=org_a, name="Desk").exists()
    assert queued == {"update": [], "remove": []}


def test_multipart_with_only_blank_member_fields_changes_nothing(
    org_a, admin_client, asha, queued
):
    team = _team(org_a, "Field", asha)

    response = admin_client.put(
        f"/api/teams/{team.id}/",
        {"name": "Renamed", "description": "Moved", "users": ["", ""]},
        format="multipart",
    )

    assert response.status_code == 400, response.data
    assert list(response.data) == ["users"]
    team.refresh_from_db()
    assert team.name == "Field"
    assert list(team.users.all()) == [asha]
    assert queued == {"update": [], "remove": []}


@pytest.mark.parametrize(
    "value, fmt", [([], "json"), ("[]", "json"), ("[ ]", "multipart")]
)
def test_an_explicit_empty_list_still_empties_the_team(
    org_a, admin_client, asha, queued, value, fmt
):
    team = _team(org_a, "Field", asha)

    response = admin_client.put(
        f"/api/teams/{team.id}/",
        {"name": "Field", "description": "Field team", "users": value},
        format=fmt,
    )

    assert response.status_code == 200, response.data
    assert not team.users.exists()
    assert queued["remove"] == [([str(asha.id)], str(team.id), str(org_a.id))]


def test_post_accepts_assign_users_list(org_a, admin_client, asha, ravi):
    response = admin_client.post(
        "/api/teams/",
        {
            "name": "Field",
            "description": "Field team",
            "assign_users": [str(asha.id), str(ravi.id)],
        },
        format="json",
    )

    assert response.status_code == 200, response.data
    team = Teams.objects.get(org=org_a, name="Field")
    assert set(team.users.all()) == {asha, ravi}


def test_team_views_require_an_org_context(org_a, admin_user, admin_profile, asha):
    for path in ("/api/teams/", "/api/teams/00000000-0000-0000-0000-000000000000/"):
        view = resolve(path).func.view_class
        assert view.permission_classes == (IsAuthenticated, HasOrgContext)

    team = _team(org_a, "Field", asha)
    client = APIClient()
    token = RefreshToken.for_user(admin_user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token.access_token}")
    assert client.get("/api/teams/").status_code == 403
    assert client.post("/api/teams/", {"name": "X"}, format="json").status_code == 403
    response = client.put(
        f"/api/teams/{team.id}/", {"name": "X", "users": []}, format="json"
    )
    assert response.status_code == 403
    team.refresh_from_db()
    assert team.name == "Field"
    assert list(team.users.all()) == [asha]
    assert not Teams.objects.filter(name="X").exists()


def test_non_admin_cannot_change_a_team(org_a, user_client, asha, ravi, queued):
    team = _team(org_a, "Field", asha)

    response = _put(user_client, team, [ravi])

    assert response.status_code == 403
    assert list(team.users.all()) == [asha]
    assert queued == {"update": [], "remove": []}


# ------------------------------------------------------------- under real RLS


@pytest.mark.postgres_only
def test_team_tasks_work_under_rls_for_the_teams_org(org_a, org_b, asha, profile_b):
    """Only meaningful under the CI role that RLS binds: with no context set
    the tasks see nothing unless they set the team's org themselves."""
    if connection.vendor != "postgresql":
        pytest.skip("RLS requires PostgreSQL")
    team = _team(org_a, "Field", asha)
    records = _records(org_a, team)
    with rls_org(org_b):
        theirs = _team(org_b, "Theirs", profile_b)
        their_records = _records(org_b, theirs)

    def current_org():
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_setting('app.current_org', true)")
            return cursor.fetchone()[0]

    clear_rls_context()
    update_team_users(str(team.id), str(org_a.id))
    assert current_org() == ""
    restore_rls_context()
    assert _kinds_with(records, asha) == KINDS

    team.users.remove(asha)
    clear_rls_context()
    remove_users([str(asha.id)], str(team.id), str(org_a.id))
    assert current_org() == ""
    restore_rls_context()
    assert _kinds_with(records, asha) == set()

    with rls_org(org_b):
        assert _kinds_with(their_records, profile_b) == set()
