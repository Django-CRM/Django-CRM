"""Views, submissions and conversion for one form.

Two numbers and a ratio, but each has a way of being quietly wrong: a sparse
series that makes a chart lie about its shape, a conversion rate that divides
by zero on a brand new form, and spam counted as a conversion.
"""

import datetime

import pytest
from django.utils import timezone

from webforms.models import WebForm, WebFormDailyStat, WebFormField, WebFormSubmission
from webforms.service import submit_form


def analytics_url(form):
    return f"/api/webforms/{form.id}/analytics/"


@pytest.fixture
def form(org_a, admin_profile):
    web_form = WebForm.objects.create(
        name="Contact us", org=org_a, is_published=True, assign_to=admin_profile
    )
    WebFormField.objects.create(
        form=web_form,
        org=org_a,
        order=0,
        source=WebFormField.SOURCE_LEAD,
        lead_field="email",
        label="Email",
        is_required=True,
    )
    return web_form


@pytest.mark.django_db
class TestShape:
    def test_the_series_covers_a_full_thirty_days(self, admin_client, form):
        response = admin_client.get(analytics_url(form))
        assert response.status_code == 200
        assert response.data["window_days"] == 30
        assert len(response.data["series"]) == 30

    def test_days_with_no_activity_are_zero_filled(self, admin_client, form):
        """A sparse series makes a chart lie about its shape: three points
        drawn evenly apart look like three consecutive days."""
        response = admin_client.get(analytics_url(form))
        assert all(day["views"] == 0 for day in response.data["series"])
        assert all(day["submissions"] == 0 for day in response.data["series"])

    def test_the_series_ends_today(self, admin_client, form):
        response = admin_client.get(analytics_url(form))
        assert response.data["series"][-1]["date"] == timezone.localdate().isoformat()


@pytest.mark.django_db
class TestCounts:
    def test_views_are_counted(self, admin_client, org_a, form):
        WebFormDailyStat.objects.create(
            form=form, org=org_a, date=timezone.localdate(), views=7
        )
        response = admin_client.get(analytics_url(form))
        assert response.data["totals"]["views"] == 7

    def test_accepted_submissions_are_counted(self, admin_client, form):
        submit_form(form, {"email": "pat@example.com"})
        response = admin_client.get(analytics_url(form))
        assert response.data["totals"]["submissions"] == 1

    def test_a_merged_duplicate_still_counts_as_a_submission(self, admin_client, form):
        submit_form(form, {"email": "pat@example.com"})
        submit_form(form, {"email": "pat@example.com"})
        response = admin_client.get(analytics_url(form))
        assert response.data["totals"]["submissions"] == 2

    def test_spam_is_counted_separately_and_not_as_a_conversion(
        self, admin_client, form
    ):
        submit_form(
            form, {}, rejected=WebFormSubmission.REJECTED_SPAM, reason="honeypot"
        )
        response = admin_client.get(analytics_url(form))
        assert response.data["totals"]["spam"] == 1
        assert response.data["totals"]["submissions"] == 0

    def test_invalid_submissions_are_not_counted_as_conversions(
        self, admin_client, form
    ):
        submit_form(form, {}, rejected=WebFormSubmission.REJECTED_INVALID, reason="bad")
        response = admin_client.get(analytics_url(form))
        assert response.data["totals"]["submissions"] == 0

    def test_another_forms_activity_is_not_counted(self, admin_client, org_a, form):
        other = WebForm.objects.create(name="Other", org=org_a, is_published=True)
        WebFormDailyStat.objects.create(
            form=other, org=org_a, date=timezone.localdate(), views=99
        )
        response = admin_client.get(analytics_url(form))
        assert response.data["totals"]["views"] == 0

    def test_activity_older_than_the_window_is_excluded(
        self, admin_client, org_a, form
    ):
        WebFormDailyStat.objects.create(
            form=form,
            org=org_a,
            date=timezone.localdate() - datetime.timedelta(days=60),
            views=99,
        )
        response = admin_client.get(analytics_url(form))
        assert response.data["totals"]["views"] == 0


@pytest.mark.django_db
class TestConversionRate:
    def test_zero_views_is_zero_not_a_crash(self, admin_client, form):
        """A brand new form has no views, and this is the first thing its page
        renders."""
        response = admin_client.get(analytics_url(form))
        assert response.status_code == 200
        assert response.data["totals"]["conversion_rate"] == 0

    def test_the_rate_is_submissions_over_views(self, admin_client, org_a, form):
        WebFormDailyStat.objects.create(
            form=form, org=org_a, date=timezone.localdate(), views=4
        )
        submit_form(form, {"email": "pat@example.com"})
        response = admin_client.get(analytics_url(form))
        assert response.data["totals"]["conversion_rate"] == 0.25


@pytest.mark.django_db
class TestAuthorization:
    def test_a_member_can_read_analytics(self, user_client, form):
        assert user_client.get(analytics_url(form)).status_code == 200

    def test_another_org_gets_404(self, org_b_client, form):
        assert org_b_client.get(analytics_url(form)).status_code == 404

    def test_an_unauthenticated_caller_is_refused(self, unauthenticated_client, form):
        assert unauthenticated_client.get(analytics_url(form)).status_code in (401, 403)
