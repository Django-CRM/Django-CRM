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
        # per `signer.sign` non-determinism, so update for the test).
        survey.token_hash = hash_csat_token(token)
        survey.save(update_fields=["token_hash"])
        return survey, token

    def test_get_returns_context(self, client, closed_case):
        _, token = self._seed(closed_case)
        resp = client.get(f"/api/public/csat/{token}/")
        assert resp.status_code == 200
        assert resp.json()["case_subject"] == "Login bug"
        assert resp.json()["rating"] is None

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
        survey.token_hash = hash_csat_token(token)
        survey.save(update_fields=["token_hash"])
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
