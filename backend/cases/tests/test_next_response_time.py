"""Next-response time: the wait after a customer writes back (G7).

`cases.analytics._next_response_waits` pairs each customer message that
follows the first agent reply with the next public agent reply. These tests
pin the definition with explicit timestamps, the breach rule against the
org's next-response target, the SLA breach count, the endpoint's visibility
rule and the policy field that sets the target.
"""

from __future__ import annotations

from datetime import timedelta
from itertools import count

import pytest
from django.contrib.contenttypes.models import ContentType
from django.utils import timezone

from cases import analytics
from cases.models import Case, CaseWatcher, EmailMessage, EscalationPolicy
from common.models import Comment

NOW = timezone.now().replace(microsecond=0)
T0 = NOW - timedelta(days=5)
FROM = NOW - timedelta(days=10)
TO = NOW + timedelta(hours=1)


def _h(hours):
    return T0 + timedelta(hours=hours)


def _case(org, creator, *, status="Assigned", priority="Normal", name="Printer"):
    case = Case.objects.create(
        name=name, status=status, priority=priority, org=org, created_by=creator
    )
    Case.objects.filter(pk=case.pk).update(created_at=T0)
    return case


def _comment(case, at, *, by=None, internal=False):
    comment = Comment.objects.create(
        org=case.org,
        content_type=ContentType.objects.get_for_model(Case),
        object_id=case.id,
        comment="text",
        commented_by=by,
        is_internal=internal,
    )
    Comment.objects.filter(pk=comment.pk).update(created_at=at)
    return comment


_message_ids = count(1)


def _email(case, at):
    return EmailMessage.objects.create(
        org=case.org,
        case=case,
        direction="inbound",
        message_id=f"<nrt-{next(_message_ids)}@example.com>",
        from_address="customer@example.com",
        received_at=at,
    )


def _nrt(org):
    return analytics.compute_nrt(Case.objects.filter(org=org), FROM, TO, org_id=org.id)


def _normal(result):
    return next(r for r in result["by_priority"] if r["priority"] == "Normal")


class TestDefinition:
    def test_customer_reply_then_agent_reply_is_one_wait(
        self, org_a, admin_user, admin_profile
    ):
        case = _case(org_a, admin_user)
        _comment(case, _h(1), by=admin_profile)  # first response
        _comment(case, _h(2))  # the customer writes back
        _comment(case, _h(5), by=admin_profile)  # the next reply
        result = _nrt(org_a)
        assert result["count"] == 1
        assert result["median_hours"] == pytest.approx(3.0)
        assert result["case_ids"] == [str(case.id)]

    def test_internal_notes_are_ignored(self, org_a, admin_user, admin_profile):
        case = _case(org_a, admin_user)
        _comment(case, _h(1), by=admin_profile)
        _comment(case, _h(2))
        # A note to the team is not an answer to the customer.
        _comment(case, _h(3), by=admin_profile, internal=True)
        _comment(case, _h(6), by=admin_profile)
        result = _nrt(org_a)
        assert result["count"] == 1
        assert result["median_hours"] == pytest.approx(4.0)

    def test_consecutive_customer_messages_time_from_the_first_unanswered(
        self, org_a, admin_user, admin_profile
    ):
        case = _case(org_a, admin_user)
        _comment(case, _h(1), by=admin_profile)
        _comment(case, _h(2))
        _comment(case, _h(3))
        _email(case, _h(4))
        _comment(case, _h(6), by=admin_profile)
        result = _nrt(org_a)
        assert result["count"] == 1
        assert result["median_hours"] == pytest.approx(4.0)

    def test_agent_reply_with_nothing_to_answer_is_not_counted(
        self, org_a, admin_user, admin_profile
    ):
        case = _case(org_a, admin_user)
        _comment(case, _h(1), by=admin_profile)
        _comment(case, _h(2), by=admin_profile)
        _comment(case, _h(3), by=admin_profile)
        assert _nrt(org_a)["count"] == 0

    def test_messages_before_the_first_reply_belong_to_first_response(
        self, org_a, admin_user, admin_profile
    ):
        """The wait before the first reply is FRT's, not NRT's, including the
        email that opened an inbound ticket."""
        case = _case(org_a, admin_user)
        _email(case, _h(0))
        _comment(case, _h(1))
        _comment(case, _h(4), by=admin_profile)
        assert _nrt(org_a)["count"] == 0

    def test_inbound_email_is_a_customer_message(
        self, org_a, admin_user, admin_profile
    ):
        case = _case(org_a, admin_user)
        _comment(case, _h(1), by=admin_profile)
        _email(case, _h(2))
        _comment(case, _h(4), by=admin_profile)
        result = _nrt(org_a)
        assert result["count"] == 1
        assert result["median_hours"] == pytest.approx(2.0)

    def test_dropped_email_is_not_a_customer_message(
        self, org_a, admin_user, admin_profile
    ):
        case = _case(org_a, admin_user)
        _comment(case, _h(1), by=admin_profile)
        email = _email(case, _h(2))
        EmailMessage.objects.filter(pk=email.pk).update(drop_reason="auto_reply")
        _comment(case, _h(4), by=admin_profile)
        assert _nrt(org_a)["count"] == 0

    def test_two_separate_waits_are_two_samples(self, org_a, admin_user, admin_profile):
        case = _case(org_a, admin_user)
        _comment(case, _h(1), by=admin_profile)
        _comment(case, _h(2))
        _comment(case, _h(3), by=admin_profile)  # 1h
        _comment(case, _h(10))
        _comment(case, _h(13), by=admin_profile)  # 3h
        result = _nrt(org_a)
        assert result["count"] == 2
        assert result["median_hours"] == pytest.approx(2.0)

    def test_window_scopes_by_when_the_wait_began(
        self, org_a, admin_user, admin_profile
    ):
        case = _case(org_a, admin_user)
        _comment(case, _h(1), by=admin_profile)
        _comment(case, _h(2))
        _comment(case, _h(4), by=admin_profile)
        later = analytics.compute_nrt(
            Case.objects.filter(org=org_a), _h(3), TO, org_id=org_a.id
        )
        assert later["count"] == 0


class TestBreaches:
    def test_late_answer_breaches_the_default_target(
        self, org_a, admin_user, admin_profile
    ):
        case = _case(org_a, admin_user)  # Normal: default 8h
        _comment(case, _h(1), by=admin_profile)
        _comment(case, _h(2))
        _comment(case, _h(12), by=admin_profile)  # 10h
        result = _nrt(org_a)
        assert result["breach_count"] == 1
        assert result["breach_case_ids"] == [str(case.id)]
        normal = _normal(result)
        assert (normal["target_hours"], normal["met"], normal["missed"]) == (8, 0, 1)

    def test_the_org_policy_sets_the_target(self, org_a, admin_user, admin_profile):
        EscalationPolicy.objects.create(
            org=org_a, priority="Normal", next_response_hours=2
        )
        case = _case(org_a, admin_user)
        _comment(case, _h(1), by=admin_profile)
        _comment(case, _h(2))
        _comment(case, _h(5), by=admin_profile)  # 3h, inside 8h, past 2h
        result = _nrt(org_a)
        assert result["breach_count"] == 1
        assert _normal(result)["target_hours"] == 2

    def test_an_inactive_policy_falls_back_to_the_default(
        self, org_a, admin_user, admin_profile
    ):
        EscalationPolicy.objects.create(
            org=org_a, priority="Normal", next_response_hours=2, is_active=False
        )
        case = _case(org_a, admin_user)
        _comment(case, _h(1), by=admin_profile)
        _comment(case, _h(2))
        _comment(case, _h(5), by=admin_profile)
        result = _nrt(org_a)
        assert result["breach_count"] == 0
        assert _normal(result)["met"] == 1

    def test_unanswered_past_target_on_an_open_ticket_breaches(
        self, org_a, admin_user, admin_profile
    ):
        case = _case(org_a, admin_user)
        _comment(case, _h(1), by=admin_profile)
        _comment(case, NOW - timedelta(hours=20))
        result = _nrt(org_a)
        assert result["count"] == 0  # no answered wait to time
        assert result["breach_count"] == 1

    def test_unanswered_last_word_on_a_closed_ticket_is_not_owed(
        self, org_a, admin_user, admin_profile
    ):
        case = _case(org_a, admin_user, status="Closed")
        _comment(case, _h(1), by=admin_profile)
        _comment(case, NOW - timedelta(hours=20))  # "thanks!"
        assert _nrt(org_a)["breach_count"] == 0

    def test_sla_counts_next_response_breaches_per_case(
        self, org_a, admin_user, admin_profile
    ):
        late = _case(org_a, admin_user, name="Late")
        _comment(late, _h(1), by=admin_profile)
        _comment(late, _h(2))
        _comment(late, _h(12), by=admin_profile)
        on_time = _case(org_a, admin_user, name="On time")
        _comment(on_time, _h(1), by=admin_profile)
        _comment(on_time, _h(2))
        _comment(on_time, _h(3), by=admin_profile)
        sla = analytics.compute_sla(Case.objects.filter(org=org_a), FROM, TO)
        assert sla["nrt_breach_count"] == 1
        assert sla["nrt_breach_rate"] == pytest.approx(0.5)
        assert sla["nrt_breach_case_ids"] == [str(late.id)]
        assert sla["by_priority"]["Normal"]["nrt_breach_rate"] == pytest.approx(0.5)


class TestEndpointVisibility:
    URL = "/api/cases/analytics/nrt/"

    def _answered(self, case, agent):
        _comment(case, _h(1), by=agent)
        _comment(case, _h(2))
        _comment(case, _h(4), by=agent)

    def test_admin_sees_every_ticket_in_the_org(
        self, admin_client, org_a, admin_user, admin_profile, regular_user
    ):
        self._answered(_case(org_a, admin_user, name="Admin's"), admin_profile)
        self._answered(_case(org_a, regular_user, name="Member's"), admin_profile)
        body = admin_client.get(self.URL, {"from": FROM.date().isoformat()}).json()
        assert body["count"] == 2

    def test_member_sees_only_tickets_they_may_open(
        self, user_client, org_a, admin_user, admin_profile, regular_user
    ):
        hidden = _case(org_a, admin_user, name="Not theirs")
        self._answered(hidden, admin_profile)
        mine = _case(org_a, regular_user, name="Theirs")
        self._answered(mine, admin_profile)
        body = user_client.get(self.URL, {"from": FROM.date().isoformat()}).json()
        assert body["count"] == 1
        assert body["case_ids"] == [str(mine.id)]

    def test_a_watched_ticket_is_visible_to_the_watcher(
        self, user_client, org_a, admin_user, admin_profile, user_profile
    ):
        watched = _case(org_a, admin_user)
        CaseWatcher.objects.create(case=watched, profile=user_profile, org=org_a)
        self._answered(watched, admin_profile)
        body = user_client.get(self.URL, {"from": FROM.date().isoformat()}).json()
        assert body["case_ids"] == [str(watched.id)]

    def test_malformed_window_is_refused(self, admin_client):
        assert admin_client.get(self.URL, {"from": "banana"}).status_code == 400

    def test_unauthenticated_is_refused(self, unauthenticated_client):
        assert unauthenticated_client.get(self.URL).status_code in (401, 403)


class TestPolicyField:
    URL = "/api/cases/escalation-policies/"

    def test_admin_sets_and_clears_the_target(self, admin_client, org_a):
        created = admin_client.post(
            self.URL,
            {"priority": "High", "next_response_hours": 3},
            format="json",
        )
        assert created.status_code == 201, created.content
        assert created.json()["next_response_hours"] == 3
        pk = created.json()["id"]
        cleared = admin_client.put(
            f"{self.URL}{pk}/", {"next_response_hours": None}, format="json"
        )
        assert cleared.status_code == 200, cleared.content
        assert EscalationPolicy.objects.get(pk=pk).next_response_hours is None

    @pytest.mark.parametrize("bad", [0, 8761, "soon"])
    def test_out_of_range_is_refused(self, admin_client, bad):
        response = admin_client.post(
            self.URL, {"priority": "Low", "next_response_hours": bad}, format="json"
        )
        assert response.status_code == 400
        assert "next_response_hours" in response.json()["errors"]

    def test_a_member_cannot_set_it(self, user_client):
        response = user_client.post(
            self.URL, {"priority": "Low", "next_response_hours": 3}, format="json"
        )
        assert response.status_code == 403
        assert not EscalationPolicy.objects.exists()
