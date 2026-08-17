"""The anonymous submit endpoint.

Every test here posts with no credentials at all, because that is the only way
a website visitor ever arrives. The first test is the one that would have
caught the defect in the legacy endpoint: it has never been reachable
anonymously, and nothing asserted that it was.
"""

import uuid

import pytest
from django.core.cache import cache

from leads.models import Lead
from webforms.dynamic_serializer import HONEYPOT_FIELD
from webforms.models import WebForm, WebFormField, WebFormSubmission
from webforms.throttles import WebFormGlobalThrottle, WebFormIPThrottle


def submit_url(org, form):
    return f"/api/public/forms/{org.id}/{form.id}/submit/"


@pytest.fixture(autouse=True)
def _clear_throttle_cache():
    """Throttle counters live in the cache and outlive a test otherwise.

    Without this, the tests that submit several times leak their counters into
    whatever runs next and the failure looks unrelated to its cause.
    """
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def form(org_a, admin_profile):
    web_form = WebForm.objects.create(
        name="Contact us",
        org=org_a,
        is_published=True,
        assign_to=admin_profile,
        lead_source="other",
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
    WebFormField.objects.create(
        form=web_form,
        org=org_a,
        order=1,
        source=WebFormField.SOURCE_LEAD,
        lead_field="first_name",
        label="First name",
    )
    return web_form


@pytest.mark.django_db
class TestHappyPath:
    def test_an_anonymous_post_creates_a_lead(
        self, unauthenticated_client, org_a, form
    ):
        response = unauthenticated_client.post(
            submit_url(org_a, form),
            {"email": "pat@example.com", "first_name": "Pat"},
            format="json",
        )
        assert response.status_code == 200, response.data
        assert Lead.objects.filter(org=org_a, email="pat@example.com").exists()

    def test_the_response_carries_the_success_message(
        self, unauthenticated_client, org_a, form
    ):
        form.success_mode = WebForm.SUCCESS_MESSAGE
        form.success_message = "Thanks, we will call you."
        form.save()
        response = unauthenticated_client.post(
            submit_url(org_a, form), {"email": "pat@example.com"}, format="json"
        )
        assert response.data["mode"] == "message"
        assert response.data["message"] == "Thanks, we will call you."

    def test_the_response_carries_the_redirect_url(
        self, unauthenticated_client, org_a, form
    ):
        form.success_mode = WebForm.SUCCESS_REDIRECT
        form.redirect_url = "https://example.com/thanks"
        form.save()
        response = unauthenticated_client.post(
            submit_url(org_a, form), {"email": "pat@example.com"}, format="json"
        )
        assert response.data["mode"] == "redirect"
        assert response.data["redirect_url"] == "https://example.com/thanks"

    def test_the_submission_is_recorded(self, unauthenticated_client, org_a, form):
        unauthenticated_client.post(
            submit_url(org_a, form), {"email": "pat@example.com"}, format="json"
        )
        submission = WebFormSubmission.objects.get()
        assert submission.status == WebFormSubmission.ACCEPTED
        assert submission.form_id == form.id
        assert submission.org_id == org_a.id


@pytest.mark.django_db
class TestNotFound:
    def test_an_unpublished_form_is_404(self, unauthenticated_client, org_a, form):
        form.is_published = False
        form.save(update_fields=["is_published"])
        response = unauthenticated_client.post(
            submit_url(org_a, form), {"email": "pat@example.com"}, format="json"
        )
        assert response.status_code == 404
        assert Lead.objects.count() == 0

    def test_another_orgs_form_is_404(self, unauthenticated_client, org_b, form):
        """Same form id, wrong org in the path. Answering 404 rather than 403
        keeps the id space from confirming that a form exists."""
        response = unauthenticated_client.post(
            submit_url(org_b, form), {"email": "pat@example.com"}, format="json"
        )
        assert response.status_code == 404
        assert Lead.objects.count() == 0

    def test_an_unknown_form_id_is_404(self, unauthenticated_client, org_a):
        response = unauthenticated_client.post(
            f"/api/public/forms/{org_a.id}/{uuid.uuid4()}/submit/",
            {"email": "pat@example.com"},
            format="json",
        )
        assert response.status_code == 404

    def test_an_unknown_org_id_is_404(self, unauthenticated_client, form):
        response = unauthenticated_client.post(
            f"/api/public/forms/{uuid.uuid4()}/{form.id}/submit/",
            {"email": "pat@example.com"},
            format="json",
        )
        assert response.status_code == 404


@pytest.mark.django_db
class TestSpamControls:
    def test_a_filled_honeypot_looks_like_success_and_writes_no_lead(
        self, unauthenticated_client, org_a, form
    ):
        response = unauthenticated_client.post(
            submit_url(org_a, form),
            {"email": "bot@example.com", HONEYPOT_FIELD: "http://spam.example"},
            format="json",
        )
        assert response.status_code == 200
        assert Lead.objects.count() == 0
        submission = WebFormSubmission.objects.get()
        assert submission.status == WebFormSubmission.REJECTED_SPAM
        assert submission.lead is None

    def test_the_reject_reason_is_never_returned_to_the_submitter(
        self, unauthenticated_client, org_a, form
    ):
        response = unauthenticated_client.post(
            submit_url(org_a, form),
            {"email": "bot@example.com", HONEYPOT_FIELD: "x"},
            format="json",
        )
        assert "honeypot" not in str(response.data).lower()
        assert "reject_reason" not in response.data

    def test_a_honeypot_rejection_is_byte_identical_to_a_success(
        self, unauthenticated_client, org_a, form
    ):
        """A bot that can tell it was caught retries differently."""
        good = unauthenticated_client.post(
            submit_url(org_a, form), {"email": "pat@example.com"}, format="json"
        )
        bot = unauthenticated_client.post(
            submit_url(org_a, form),
            {"email": "bot@example.com", HONEYPOT_FIELD: "x"},
            format="json",
        )
        assert good.status_code == bot.status_code
        assert good.data == bot.data

    def test_a_disallowed_origin_is_403(self, unauthenticated_client, org_a, form):
        form.allowed_origins = ["https://example.com"]
        form.save(update_fields=["allowed_origins"])
        response = unauthenticated_client.post(
            submit_url(org_a, form),
            {"email": "pat@example.com"},
            format="json",
            HTTP_ORIGIN="https://evil.example",
        )
        assert response.status_code == 403
        assert Lead.objects.count() == 0

    def test_an_allowed_origin_passes(self, unauthenticated_client, org_a, form):
        form.allowed_origins = ["https://example.com"]
        form.save(update_fields=["allowed_origins"])
        response = unauthenticated_client.post(
            submit_url(org_a, form),
            {"email": "pat@example.com"},
            format="json",
            HTTP_ORIGIN="https://example.com",
        )
        assert response.status_code == 200

    def test_our_own_origin_is_allowed_even_when_it_is_not_listed(
        self, unauthenticated_client, org_a, form
    ):
        """The iframe embed is a document WE serve, so its fetch is
        same-origin and carries the API's own origin, which is never in a
        customer's `allowed_origins` list.

        Found in a browser, not in a unit test: listing an origin for the
        script embed silently 403'd every iframe submission, because the two
        embeds have different origins for the same form.
        """
        form.allowed_origins = ["https://example.com"]
        form.save(update_fields=["allowed_origins"])
        response = unauthenticated_client.post(
            submit_url(org_a, form),
            {"email": "pat@example.com"},
            format="json",
            HTTP_ORIGIN="http://testserver",
        )
        assert response.status_code == 200, response.data

    def test_an_empty_allowed_origins_list_permits_any_origin(
        self, unauthenticated_client, org_a, form
    ):
        response = unauthenticated_client.post(
            submit_url(org_a, form),
            {"email": "pat@example.com"},
            format="json",
            HTTP_ORIGIN="https://anywhere.example",
        )
        assert response.status_code == 200

    def test_a_failed_captcha_is_400_and_writes_no_lead(
        self, unauthenticated_client, org_a, form, monkeypatch
    ):
        monkeypatch.setattr(
            "webforms.public_views.captcha.verify", lambda *a, **k: False
        )
        form.captcha_provider = WebForm.CAPTCHA_TURNSTILE
        form.captcha_secret = "s"
        form.save()
        response = unauthenticated_client.post(
            submit_url(org_a, form), {"email": "pat@example.com"}, format="json"
        )
        assert response.status_code == 400
        assert Lead.objects.count() == 0
        assert WebFormSubmission.objects.get().status == WebFormSubmission.REJECTED_SPAM

    def test_a_passing_captcha_creates_the_lead(
        self, unauthenticated_client, org_a, form, monkeypatch
    ):
        monkeypatch.setattr(
            "webforms.public_views.captcha.verify", lambda *a, **k: True
        )
        form.captcha_provider = WebForm.CAPTCHA_TURNSTILE
        form.captcha_secret = "s"
        form.save()
        response = unauthenticated_client.post(
            submit_url(org_a, form), {"email": "pat@example.com"}, format="json"
        )
        assert response.status_code == 200
        assert Lead.objects.count() == 1


@pytest.fixture
def tight_rates(monkeypatch):
    """Force low limits onto the two throttle classes.

    Overriding `settings.REST_FRAMEWORK` does NOT work here.
    `SimpleRateThrottle.THROTTLE_RATES` is bound to `api_settings
    .DEFAULT_THROTTLE_RATES` at class-definition time (see
    `rest_framework/throttling.py:66`), so it still points at the dict built
    when the module was first imported. Patching the bound attribute is what
    actually reaches `get_rate`.
    """
    rates = {"webform_submit_ip": "2/hour", "webform_submit_global": "3/day"}
    monkeypatch.setattr(WebFormIPThrottle, "THROTTLE_RATES", rates)
    monkeypatch.setattr(WebFormGlobalThrottle, "THROTTLE_RATES", rates)
    cache.clear()
    return rates


@pytest.mark.django_db
class TestThrottling:
    def test_the_per_ip_limit_returns_429(
        self, unauthenticated_client, org_a, form, tight_rates
    ):
        for index in range(2):
            response = unauthenticated_client.post(
                submit_url(org_a, form),
                {"email": f"pat{index}@example.com"},
                format="json",
            )
            assert response.status_code == 200
        response = unauthenticated_client.post(
            submit_url(org_a, form), {"email": "third@example.com"}, format="json"
        )
        assert response.status_code == 429

    def test_the_limit_is_per_form_not_per_org(
        self, unauthenticated_client, org_a, form, admin_profile, tight_rates
    ):
        """Exhausting one org's form must not lock a visitor out of another
        form they have never touched."""
        other = WebForm.objects.create(
            name="Request a demo",
            org=org_a,
            is_published=True,
            assign_to=admin_profile,
        )
        WebFormField.objects.create(
            form=other,
            org=org_a,
            order=0,
            source=WebFormField.SOURCE_LEAD,
            lead_field="email",
            label="Email",
            is_required=True,
        )
        for index in range(3):
            unauthenticated_client.post(
                submit_url(org_a, form),
                {"email": f"pat{index}@example.com"},
                format="json",
            )
        response = unauthenticated_client.post(
            submit_url(org_a, other), {"email": "fresh@example.com"}, format="json"
        )
        assert response.status_code == 200

    def test_rotating_the_forwarded_header_does_not_evade_the_global_limit(
        self, unauthenticated_client, org_a, form, tight_rates
    ):
        """X-Forwarded-For is submitter-controlled, so the per-IP bucket is
        trivially reset by an attacker. The global cap is the layer that
        rotation cannot evade, and this is the test that says so."""
        for index in range(3):
            response = unauthenticated_client.post(
                submit_url(org_a, form),
                {"email": f"pat{index}@example.com"},
                format="json",
                HTTP_X_FORWARDED_FOR=f"203.0.113.{index}",
            )
            assert response.status_code == 200
        response = unauthenticated_client.post(
            submit_url(org_a, form),
            {"email": "fourth@example.com"},
            format="json",
            HTTP_X_FORWARDED_FOR="203.0.113.99",
        )
        assert response.status_code == 429


@pytest.mark.django_db
class TestValidation:
    def test_a_missing_required_field_is_400(self, unauthenticated_client, org_a, form):
        response = unauthenticated_client.post(
            submit_url(org_a, form), {"first_name": "Pat"}, format="json"
        )
        assert response.status_code == 400
        assert Lead.objects.count() == 0

    def test_a_malformed_email_is_400(self, unauthenticated_client, org_a, form):
        response = unauthenticated_client.post(
            submit_url(org_a, form), {"email": "not-an-email"}, format="json"
        )
        assert response.status_code == 400

    def test_an_invalid_submission_is_recorded(
        self, unauthenticated_client, org_a, form
    ):
        unauthenticated_client.post(submit_url(org_a, form), {}, format="json")
        submission = WebFormSubmission.objects.get()
        assert submission.status == WebFormSubmission.REJECTED_INVALID

    def test_a_repeat_address_does_not_raise(self, unauthenticated_client, org_a, form):
        """`Lead` has UniqueConstraint(Lower("email"), "org"). This is the
        second time anyone fills in the form, not an edge case."""
        for _ in range(2):
            response = unauthenticated_client.post(
                submit_url(org_a, form), {"email": "pat@example.com"}, format="json"
            )
            assert response.status_code == 200
        assert Lead.objects.filter(org=org_a).count() == 1


@pytest.mark.django_db
class TestServerDerivedFields:
    """Identity, org, ownership and lifecycle are the server's, never the
    submitter's. Each of these posts a hostile value and asserts it was
    ignored rather than honoured."""

    def _post(self, client, org, form, extra):
        return client.post(
            submit_url(org, form),
            {"email": "pat@example.com", **extra},
            format="json",
        )

    def test_org_in_the_body_is_ignored(
        self, unauthenticated_client, org_a, org_b, form
    ):
        self._post(unauthenticated_client, org_a, form, {"org": str(org_b.id)})
        assert Lead.objects.get().org_id == org_a.id

    def test_status_in_the_body_is_ignored(self, unauthenticated_client, org_a, form):
        self._post(unauthenticated_client, org_a, form, {"status": "converted"})
        assert Lead.objects.get().status == "assigned"

    def test_source_in_the_body_is_ignored(self, unauthenticated_client, org_a, form):
        self._post(unauthenticated_client, org_a, form, {"source": "partner"})
        assert Lead.objects.get().source == "other"

    def test_created_by_in_the_body_is_ignored(
        self, unauthenticated_client, org_a, form, user_b
    ):
        self._post(unauthenticated_client, org_a, form, {"created_by": str(user_b.id)})
        assert Lead.objects.get().created_by_id != user_b.id

    def test_tags_in_the_body_are_ignored(self, unauthenticated_client, org_a, form):
        self._post(unauthenticated_client, org_a, form, {"tags": ["vip"]})
        assert Lead.objects.get().tags.count() == 0

    def test_opportunity_amount_in_the_body_is_ignored(
        self, unauthenticated_client, org_a, form
    ):
        """A column the form does not collect must not be writable through it."""
        self._post(
            unauthenticated_client, org_a, form, {"opportunity_amount": "999999"}
        )
        assert Lead.objects.get().opportunity_amount is None

    def test_is_active_in_the_body_is_ignored(
        self, unauthenticated_client, org_a, form
    ):
        self._post(unauthenticated_client, org_a, form, {"is_active": False})
        assert Lead.objects.get().is_active is True

    def test_the_stored_payload_holds_only_validated_keys(
        self, unauthenticated_client, org_a, form
    ):
        """Storing raw request.data would let a submitter persist an arbitrary
        blob that later renders in an admin's browser."""
        self._post(
            unauthenticated_client,
            org_a,
            form,
            {"first_name": "Pat", "<script>": "alert(1)"},
        )
        payload = WebFormSubmission.objects.get().payload
        assert set(payload) <= {"email", "first_name", "custom_fields"}
