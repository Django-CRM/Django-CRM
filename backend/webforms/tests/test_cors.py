"""Cross-origin permission for the script embed.

Handled through django-cors-headers' own extension point rather than a bespoke
middleware, so the project keeps one CORS implementation. The important
assertions here are the negative ones: this must widen nothing outside the
submit route, and must never become a blanket allow.
"""

import uuid

import pytest
from django.test import RequestFactory

from webforms.cors import allow_webform_origin
from webforms.models import WebForm


@pytest.fixture
def form(org_a):
    return WebForm.objects.create(
        name="Contact us",
        org=org_a,
        is_published=True,
        allowed_origins=["https://example.com"],
    )


def _request(path, origin=None):
    extra = {"HTTP_ORIGIN": origin} if origin is not None else {}
    return RequestFactory().post(path, **extra)


def submit_path(org, form):
    return f"/api/public/forms/{org.id}/{form.id}/submit/"


@pytest.mark.django_db
class TestAllowed:
    def test_a_listed_origin_on_the_submit_route_is_allowed(self, org_a, form):
        request = _request(submit_path(org_a, form), "https://example.com")
        assert allow_webform_origin(None, request) is True

    def test_one_of_several_listed_origins_is_allowed(self, org_a, form):
        form.allowed_origins = ["https://a.example", "https://b.example"]
        form.save(update_fields=["allowed_origins"])
        request = _request(submit_path(org_a, form), "https://b.example")
        assert allow_webform_origin(None, request) is True


@pytest.mark.django_db
class TestRefused:
    def test_an_unlisted_origin_is_refused(self, org_a, form):
        request = _request(submit_path(org_a, form), "https://evil.example")
        assert allow_webform_origin(None, request) is False

    def test_a_scheme_mismatch_is_refused(self, org_a, form):
        """http and https are different origins. Matching loosely here would
        let a network attacker on a plaintext page post into the org."""
        request = _request(submit_path(org_a, form), "http://example.com")
        assert allow_webform_origin(None, request) is False

    def test_a_subdomain_of_a_listed_origin_is_refused(self, org_a, form):
        """Exact match only. Allowing any subdomain hands the form to whoever
        can register or take over one."""
        request = _request(submit_path(org_a, form), "https://evil.example.com")
        assert allow_webform_origin(None, request) is False

    def test_the_embed_route_is_refused_even_for_a_listed_origin(self, org_a, form):
        """Only /submit/ needs CORS. The embed routes are plain GETs of a
        document and a script, which CORS does not apply to."""
        request = _request(
            f"/api/public/forms/{org_a.id}/{form.id}/embed/", "https://example.com"
        )
        assert allow_webform_origin(None, request) is False

    def test_an_unrelated_api_path_is_refused(self, org_a, form):
        request = _request("/api/leads/", "https://example.com")
        assert allow_webform_origin(None, request) is False

    def test_an_unpublished_form_is_refused(self, org_a, form):
        form.is_published = False
        form.save(update_fields=["is_published"])
        request = _request(submit_path(org_a, form), "https://example.com")
        assert allow_webform_origin(None, request) is False

    def test_an_unknown_form_is_refused(self, org_a):
        request = _request(
            f"/api/public/forms/{org_a.id}/{uuid.uuid4()}/submit/",
            "https://example.com",
        )
        assert allow_webform_origin(None, request) is False

    def test_a_mismatched_org_is_refused(self, org_a, org_b, form):
        request = _request(submit_path(org_b, form), "https://example.com")
        assert allow_webform_origin(None, request) is False

    def test_a_form_with_no_listed_origins_is_refused(self, org_a):
        """An empty list means "any origin may POST", which the VIEW enforces.

        It must NOT mean "reflect any Origin header back as allowed": that
        would turn an unconfigured form into a blanket CORS hole on a
        production API. Such a form still works through the iframe embed
        (same-origin) and through a server-side POST, and the admin UI tells
        the org to list origins before using the script embed.
        """
        open_form = WebForm.objects.create(
            name="Open", org=org_a, is_published=True, allowed_origins=[]
        )
        request = _request(submit_path(org_a, open_form), "https://anywhere.example")
        assert allow_webform_origin(None, request) is False

    def test_a_request_with_no_origin_header_is_refused(self, org_a, form):
        assert allow_webform_origin(None, _request(submit_path(org_a, form))) is False

    def test_a_malformed_path_does_not_raise(self, org_a):
        request = _request(
            "/api/public/forms/not-a-uuid/also-not-a-uuid/submit/", "https://x.example"
        )
        assert allow_webform_origin(None, request) is False

    def test_a_path_that_merely_starts_the_same_is_refused(self, org_a, form):
        """The pattern is anchored at both ends. A route added later under a
        longer path must not inherit this permission by accident."""
        request = _request(
            f"/api/public/forms/{org_a.id}/{form.id}/submit/extra/",
            "https://example.com",
        )
        assert allow_webform_origin(None, request) is False


@pytest.mark.django_db
class TestSignalIsConnected:
    def test_dispatching_the_signal_reaches_our_receiver(self, org_a, form):
        """Connecting the signal is what makes every rule above take effect.
        A correct receiver that nobody calls is the same as no receiver.

        Dispatched through the real signal rather than inspected on
        `signal.receivers`, whose internal tuple shape is a Django private
        detail that has changed between versions.
        """
        from corsheaders.signals import check_request_enabled

        request = _request(submit_path(org_a, form), "https://example.com")
        results = check_request_enabled.send(sender=None, request=request)
        assert any(response for _, response in results), (
            "No receiver returned True. The signal is probably not connected "
            "in WebFormsConfig.ready()."
        )

    def test_dispatching_the_signal_refuses_an_unlisted_origin(self, org_a, form):
        from corsheaders.signals import check_request_enabled

        request = _request(submit_path(org_a, form), "https://evil.example")
        results = check_request_enabled.send(sender=None, request=request)
        assert not any(response for _, response in results)
