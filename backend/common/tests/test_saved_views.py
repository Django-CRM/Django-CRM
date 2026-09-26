"""Saved list views (G29): private to their owner, and only what the list reads.

Run with: pytest common/tests/test_saved_views.py -v
"""

import uuid
from types import SimpleNamespace

import pytest
from django.http import QueryDict
from rest_framework import status as http

from common.models import Profile, SavedView, User
from common.saved_views import LISTS, MAX_VIEWS_PER_LIST
from common.testing import _make_authenticated_client

URL = "/api/saved-views/"


def detail(pk):
    return f"{URL}{pk}/"


def create(client, **body):
    payload = {"module": "leads", "name": "Hot", "filters": {}}
    payload.update(body)
    return client.post(URL, payload, format="json")


@pytest.fixture
def colleague(org_a):
    """A second member of org A, with a client of their own."""
    user = User.objects.create_user(email="colleague@test.com", password="x")
    profile = Profile.objects.create(user=user, org=org_a, role="USER", is_active=True)
    return SimpleNamespace(
        profile=profile, client=_make_authenticated_client(user, org_a, profile)
    )


# ---------------------------------------------------------------- owner CRUD


def test_owner_creates_lists_reads_renames_and_deletes(user_client, user_profile):
    tag = str(uuid.uuid4())
    created = create(
        user_client,
        name="  Hot leads  ",
        filters={"status": ["assigned", "in process"], "tags": tag, "search": ""},
    )
    assert created.status_code == http.HTTP_201_CREATED, created.data
    view_id = created.data["id"]
    assert created.data["name"] == "Hot leads"
    # One value becomes a list of one; a blank is dropped with its key.
    assert created.data["filters"] == {
        "status": ["assigned", "in process"],
        "tags": [tag],
    }
    stored = SavedView.objects.get(pk=view_id)
    assert stored.profile == user_profile
    assert stored.org == user_profile.org

    listed = user_client.get(URL, {"module": "leads"})
    assert listed.status_code == 200
    assert [v["id"] for v in listed.data["saved_views"]] == [view_id]
    assert listed.data["limit"] == MAX_VIEWS_PER_LIST

    assert user_client.get(detail(view_id)).data["name"] == "Hot leads"

    renamed = user_client.patch(detail(view_id), {"name": "Warm"}, format="json")
    assert renamed.status_code == 200, renamed.data
    assert renamed.data["name"] == "Warm"
    assert renamed.data["filters"]["tags"] == [tag]

    refiltered = user_client.patch(
        detail(view_id), {"filters": {"source": "call"}}, format="json"
    )
    assert refiltered.status_code == 200, refiltered.data
    assert refiltered.data["filters"] == {"source": ["call"]}

    assert user_client.delete(detail(view_id)).status_code == 204
    assert not SavedView.objects.filter(pk=view_id).exists()


def test_list_without_module_returns_every_list_and_bad_module_is_400(user_client):
    create(user_client, module="leads", name="A")
    create(user_client, module="cases", name="B")
    everything = user_client.get(URL)
    assert sorted(v["module"] for v in everything.data["saved_views"]) == [
        "cases",
        "leads",
    ]
    assert user_client.get(URL, {"module": "tasks"}).status_code == 400
    assert create(user_client, module="tasks").status_code == 400


def test_every_one_of_the_six_lists_takes_a_view(user_client):
    for module in LISTS:
        response = create(user_client, module=module, name=f"{module} view")
        assert response.status_code == 201, (module, response.data)


def test_anonymous_is_refused(unauthenticated_client):
    assert unauthenticated_client.get(URL).status_code in (401, 403)
    assert create(unauthenticated_client).status_code in (401, 403)
    assert not SavedView.objects.exists()


# ------------------------------------------------------------- private views


def test_another_members_view_is_404_on_every_verb(user_client, colleague):
    theirs = create(colleague.client, name="Theirs").data["id"]
    missing = str(uuid.uuid4())

    # Hidden from the list, and every verb answers exactly as for a missing id.
    assert user_client.get(URL).data["saved_views"] == []
    for verb, kwargs in (
        ("get", {}),
        ("patch", {"data": {"name": "Mine now"}, "format": "json"}),
        ("delete", {}),
    ):
        hidden = getattr(user_client, verb)(detail(theirs), **kwargs)
        absent = getattr(user_client, verb)(detail(missing), **kwargs)
        assert hidden.status_code == absent.status_code == 404, verb
        assert hidden.data == absent.data, verb

    view = SavedView.objects.get(pk=theirs)
    assert view.name == "Theirs"
    # The owner still reads it: the check refuses one person, not everyone.
    assert colleague.client.get(detail(theirs)).status_code == 200


def test_an_admin_cannot_read_a_members_view_either(admin_client, user_client):
    member_view = create(user_client, name="Private").data["id"]
    assert admin_client.get(detail(member_view)).status_code == 404
    assert admin_client.get(URL).data["saved_views"] == []


def test_another_orgs_view_is_invisible(user_client, org_b_client):
    other = create(org_b_client, name="Org B view").data["id"]
    assert user_client.get(URL).data["saved_views"] == []
    assert user_client.get(detail(other)).status_code == 404
    assert user_client.delete(detail(other)).status_code == 404
    assert SavedView.objects.filter(pk=other).exists()


def test_org_and_profile_in_the_body_are_refused(user_client, colleague, org_b):
    response = create(user_client, profile=str(colleague.profile.id))
    assert response.status_code == 400
    assert "profile" in response.data
    response = create(user_client, org=str(org_b.id))
    assert response.status_code == 400
    assert "org" in response.data
    assert not SavedView.objects.exists()

    mine = create(user_client).data["id"]
    moved = user_client.patch(
        detail(mine), {"profile": str(colleague.profile.id)}, format="json"
    )
    assert moved.status_code == 400
    assert SavedView.objects.get(pk=mine).profile.user.email == "user@test.com"


def test_a_view_cannot_move_to_another_list(user_client):
    mine = create(user_client).data["id"]
    response = user_client.patch(detail(mine), {"module": "cases"}, format="json")
    assert response.status_code == 400
    assert "module" in response.data
    # Naming its own list again is not a move.
    same = user_client.patch(detail(mine), {"module": "leads"}, format="json")
    assert same.status_code == 200


# ------------------------------------------------------------ what it holds


@pytest.mark.parametrize(
    "module, filters",
    [
        ("leads", {"limit": "10"}),
        ("leads", {"junk": "x"}),
        ("leads", {"pipeline": str(uuid.uuid4())}),  # a deal param, not a lead one
        ("invoices", {"due_date__gte": "2026-01-01"}),  # invoices use one underscore
        ("cases", {"cf_": "x"}),
        ("cases", {"cf_bad-key": "x"}),
        ("leads", ["status", "assigned"]),
        ("leads", "status=assigned"),
        ("leads", {"status": 5}),
        ("leads", {"status": ["assigned", None]}),
        ("leads", {"status": {"nested": "x"}}),
        ("leads", {"search": "x" * 201}),
        ("leads", {"status": ["assigned"] * 51}),
        ("leads", {f"cf_k{i}": "x" for i in range(31)}),
        # Malformed values are refused by the list's own parsing.
        ("leads", {"assigned_to": "not-a-uuid"}),
        ("contacts", {"tags": ["x"]}),
        ("accounts", {"created_at__gte": "2026-02-30"}),
        ("opportunities", {"amount__gte": "lots"}),
        ("opportunities", {"amount__lte": "NaN"}),
        ("cases", {"account": "nope"}),
        ("invoices", {"due_date_gte": "yesterday"}),
    ],
)
def test_junk_filters_are_400(user_client, module, filters):
    response = create(user_client, module=module, filters=filters)
    assert response.status_code == 400, response.data
    assert "filters" in response.data
    assert not SavedView.objects.exists()


def test_junk_filters_are_400_on_update_too(user_client):
    mine = create(user_client, filters={"source": "call"}).data["id"]
    response = user_client.patch(
        detail(mine), {"filters": {"assigned_to": "x"}}, format="json"
    )
    assert response.status_code == 400
    assert SavedView.objects.get(pk=mine).filters == {"source": ["call"]}


def _sample(key):
    """A well-formed value for list parameter ``key``."""
    if key in {
        "assigned_to",
        "tags",
        "account",
        "contact",
        "opportunity",
        "pipeline",
        "created_by",
    }:
        return str(uuid.uuid4())
    if key.startswith("amount"):
        return "10.5"
    if "date" in key or "created_at" in key or "closed_on" in key:
        return "2026-01-02"
    if key == "next_follow_up":
        return "2026-01-02"
    return "true"


def test_every_param_the_lists_read_is_accepted(user_client):
    for module, (_build, accepted) in LISTS.items():
        filters = {key: _sample(key) for key in accepted}
        filters["cf_region"] = "EMEA"
        response = create(user_client, module=module, name=module, filters=filters)
        assert response.status_code == 201, (module, response.data)


class _Recorder(QueryDict):
    """A query string that remembers every key it was asked for."""

    def __init__(self):
        super().__init__(mutable=True)
        # Non-empty, so a list that returns early on no params still reads on.
        self.setlist("__probe__", ["x"])
        self.read = set()

    def __getitem__(self, key):
        self.read.add(key)
        return super().__getitem__(key)

    def get(self, key, default=None):
        self.read.add(key)
        return super().get(key, default)

    def getlist(self, key, default=None):
        self.read.add(key)
        return super().getlist(key, default)


@pytest.mark.parametrize("module", sorted(LISTS))
def test_accepted_params_match_what_the_list_reads(module, admin_user, admin_profile):
    """The written-out sets drift from the list functions the day one of them
    grows a filter. Asked both ways, so a param a list stops reading is caught
    as well as one it starts reading."""
    build, accepted = LISTS[module]
    params = _Recorder()
    build(SimpleNamespace(profile=admin_profile, user=admin_user), params)
    assert params.read - {"__probe__"} == accepted


# ------------------------------------------------------------ names and cap


def test_duplicate_name_is_400_case_insensitively(user_client, colleague):
    assert create(user_client, name="Hot").status_code == 201
    duplicate = create(user_client, name=" hot ")
    assert duplicate.status_code == 400
    assert "name" in duplicate.data
    # Another list, or another person, may use the same name.
    assert create(user_client, module="cases", name="Hot").status_code == 201
    assert create(colleague.client, name="Hot").status_code == 201

    other = create(user_client, name="Cold").data["id"]
    clash = user_client.patch(detail(other), {"name": "HOT"}, format="json")
    assert clash.status_code == 400
    # Renaming a view to its own name, in another case, is not a clash.
    assert (
        user_client.patch(detail(other), {"name": "COLD"}, format="json").status_code
        == 200
    )


def test_name_is_required_and_bounded(user_client):
    assert create(user_client, name="   ").status_code == 400
    assert create(user_client, name="x" * 101).status_code == 400
    assert create(user_client, name="x" * 100).status_code == 201
    body = {"module": "leads", "filters": {}}
    assert user_client.post(URL, body, format="json").status_code == 400


def test_cap_per_list_is_enforced(user_client, colleague):
    for i in range(MAX_VIEWS_PER_LIST):
        assert create(user_client, name=f"View {i}").status_code == 201
    over = create(user_client, name="One too many")
    assert over.status_code == 400
    assert SavedView.objects.filter(module="leads").count() == MAX_VIEWS_PER_LIST
    # The cap is per person and per list.
    assert create(user_client, module="cases", name="Fine").status_code == 201
    assert create(colleague.client, name="Fine").status_code == 201
    # A full list can still be renamed.
    first = SavedView.objects.filter(name="View 0").first()
    assert (
        user_client.patch(
            detail(first.id), {"name": "Renamed"}, format="json"
        ).status_code
        == 200
    )


def test_a_save_that_loses_the_race_is_a_400_not_a_500(user_profile, regular_user):
    """Two saves can both pass the name check; the constraint catches the second."""
    from rest_framework.exceptions import ValidationError

    from common.saved_views import SavedViewSerializer

    request = SimpleNamespace(profile=user_profile, user=regular_user)
    serializer = SavedViewSerializer(
        data={"module": "leads", "name": "Hot"}, context={"request": request}
    )
    assert serializer.is_valid(), serializer.errors
    SavedView.objects.create(
        org=user_profile.org, profile=user_profile, module="leads", name="HOT"
    )
    with pytest.raises(ValidationError) as caught:
        serializer.save(org=user_profile.org, profile=user_profile)
    assert "name" in caught.value.detail
    assert SavedView.objects.count() == 1


def test_a_refused_value_reads_as_one_sentence_per_param(user_client):
    response = create(user_client, filters={"assigned_to": "nope"})
    assert response.status_code == 400
    assert response.data["filters"] == ["assigned_to: 'nope' is not a valid id."]
