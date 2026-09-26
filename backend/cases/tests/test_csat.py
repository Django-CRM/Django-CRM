"""Tests for the CSAT survey lifecycle.

Covers the closed-case → email → public submit → analytics roll-up path
end-to-end. Uses the synchronous Celery configuration in test_settings,
so `send_csat_survey.apply_async(countdown=...)` runs inline.
"""

from __future__ import annotations

import re
from datetime import timedelta

import pytest
from django.utils import timezone

from cases import analytics
from cases.models import Case, CsatSurvey
from cases.tasks import (
    CSAT_RATING_MAX,
    CSAT_RATING_MIN,
    CSAT_TOKEN_TTL_DAYS,
    csat_signer,
    hash_csat_token,
    send_csat_survey,
)
from common.portal_tokens import register_portal_token_hash
from contacts.models import Contact


@pytest.fixture
def contact_with_email(org_a):
    return Contact.objects.create(
        org=org_a,
        first_name="Pat",
        last_name="Smith",
        email="pat@example.com",
    )


@pytest.fixture
def closed_case(org_a, contact_with_email):
    case = Case.objects.create(
        org=org_a,
        name="Login bug",
        status="Closed",
        priority="Normal",
        closed_on=timezone.localdate(),
        resolved_at=timezone.now(),
    )
    case.contacts.add(contact_with_email)
    return case


def _rehash(survey, token):
    survey.token_hash = hash_csat_token(token)
    survey.save(update_fields=["token_hash"])
    register_portal_token_hash(survey.token_hash, survey.org_id, "csat", survey.id)


# --------------------------------------------------------------------------
# Token helpers


class TestCsatTokens:
    def test_sign_and_verify_roundtrip(self):
        token = csat_signer().sign("abc")
        assert csat_signer().unsign(token) == "abc"

    def test_hash_is_deterministic(self):
        assert hash_csat_token("foo") == hash_csat_token("foo")
        assert len(hash_csat_token("foo")) == 64

    def test_different_tokens_different_hashes(self):
        assert hash_csat_token("foo") != hash_csat_token("bar")


# --------------------------------------------------------------------------
# Send task


class TestSendCsatSurvey:
    def test_creates_survey_row(self, closed_case):
        result = send_csat_survey(str(closed_case.id), str(closed_case.org_id))
        assert result is not None
        survey = CsatSurvey.objects.get(case=closed_case)
        assert survey.contact.email == "pat@example.com"
        assert survey.token_hash  # populated
        assert survey.rating is None  # not yet submitted
        assert survey.expires_at > timezone.now() + timedelta(
            days=CSAT_TOKEN_TTL_DAYS - 1
        )

    def test_skips_when_no_contact_email(self, org_a):
        # Case with no contacts at all.
        case = Case.objects.create(
            org=org_a,
            name="No contact",
            status="Closed",
            priority="Normal",
            closed_on=timezone.localdate(),
        )
        result = send_csat_survey(str(case.id), str(case.org_id))
        assert result is None
        assert not CsatSurvey.objects.filter(case=case).exists()

    def test_skips_when_org_disabled(self, closed_case):
        closed_case.org.csat_enabled = False
        closed_case.org.save()
        result = send_csat_survey(str(closed_case.id), str(closed_case.org_id))
        assert result is None
        assert not CsatSurvey.objects.filter(case=closed_case).exists()

    def test_skips_when_reopened(self, closed_case):
        # Simulate the spec's reopen-protection: status flipped back to
        # Pending before the delayed task fires.
        Case.objects.filter(pk=closed_case.pk).update(status="Pending")
        result = send_csat_survey(str(closed_case.id), str(closed_case.org_id))
        assert result is None

    def test_idempotent_on_existing_survey(self, closed_case):
        send_csat_survey(str(closed_case.id), str(closed_case.org_id))
        result = send_csat_survey(str(closed_case.id), str(closed_case.org_id))
        # Second call short-circuits.
        assert result is None
        assert CsatSurvey.objects.filter(case=closed_case).count() == 1


# --------------------------------------------------------------------------
# The survey email
#
# `csat/survey_email.html` did not exist for the whole life of this feature.
# The render sat under a bare `except Exception` that fell back to a plain
# link, with a comment claiming production had the template, so every survey
# ever sent went out through the fallback and the suite stayed green because
# nothing here looked at the body. These assert the body.


class TestCsatSurveyEmail:
    def test_sends_one_html_email(self, closed_case, mailoutbox):
        send_csat_survey(str(closed_case.id), str(closed_case.org_id))
        assert len(mailoutbox) == 1
        assert mailoutbox[0].content_subtype == "html"
        assert mailoutbox[0].to == ["pat@example.com"]

    def test_body_renders_the_real_template(self, closed_case, mailoutbox):
        """The fallback had none of this markup, so it pins template, not text."""
        send_csat_survey(str(closed_case.id), str(closed_case.org_id))
        body = mailoutbox[0].body
        # From root_email_template_new.html, which only an extending template
        # can produce.
        assert "Sent by BottleCRM" in body
        assert closed_case.name in body

    def test_one_star_link_per_point_on_the_scale(self, closed_case, mailoutbox):
        send_csat_survey(str(closed_case.id), str(closed_case.org_id))
        survey = CsatSurvey.objects.get(case=closed_case)
        body = mailoutbox[0].body

        expected = list(range(CSAT_RATING_MIN, CSAT_RATING_MAX + 1))
        for value in expected:
            assert f"?rating={value}" in body, f"no star link for rating {value}"
        # And nothing off the ends, which the API would reject on arrival.
        for value in (CSAT_RATING_MIN - 1, CSAT_RATING_MAX + 1):
            assert f"?rating={value}" not in body
        assert body.count("?rating=") == len(expected)
        assert survey.rating is None  # arriving is not answering

    def test_links_point_at_the_survey_for_this_token(self, closed_case, mailoutbox):
        """Every star carries the same token, so one click identifies one survey."""
        send_csat_survey(str(closed_case.id), str(closed_case.org_id))
        survey = CsatSurvey.objects.get(case=closed_case)
        body = mailoutbox[0].body

        tokens = set(re.findall(r"/csat/([^?\"]+)", body))
        assert len(tokens) == 1, f"expected one token in the email, got {tokens}"
        assert hash_csat_token(tokens.pop()) == survey.token_hash


# --------------------------------------------------------------------------
# Public GET


class TestPublicCsatGet:
    def _seed(self, case):
        send_csat_survey(str(case.id), str(case.org_id))
        survey = CsatSurvey.objects.get(case=case)
        # The raw token isn't stored on the row; reconstruct one that
        # collides with the same hash by recomputing what the task signed.
        # In practice the customer reads it from the email link; for tests
        # we sign here with the same salt so the hash matches.
        token = csat_signer().sign(str(case.id))
        # Persist the hash for this token (tasks signed a different one
        # per `signer.sign` non-determinism, so update for the test), and
        # register it the way the task registers the one it mails.
        _rehash(survey, token)
        return survey, token

    def test_get_returns_context(self, client, closed_case):
        _, token = self._seed(closed_case)
        resp = client.get(f"/api/public/csat/{token}/")
        assert resp.status_code == 200
        assert resp.json()["case_subject"] == "Login bug"
        assert resp.json()["rating"] is None

    @pytest.mark.parametrize(
        "name, shown",
        [
            ("Dana Agent", "Dana Agent"),
            ("", "Org A support"),
            ("dana@agents.example", "Org A support"),
        ],
    )
    def test_agent_name_is_never_an_email_address(
        self, client, closed_case, admin_profile, name, shown
    ):
        """Anyone holding the link reads this, so the agent's login address
        must not be in it, even when their display name is blank or is one."""
        closed_case.org.name = "Org A"
        closed_case.org.save()
        user = admin_profile.user
        user.name = name
        user.save()
        closed_case.assigned_to.add(admin_profile)
        _, token = self._seed(closed_case)
        body = client.get(f"/api/public/csat/{token}/").json()
        assert body["agent_name"] == shown
        assert user.email not in str(body)

    def test_unassigned_case_names_the_org(self, client, closed_case):
        closed_case.org.name = "Org A"
        closed_case.org.save()
        _, token = self._seed(closed_case)
        body = client.get(f"/api/public/csat/{token}/").json()
        assert body["agent_name"] == "Org A support"

    def test_invalid_token_400(self, client):
        resp = client.get("/api/public/csat/garbage/")
        assert resp.status_code == 400

    def test_unknown_token_410(self, client):
        # Valid signature but no survey row.
        token = csat_signer().sign("ghost")
        resp = client.get(f"/api/public/csat/{token}/")
        assert resp.status_code == 410

    def test_expired_survey_410(self, client, closed_case):
        survey, token = self._seed(closed_case)
        survey.expires_at = timezone.now() - timedelta(days=1)
        survey.save(update_fields=["expires_at"])
        resp = client.get(f"/api/public/csat/{token}/")
        assert resp.status_code == 410


# --------------------------------------------------------------------------
# Public POST


class TestPublicCsatPost:
    def _seed(self, case):
        send_csat_survey(str(case.id), str(case.org_id))
        survey = CsatSurvey.objects.get(case=case)
        token = csat_signer().sign(str(case.id))
        _rehash(survey, token)
        return survey, token

    def test_first_submit_records(self, client, closed_case):
        _, token = self._seed(closed_case)
        resp = client.post(
            f"/api/public/csat/{token}/",
            data={"rating": 5, "comment": "Helpful!"},
            content_type="application/json",
        )
        assert resp.status_code == 200
        survey = CsatSurvey.objects.get(case=closed_case)
        assert survey.rating == 5
        assert survey.comment == "Helpful!"
        assert survey.responded_at is not None

    def test_rating_out_of_range_rejected(self, client, closed_case):
        _, token = self._seed(closed_case)
        for bad in (0, 6, "abc", None):
            resp = client.post(
                f"/api/public/csat/{token}/",
                data={"rating": bad},
                content_type="application/json",
            )
            assert resp.status_code == 400

    def test_edit_within_24h_allowed(self, client, closed_case):
        _, token = self._seed(closed_case)
        client.post(
            f"/api/public/csat/{token}/",
            data={"rating": 3, "comment": "ok"},
            content_type="application/json",
        )
        # Second submission within window updates.
        resp = client.post(
            f"/api/public/csat/{token}/",
            data={"rating": 5, "comment": "actually great"},
            content_type="application/json",
        )
        assert resp.status_code == 200
        assert CsatSurvey.objects.get(case=closed_case).rating == 5

    def test_edit_after_24h_locked(self, client, closed_case):
        survey, token = self._seed(closed_case)
        # First submit: rewind responded_at past the edit window.
        client.post(
            f"/api/public/csat/{token}/",
            data={"rating": 3},
            content_type="application/json",
        )
        survey.refresh_from_db()
        survey.responded_at = timezone.now() - timedelta(hours=25)
        survey.save(update_fields=["responded_at"])
        resp = client.post(
            f"/api/public/csat/{token}/",
            data={"rating": 5},
            content_type="application/json",
        )
        assert resp.status_code == 409


# --------------------------------------------------------------------------
# Aggregate


class TestCsatAggregate:
    def test_empty_org(self, admin_client):
        resp = admin_client.get("/api/cases/csat/aggregate/")
        assert resp.status_code == 200
        assert resp.data["count"] == 0
        assert resp.data["average"] is None

    def test_average_and_distribution(self, admin_client, org_a, contact_with_email):
        # Three responded surveys.
        for i, rating in enumerate((5, 4, 4)):
            case = Case.objects.create(
                org=org_a,
                name=f"C{i}",
                status="Closed",
                priority="Normal",
                closed_on=timezone.localdate(),
            )
            CsatSurvey.objects.create(
                org=org_a,
                case=case,
                contact=contact_with_email,
                token_hash=hash_csat_token(f"t{i}"),
                sent_at=timezone.now(),
                expires_at=timezone.now() + timedelta(days=30),
                rating=rating,
                responded_at=timezone.now(),
            )
        # One un-responded survey, must not count.
        case = Case.objects.create(
            org=org_a,
            name="Cx",
            status="Closed",
            priority="Normal",
            closed_on=timezone.localdate(),
        )
        CsatSurvey.objects.create(
            org=org_a,
            case=case,
            contact=contact_with_email,
            token_hash=hash_csat_token("tx"),
            sent_at=timezone.now(),
            expires_at=timezone.now() + timedelta(days=30),
        )
        resp = admin_client.get("/api/cases/csat/aggregate/")
        assert resp.data["count"] == 3
        assert resp.data["average"] == pytest.approx(4.333, rel=1e-2)
        assert resp.data["distribution"]["5"] == 1
        assert resp.data["distribution"]["4"] == 2

    def test_unauthenticated_blocked(self, unauthenticated_client):
        resp = unauthenticated_client.get("/api/cases/csat/aggregate/")
        assert resp.status_code in (401, 403)


class TestCsatAggregateScope:
    """The aggregate follows the analytics endpoints' visibility and window."""

    URL = "/api/cases/csat/aggregate/"

    def _rated(self, org, creator, rating, *, days_ago=1, priority="Normal"):
        case = Case.objects.create(
            org=org,
            name=f"Rated {rating}",
            status="Closed",
            priority=priority,
            closed_on=timezone.localdate(),
            created_by=creator,
        )
        answered = timezone.now() - timedelta(days=days_ago)
        CsatSurvey.objects.create(
            org=org,
            case=case,
            token_hash=hash_csat_token(f"scope-{case.id}"),
            sent_at=answered,
            expires_at=answered + timedelta(days=30),
            rating=rating,
            responded_at=answered,
        )
        return case

    def test_member_sees_only_ratings_on_tickets_they_may_open(
        self, user_client, admin_client, org_a, admin_user, regular_user
    ):
        self._rated(org_a, admin_user, 1)
        self._rated(org_a, regular_user, 5)
        mine = user_client.get(self.URL).json()
        assert (mine["count"], mine["average"]) == (1, 5.0)
        assert mine["distribution"]["1"] == 0
        everyone = admin_client.get(self.URL).json()
        assert everyone["count"] == 2

    def test_window_is_when_the_customer_answered(
        self, admin_client, org_a, admin_user
    ):
        self._rated(org_a, admin_user, 4, days_ago=40)
        self._rated(org_a, admin_user, 2, days_ago=1)
        default = admin_client.get(self.URL).json()
        assert (default["count"], default["average"]) == (1, 2.0)
        wide = (timezone.localdate() - timedelta(days=60)).isoformat()
        widened = admin_client.get(self.URL, {"from": wide}).json()
        assert widened["count"] == 2

    def test_priority_filter_narrows(self, admin_client, org_a, admin_user):
        self._rated(org_a, admin_user, 5, priority="Urgent")
        self._rated(org_a, admin_user, 1, priority="Low")
        body = admin_client.get(self.URL, {"priority": "Urgent"}).json()
        assert (body["count"], body["average"]) == (1, 5.0)

    def test_malformed_window_is_refused(self, admin_client):
        assert admin_client.get(self.URL, {"from": "banana"}).status_code == 400

    def test_another_orgs_ratings_never_count(
        self, admin_client, org_b, user_b, admin_user, org_a
    ):
        from conftest import rls_org

        with rls_org(org_b):
            self._rated(org_b, user_b, 1)
        self._rated(org_a, admin_user, 5)
        body = admin_client.get(self.URL).json()
        assert (body["count"], body["average"]) == (1, 5.0)


class TestPublicCsatResolvesOrgFirst:
    """The anonymous survey view sets the org's RLS context before it reads
    `csat_survey`. Under the non-superuser production role an empty context
    hides every survey row, so reading first answered 410 to every customer."""

    def test_rls_context_is_set_before_the_survey_is_read(
        self, client, closed_case, monkeypatch
    ):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        survey, token = TestPublicCsatGet()._seed(closed_case)
        calls = []
        with CaptureQueriesContext(connection) as ctx:

            def record(org_id):
                calls.append(
                    (
                        str(org_id),
                        any("csat_survey" in q["sql"] for q in ctx.captured_queries),
                    )
                )

            monkeypatch.setattr("cases.csat_views.set_rls_context", record)
            resp = client.get(f"/api/public/csat/{token}/")
            read_survey = any("csat_survey" in q["sql"] for q in ctx.captured_queries)

        assert resp.status_code == 200, resp.content
        assert calls[0] == (str(closed_case.org_id), False)
        assert read_survey

    def test_a_token_with_no_registered_org_is_gone(self, client, closed_case):
        survey, token = TestPublicCsatGet()._seed(closed_case)
        from common.models import PortalAccessToken

        PortalAccessToken.objects.filter(token_hash=survey.token_hash).delete()
        assert client.get(f"/api/public/csat/{token}/").status_code == 410
        resp = client.post(
            f"/api/public/csat/{token}/",
            data={"rating": 5},
            content_type="application/json",
        )
        assert resp.status_code == 410
        survey.refresh_from_db()
        assert survey.rating is None

    def test_a_token_registered_to_another_org_finds_nothing(
        self, client, closed_case, org_b
    ):
        """The survey is read only inside the resolved org."""
        survey, token = TestPublicCsatGet()._seed(closed_case)
        register_portal_token_hash(survey.token_hash, org_b.id, "csat", survey.id)
        assert client.get(f"/api/public/csat/{token}/").status_code == 410


@pytest.mark.postgres_only
def test_public_survey_answers_under_enforced_rls(client):
    """The anonymous survey GET and POST work under a role that enforces RLS.

    Runs only against PostgreSQL as a non-superuser (``RLS_ENFORCE=1
    --ds=crm.test_settings_postgres``): a superuser bypasses RLS, and the first
    assertion below says so rather than passing vacuously.
    """
    from django.db import connection

    from common.models import Org

    if connection.vendor != "postgresql":
        pytest.skip("RLS requires PostgreSQL")

    def context(value):
        with connection.cursor() as cursor:
            cursor.execute("SELECT set_config('app.current_org', %s, false)", [value])

    org = Org.objects.create(name="RLS CSAT Org")
    context(str(org.id))
    try:
        case = Case.objects.create(
            org=org,
            name="Survey under RLS",
            status="Closed",
            priority="Normal",
            closed_on=timezone.localdate(),
        )
        token = csat_signer().sign(str(case.id))
        survey = CsatSurvey.objects.create(
            org=org,
            case=case,
            token_hash=hash_csat_token(token),
            sent_at=timezone.now(),
            expires_at=timezone.now() + timedelta(days=30),
        )
        register_portal_token_hash(survey.token_hash, org.id, "csat", survey.id)
    finally:
        context("")

    # The anonymous request's starting point: the row is invisible.
    assert not CsatSurvey.objects.filter(pk=survey.pk).exists()

    assert client.get(f"/api/public/csat/{token}/").status_code == 200
    resp = client.post(
        f"/api/public/csat/{token}/",
        data={"rating": 4},
        content_type="application/json",
    )
    assert resp.status_code == 200, resp.content
    context(str(org.id))
    try:
        assert CsatSurvey.objects.get(pk=survey.pk).rating == 4
    finally:
        context("")


# --------------------------------------------------------------------------
# Analytics integration: csat_avg flows into compute_agents


class TestCsatFeedsAnalytics:
    def test_csat_avg_in_agents_payload(self, org_a, admin_profile, contact_with_email):
        from datetime import datetime
        from datetime import timezone as dt_timezone

        case = Case.objects.create(
            org=org_a,
            name="A",
            status="Closed",
            priority="Normal",
            closed_on=timezone.localdate(),
        )
        case.assigned_to.add(admin_profile)
        Case.objects.filter(pk=case.pk).update(
            created_at=datetime(2026, 5, 1, 8, tzinfo=dt_timezone.utc),
            first_response_at=datetime(2026, 5, 1, 9, tzinfo=dt_timezone.utc),
            resolved_at=datetime(2026, 5, 1, 10, tzinfo=dt_timezone.utc),
        )
        CsatSurvey.objects.create(
            org=org_a,
            case=case,
            contact=contact_with_email,
            token_hash=hash_csat_token("k1"),
            sent_at=timezone.now(),
            expires_at=timezone.now() + timedelta(days=30),
            rating=5,
            responded_at=timezone.now(),
        )
        rows = analytics.compute_agents(
            Case.objects.filter(org=org_a),
            from_dt=datetime(2026, 5, 1, tzinfo=dt_timezone.utc),
            to_dt=datetime(2026, 5, 2, tzinfo=dt_timezone.utc),
        )
        assert len(rows) == 1
        assert rows[0]["csat_avg"] == 5.0


# --------------------------------------------------------------------------
# Enqueued at commit, not at save


class TestCsatEnqueuedAfterCommit:
    """The survey is scheduled once the close commits, so a close that rolls
    back never surveys anybody about a ticket that is still open."""

    def _open_case(self, org_a, contact_with_email):
        case = Case.objects.create(
            org=org_a, name="Login bug", status="Assigned", priority="Normal"
        )
        case.contacts.add(contact_with_email)
        return case

    def test_a_committed_close_schedules_the_survey(
        self, org_a, contact_with_email, django_capture_on_commit_callbacks
    ):
        from unittest.mock import patch

        case = self._open_case(org_a, contact_with_email)
        with patch("cases.tasks.send_csat_survey.apply_async") as schedule:
            with django_capture_on_commit_callbacks(execute=True):
                case.status = "Closed"
                case.save()
        schedule.assert_called_once()
        assert schedule.call_args.kwargs["args"] == [str(case.id), str(org_a.id)]

    def test_a_rolled_back_close_schedules_nothing(
        self, org_a, contact_with_email, django_capture_on_commit_callbacks
    ):
        from unittest.mock import patch

        from django.db import transaction

        case = self._open_case(org_a, contact_with_email)
        with patch("cases.tasks.send_csat_survey.apply_async") as schedule:
            with django_capture_on_commit_callbacks(execute=True):
                with pytest.raises(RuntimeError):
                    with transaction.atomic():
                        case.status = "Closed"
                        case.save()
                        raise RuntimeError("the request failed after the save")
        schedule.assert_not_called()
