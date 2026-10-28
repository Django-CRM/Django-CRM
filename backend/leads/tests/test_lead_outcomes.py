"""Won and lost dates on leads, and ``GET /api/leads/outcomes/``.

A lead is won when its status is converted or its stage is a won stage, lost
when its status is closed or its stage is a lost stage, and open otherwise.
`Lead.save()` stamps `won_at` or `lost_at` when a save changes that, and
clears both when the lead reopens. The view counts, per person assigned now,
the leads won and lost inside the window and the leads that arrived in it.
"""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from django.utils import timezone

from common.models import Profile, User
from common.org_time import org_timezone
from common.testing import _make_authenticated_client, rls_org
from leads.models import Lead, LeadPipeline, LeadStage

URL = "/api/leads/outcomes/"
NOW = timezone.now()
ROW_KEYS = {
    "profile",
    "name",
    "won",
    "lost",
    "win_rate",
    "median_seconds_to_win",
    "new_leads",
}
TOTAL_KEYS = ROW_KEYS - {"profile", "name"}


@pytest.fixture
def stages(org_a):
    pipeline = LeadPipeline.objects.create(name="Admissions", org=org_a)
    return SimpleNamespace(
        **{
            kind: LeadStage.objects.create(
                pipeline=pipeline, name=kind.title(), order=order, stage_type=kind
            )
            for order, kind in enumerate(("open", "won", "lost"))
        }
    )


@pytest.fixture
def clock(monkeypatch):
    """Stamps read this clock, so two stamps can be told apart."""
    times = SimpleNamespace(now=NOW)
    monkeypatch.setattr(
        "leads.outcomes.timezone", SimpleNamespace(now=lambda: times.now)
    )
    return times


def _open_lead(org, **fields):
    fields.setdefault("status", "in process")
    fields.setdefault("first_name", "Ada")
    lead = Lead.objects.create(org=org, **fields)
    return Lead.objects.get(pk=lead.pk)


def _saved(lead):
    return Lead.objects.values("won_at", "lost_at").get(pk=lead.pk)


# ------------------------------------------------------------------ the stamps


def test_converting_stamps_won_at(org_a, clock):
    lead = _open_lead(org_a)
    lead.status = "converted"
    lead.save()
    assert _saved(lead) == {"won_at": NOW, "lost_at": None}


def test_moving_to_a_won_stage_stamps_won_at(org_a, stages, clock):
    lead = _open_lead(org_a, stage=stages.open)
    lead.stage = stages.won
    lead.save()
    assert _saved(lead) == {"won_at": NOW, "lost_at": None}


@pytest.mark.parametrize("change", ["closed status", "lost stage"])
def test_closed_status_or_lost_stage_stamps_lost_at(org_a, stages, clock, change):
    lead = _open_lead(org_a, stage=stages.open)
    if change == "closed status":
        lead.status = "closed"
    else:
        lead.stage = stages.lost
    lead.save()
    assert _saved(lead) == {"won_at": None, "lost_at": NOW}


def test_won_wins_when_both_apply(org_a, stages, clock):
    lead = _open_lead(org_a, stage=stages.open)
    lead.status = "closed"
    lead.stage = stages.won
    lead.save()
    assert _saved(lead) == {"won_at": NOW, "lost_at": None}


def test_won_then_lost_clears_won_at(org_a, stages, clock):
    lead = _open_lead(org_a, stage=stages.open)
    lead.stage = stages.won
    lead.save()
    clock.now = NOW + timedelta(hours=1)
    lead.stage = stages.lost
    lead.save()
    assert _saved(lead) == {"won_at": None, "lost_at": NOW + timedelta(hours=1)}


@pytest.mark.parametrize("closed_by", ["status", "stage"])
def test_reopening_clears_both(org_a, stages, clock, closed_by):
    lead = _open_lead(org_a, stage=stages.open)
    if closed_by == "status":
        lead.status = "closed"
    else:
        lead.stage = stages.won
    lead.save()
    assert _saved(lead) != {"won_at": None, "lost_at": None}

    lead.status = "in process"
    lead.stage = stages.open
    lead.save()
    assert _saved(lead) == {"won_at": None, "lost_at": None}


def test_won_again_after_reopening_is_stamped_again(org_a, stages, clock):
    lead = _open_lead(org_a, stage=stages.open)
    lead.stage = stages.won
    lead.save()
    lead.stage = stages.open
    lead.save()
    clock.now = NOW + timedelta(days=2)
    lead.stage = stages.won
    lead.save()
    assert _saved(lead) == {"won_at": NOW + timedelta(days=2), "lost_at": None}


def test_a_move_between_two_won_stages_keeps_the_first_stamp(org_a, stages, clock):
    other_won = LeadStage.objects.create(
        pipeline=stages.won.pipeline, name="Enrolled", order=9, stage_type="won"
    )
    lead = _open_lead(org_a, stage=stages.open)
    lead.stage = stages.won
    lead.save()
    clock.now = NOW + timedelta(days=1)
    lead.stage = other_won
    lead.save()
    assert _saved(lead)["won_at"] == NOW


def test_editing_an_old_converted_lead_stamps_nothing(org_a, clock):
    """Converted before the release: nothing knows when, so an edit today must
    not count it as won today."""
    lead = _open_lead(org_a)
    Lead.objects.filter(pk=lead.pk).update(status="converted")

    old = Lead.objects.get(pk=lead.pk)
    old.title = "Renamed"
    old.save()

    assert _saved(lead) == {"won_at": None, "lost_at": None}


def test_a_stale_instance_refreshed_from_the_row_stamps_nothing(org_a, clock):
    lead = _open_lead(org_a)
    Lead.objects.filter(pk=lead.pk).update(status="converted")
    lead.refresh_from_db()
    lead.title = "Renamed"
    lead.save()
    assert _saved(lead) == {"won_at": None, "lost_at": None}


def test_a_lead_created_won_is_not_stamped(org_a, stages, clock):
    converted = Lead.objects.create(org=org_a, first_name="A", status="converted")
    at_lost = Lead.objects.create(org=org_a, first_name="B", stage=stages.lost)
    for lead in (converted, at_lost):
        lead.title = "Saved again"
        lead.save()
        assert _saved(lead) == {"won_at": None, "lost_at": None}


def test_recycled_is_open(org_a, clock):
    lead = _open_lead(org_a)
    lead.status = "recycled"
    lead.save()
    assert _saved(lead) == {"won_at": None, "lost_at": None}

    lead.status = "closed"
    lead.save()
    lead.status = "recycled"
    lead.save()
    assert _saved(lead) == {"won_at": None, "lost_at": None}


def test_update_fields_save_writes_the_stamps(org_a, stages, clock):
    lead = _open_lead(org_a, stage=stages.open)
    lead.status = "converted"
    lead.save(update_fields=["status"])
    assert _saved(lead) == {"won_at": NOW, "lost_at": None}

    other = _open_lead(org_a, first_name="Bo", stage=stages.open)
    other.stage = stages.lost
    other.save(update_fields=["stage"])
    assert _saved(other) == {"won_at": None, "lost_at": NOW}


def test_a_save_that_names_neither_status_nor_stage_stamps_nothing(org_a, clock):
    lead = _open_lead(org_a)
    lead.status = "converted"
    lead.title = "Only the title"
    lead.save(update_fields=["title"])
    assert _saved(lead) == {"won_at": None, "lost_at": None}

    lead.save(update_fields=["status"])
    assert _saved(lead) == {"won_at": NOW, "lost_at": None}


def test_kanban_move_and_conversion_stamp(org_a, admin_client, stages):
    moved = _open_lead(org_a, email="moved@example.com", stage=stages.open)
    closed = _open_lead(org_a, email="closed@example.com")
    converted = _open_lead(
        org_a, email="converted@example.com", company_name="Analytical"
    )
    before = timezone.now()

    response = admin_client.patch(
        f"/api/leads/{moved.id}/move/", {"stage_id": str(stages.won.id)}, format="json"
    )
    assert response.status_code == 200, response.data
    response = admin_client.patch(
        f"/api/leads/{closed.id}/move/", {"status": "closed"}, format="json"
    )
    assert response.status_code == 200, response.data
    response = admin_client.patch(
        f"/api/leads/{converted.id}/",
        {"status": "converted", "email": "converted@example.com"},
        format="json",
    )
    assert response.status_code == 200, response.data

    after = timezone.now()
    for lead, field in ((moved, "won_at"), (closed, "lost_at"), (converted, "won_at")):
        stamps = _saved(lead)
        assert before <= stamps[field] <= after, (lead.email, stamps)
        assert [value for value in stamps.values() if value] == [stamps[field]]


def test_stamps_are_not_writable_through_the_api(org_a, admin_client):
    lead = _open_lead(org_a, email="ada@example.com")
    forged = "2020-01-01T00:00:00Z"
    for verb in ("put", "patch"):
        response = getattr(admin_client, verb)(
            f"/api/leads/{lead.id}/",
            {
                "first_name": "Ada",
                "email": "ada@example.com",
                "status": "in process",
                "won_at": forged,
                "lost_at": forged,
            },
            format="json",
        )
        assert response.status_code == 200, response.data
        assert _saved(lead) == {"won_at": None, "lost_at": None}

    since = org_a.lead_outcomes_since
    response = admin_client.patch(
        "/api/org/settings/", {"lead_outcomes_since": forged}, format="json"
    )
    assert response.status_code == 200, response.data
    org_a.refresh_from_db()
    assert org_a.lead_outcomes_since == since


def test_merge_never_copies_won_or_lost_dates(org_a, admin_client, stages, clock):
    """The keeper's outcome is the keeper's: a merge copies blank fields from
    the lead merged away, but never its stamps."""
    open_keeper = _open_lead(org_a, email="open@example.com", stage=stages.open)
    lost_keeper = _open_lead(org_a, email="lost@example.com", stage=stages.open)
    lost_keeper.status = "closed"
    lost_keeper.save()
    for keeper in (open_keeper, lost_keeper):
        loser = _open_lead(
            org_a, email=f"won-{keeper.email}", first_name="Bo", stage=stages.open
        )
        clock.now = NOW + timedelta(days=1)
        loser.stage = stages.won
        loser.save()
        assert _saved(loser)["won_at"] == NOW + timedelta(days=1)

        response = admin_client.post(
            f"/api/leads/{keeper.id}/merge/",
            {"merge_id": str(loser.id)},
            format="json",
        )
        assert response.status_code == 200, response.data

    assert _saved(open_keeper) == {"won_at": None, "lost_at": None}
    assert _saved(lost_keeper) == {"won_at": None, "lost_at": NOW}


# -------------------------------------------------------------------- the view


def _member(org, email, name="", role="USER", is_active=True):
    user = User.objects.create_user(email=email, password="x", name=name)
    profile = Profile.objects.create(user=user, org=org, role=role, is_active=is_active)
    return SimpleNamespace(
        profile=profile, client=_make_authenticated_client(user, org, profile)
    )


@pytest.fixture
def colleague(org_a):
    return _member(org_a, "colleague@test.com", name="Maria Lopez")


def _lead(org, *, created_at, assigned=(), **stamps):
    """A lead that arrived at ``created_at``, with ``won_at``/``lost_at`` set
    directly: the view only reads them."""
    with rls_org(org):
        lead = Lead.objects.create(org=org, first_name="Ada", status="in process")
        Lead.objects.filter(pk=lead.pk).update(created_at=created_at, **stamps)
        for profile in assigned:
            lead.assigned_to.add(profile)
    return lead


def _won(org, assigned, *, seconds, at=NOW - timedelta(days=1)):
    """A lead won at ``at``, ``seconds`` after it arrived."""
    return _lead(
        org,
        created_at=at - timedelta(seconds=seconds),
        assigned=assigned,
        won_at=at,
    )


def _lost(org, assigned, *, at=NOW - timedelta(days=1)):
    return _lead(org, created_at=at - timedelta(days=3), assigned=assigned, lost_at=at)


def _get(client, **params):
    response = client.get(URL, params)
    assert response.status_code == 200, response.data
    return response.data


def _row_for(data, profile):
    (row,) = [row for row in data["rows"] if row["profile"] == str(profile.id)]
    return row


def test_outcomes_rows_count_won_lost_and_new_per_assignee(
    org_a, admin_client, admin_profile, user_profile, colleague
):
    mine = [user_profile]
    _won(org_a, mine, seconds=60)
    _won(org_a, mine, seconds=60)
    _lost(org_a, mine)
    _lead(org_a, created_at=NOW - timedelta(days=40), assigned=mine)
    _won(org_a, [colleague.profile], seconds=60)
    _lead(org_a, created_at=NOW - timedelta(hours=1), assigned=[colleague.profile])
    _member(org_a, "gone@test.com", name="Gone", is_active=False)

    data = _get(admin_client)

    assert {row["profile"] for row in data["rows"]} == {
        str(admin_profile.id),
        str(user_profile.id),
        str(colleague.profile.id),
    }
    assert all(set(row) == ROW_KEYS for row in data["rows"])
    mine_row = _row_for(data, user_profile)
    # The lost lead arrived three days before it was lost, inside the window.
    assert (mine_row["won"], mine_row["lost"], mine_row["new_leads"]) == (2, 1, 3)
    theirs = _row_for(data, colleague.profile)
    assert (theirs["won"], theirs["lost"], theirs["new_leads"]) == (1, 0, 2)
    admin_row = _row_for(data, admin_profile)
    assert (admin_row["won"], admin_row["lost"], admin_row["new_leads"]) == (0, 0, 0)
    assert admin_row["win_rate"] is None
    assert admin_row["median_seconds_to_win"] is None


def test_win_rate_and_median(org_a, user_client, user_profile):
    mine = [user_profile]
    for seconds in (60, 600, 3600, 86400):
        _won(org_a, mine, seconds=seconds)
    for _ in range(4):
        _lost(org_a, mine)
    _lost(org_a, mine)

    (row,) = _get(user_client)["rows"]

    assert row["won"] == 4
    assert row["lost"] == 5
    assert row["win_rate"] == 44
    assert row["median_seconds_to_win"] == 2100


def test_an_odd_count_takes_the_middle_time_to_win(org_a, user_client, user_profile):
    for seconds in (10, 500, 9000):
        _won(org_a, [user_profile], seconds=seconds)
    (row,) = _get(user_client)["rows"]
    assert row["median_seconds_to_win"] == 500
    assert row["win_rate"] == 100


def test_outcomes_outside_the_window_are_not_counted(org_a, user_client, user_profile):
    org_a.timezone = "Asia/Kolkata"
    org_a.save(update_fields=["timezone"])
    with org_timezone(org_a):
        start = datetime.combine(
            timezone.localdate() - timedelta(days=6),
            datetime.min.time(),
            tzinfo=timezone.get_current_timezone(),
        )
    mine = [user_profile]
    _won(org_a, mine, seconds=60, at=start)
    _won(org_a, mine, seconds=60, at=start - timedelta(seconds=1))
    _lost(org_a, mine, at=start - timedelta(seconds=1))
    _lead(org_a, created_at=start - timedelta(seconds=1), assigned=mine)

    (row,) = _get(user_client, days="7")["rows"]
    assert (row["won"], row["lost"], row["new_leads"]) == (1, 0, 0)

    (row,) = _get(user_client, days="30")["rows"]
    assert (row["won"], row["lost"]) == (2, 1)


def test_a_lead_with_two_assignees_counts_for_both_and_once_in_totals(
    org_a, admin_client, user_profile, colleague
):
    both = [user_profile, colleague.profile]
    _won(org_a, both, seconds=100)
    _lost(org_a, both)
    _won(org_a, [], seconds=300)
    _lead(org_a, created_at=NOW - timedelta(hours=2))

    data = _get(admin_client)

    for profile in both:
        row = _row_for(data, profile)
        assert (row["won"], row["lost"], row["new_leads"]) == (1, 1, 2)
        assert row["median_seconds_to_win"] == 100
    assert data["totals"] == {
        "won": 2,
        "lost": 1,
        "win_rate": 67,
        "median_seconds_to_win": 200,
        "new_leads": 4,
    }


def test_member_sees_only_their_row_and_no_totals(
    org_a, admin_client, user_client, user_profile, colleague
):
    _won(org_a, [colleague.profile], seconds=60)
    _won(org_a, [user_profile], seconds=60)

    data = _get(user_client)
    assert [row["profile"] for row in data["rows"]] == [str(user_profile.id)]
    assert data["totals"] is None

    data = _get(admin_client)
    assert len(data["rows"]) == 3
    assert set(data["totals"]) == TOTAL_KEYS
    assert data["totals"]["won"] == 2


def test_anonymous_is_refused(unauthenticated_client):
    assert unauthenticated_client.get(URL).status_code in (401, 403)


@pytest.mark.parametrize("days", ["6", "91", "0", "-30", "abc", "7.5", "1e2"])
def test_days_out_of_range_is_400(org_a, user_client, days):
    response = user_client.get(URL, {"days": days})
    assert response.status_code == 400
    assert "days" in response.data


def test_since_and_measured_since_are_the_orgs_dates(org_a, user_client):
    org_a.timezone = "Pacific/Kiritimati"
    org_a.lead_outcomes_since = datetime.fromisoformat("2026-10-28T11:00:00+00:00")
    org_a.save(update_fields=["timezone", "lead_outcomes_since"])
    with org_timezone(org_a):
        today = timezone.localdate()

    data = _get(user_client)

    assert data["since"] == (today - timedelta(days=29)).isoformat()
    # 11:00 UTC is already the next day at UTC+14.
    assert data["measured_since"] == "2026-10-29"
    assert (
        _get(user_client, days="7")["since"] == (today - timedelta(days=6)).isoformat()
    )


def test_other_orgs_leads_are_never_counted(
    org_a, org_b, admin_client, org_b_client, user_profile, profile_b
):
    _won(org_a, [user_profile], seconds=60)
    # A foreign assignee planted on this org's lead counts nowhere.
    _won(org_a, [profile_b], seconds=60)
    _won(org_b, [profile_b], seconds=60)
    _lost(org_b, [profile_b])
    _lost(org_b, [user_profile])

    data = _get(admin_client)
    assert str(profile_b.id) not in {row["profile"] for row in data["rows"]}
    assert (
        _row_for(data, user_profile)["won"],
        _row_for(data, user_profile)["lost"],
    ) == (
        1,
        0,
    )
    assert (data["totals"]["won"], data["totals"]["lost"]) == (2, 0)

    data = _get(org_b_client)
    (row,) = data["rows"]
    assert row["profile"] == str(profile_b.id)
    assert (row["won"], row["lost"]) == (1, 1)
    assert (data["totals"]["won"], data["totals"]["lost"]) == (1, 2)
