"""The iframe and script embeds.

The X-Frame-Options assertion is the important one. The project runs
XFrameOptionsMiddleware, so without an explicit exemption the iframe embed is
blocked by our own middleware and the feature simply does not work on any
customer site. That failure is invisible to a test that only checks the status
code.
"""

import datetime

import pytest

from webforms.models import WebForm, WebFormDailyStat, WebFormField


@pytest.fixture
def form(org_a):
    web_form = WebForm.objects.create(
        name="Contact us", org=org_a, is_published=True, submit_button_label="Send"
    )
    WebFormField.objects.create(
        form=web_form,
        org=org_a,
        order=0,
        source=WebFormField.SOURCE_LEAD,
        lead_field="email",
        label="Your email",
        is_required=True,
    )
    WebFormField.objects.create(
        form=web_form,
        org=org_a,
        order=1,
        source=WebFormField.SOURCE_LEAD,
        lead_field="description",
        label="How can we help",
    )
    return web_form


def embed_url(org, form):
    return f"/api/public/forms/{org.id}/{form.id}/embed/"


def embed_js_url(org, form):
    return f"/api/public/forms/{org.id}/{form.id}/embed.js"


@pytest.mark.django_db
class TestIframeEmbed:
    def test_renders_anonymously(self, unauthenticated_client, org_a, form):
        assert unauthenticated_client.get(embed_url(org_a, form)).status_code == 200

    def test_is_not_blocked_by_our_own_x_frame_options(
        self, unauthenticated_client, org_a, form
    ):
        """XFrameOptionsMiddleware is in MIDDLEWARE. Without
        @xframe_options_exempt this response carries X-Frame-Options and every
        customer iframe shows an empty box."""
        response = unauthenticated_client.get(embed_url(org_a, form))
        assert "X-Frame-Options" not in response

    def test_renders_the_forms_fields_and_button_label(
        self, unauthenticated_client, org_a, form
    ):
        body = unauthenticated_client.get(embed_url(org_a, form)).content.decode()
        assert "Your email" in body
        assert "How can we help" in body
        assert "Send" in body

    def test_a_message_field_renders_as_a_textarea(
        self, unauthenticated_client, org_a, form
    ):
        body = unauthenticated_client.get(embed_url(org_a, form)).content.decode()
        assert "<textarea" in body

    def test_sets_frame_ancestors_when_origins_are_configured(
        self, unauthenticated_client, org_a, form
    ):
        form.allowed_origins = ["https://example.com"]
        form.save(update_fields=["allowed_origins"])
        response = unauthenticated_client.get(embed_url(org_a, form))
        assert (
            "frame-ancestors https://example.com" in response["Content-Security-Policy"]
        )

    def test_omits_frame_ancestors_when_unrestricted(
        self, unauthenticated_client, org_a, form
    ):
        response = unauthenticated_client.get(embed_url(org_a, form))
        assert "Content-Security-Policy" not in response

    def test_carries_the_absolute_submit_url(self, unauthenticated_client, org_a, form):
        body = unauthenticated_client.get(embed_url(org_a, form)).content.decode()
        assert f"/api/public/forms/{org_a.id}/{form.id}/submit/" in body

    def test_an_unpublished_form_is_404(self, unauthenticated_client, org_a, form):
        form.is_published = False
        form.save(update_fields=["is_published"])
        assert unauthenticated_client.get(embed_url(org_a, form)).status_code == 404

    def test_another_orgs_form_is_404(self, unauthenticated_client, org_b, form):
        assert unauthenticated_client.get(embed_url(org_b, form)).status_code == 404

    def test_a_form_name_with_markup_is_escaped(
        self, unauthenticated_client, org_a, form
    ):
        """The name is admin-controlled rather than visitor-controlled, but it
        still reaches a page served on a customer's domain."""
        form.name = "<script>alert(1)</script>"
        form.save(update_fields=["name"])
        body = unauthenticated_client.get(embed_url(org_a, form)).content.decode()
        assert "<script>alert(1)</script>" not in body


@pytest.mark.django_db
class TestScriptEmbed:
    def test_is_served_as_javascript(self, unauthenticated_client, org_a, form):
        response = unauthenticated_client.get(embed_js_url(org_a, form))
        assert response.status_code == 200
        assert "javascript" in response["Content-Type"]

    def test_inlines_the_field_config(self, unauthenticated_client, org_a, form):
        body = unauthenticated_client.get(embed_js_url(org_a, form)).content.decode()
        assert "Your email" in body

    def test_carries_the_absolute_submit_url(self, unauthenticated_client, org_a, form):
        body = unauthenticated_client.get(embed_js_url(org_a, form)).content.decode()
        assert f"/api/public/forms/{org_a.id}/{form.id}/submit/" in body

    def test_an_unpublished_form_is_404(self, unauthenticated_client, org_a, form):
        form.is_published = False
        form.save(update_fields=["is_published"])
        assert unauthenticated_client.get(embed_js_url(org_a, form)).status_code == 404


@pytest.mark.django_db
class TestCaptchaSecretNeverLeaves:
    """Two tests rather than one, because the two templates are separate files
    and a leak added to either one is a leak."""

    def _configure(self, form):
        form.captcha_provider = WebForm.CAPTCHA_TURNSTILE
        form.captcha_site_key = "site-key-public"
        form.captcha_secret = "secret-never-send-this"
        form.save()

    def test_the_script_embed_sends_the_site_key_but_not_the_secret(
        self, unauthenticated_client, org_a, form
    ):
        self._configure(form)
        body = unauthenticated_client.get(embed_js_url(org_a, form)).content.decode()
        assert "site-key-public" in body
        assert "secret-never-send-this" not in body

    def test_the_iframe_embed_sends_the_site_key_but_not_the_secret(
        self, unauthenticated_client, org_a, form
    ):
        self._configure(form)
        body = unauthenticated_client.get(embed_url(org_a, form)).content.decode()
        assert "site-key-public" in body
        assert "secret-never-send-this" not in body


@pytest.mark.django_db
class TestViewCounting:
    def test_an_iframe_render_counts_a_view(self, unauthenticated_client, org_a, form):
        unauthenticated_client.get(embed_url(org_a, form))
        stat = WebFormDailyStat.objects.get(form=form)
        assert stat.views == 1
        assert stat.date == datetime.date.today()

    def test_repeat_renders_increment_the_same_day_row(
        self, unauthenticated_client, org_a, form
    ):
        for _ in range(3):
            unauthenticated_client.get(embed_url(org_a, form))
        assert WebFormDailyStat.objects.count() == 1
        assert WebFormDailyStat.objects.get(form=form).views == 3

    def test_a_script_render_counts_too(self, unauthenticated_client, org_a, form):
        unauthenticated_client.get(embed_js_url(org_a, form))
        assert WebFormDailyStat.objects.get(form=form).views == 1

    def test_the_stat_row_carries_the_forms_org(
        self, unauthenticated_client, org_a, form
    ):
        unauthenticated_client.get(embed_url(org_a, form))
        assert WebFormDailyStat.objects.get(form=form).org_id == org_a.id

    def test_a_404_does_not_count_a_view(self, unauthenticated_client, org_a, form):
        form.is_published = False
        form.save(update_fields=["is_published"])
        unauthenticated_client.get(embed_url(org_a, form))
        assert WebFormDailyStat.objects.count() == 0


@pytest.mark.django_db
class TestNonFieldErrorsAreVisible:
    """A refused submission has to say so.

    Both embeds used to render an error only when its JSON key matched one of
    the form's own inputs. Every refusal that is not about a single field
    answers `{"detail": "..."}` instead: the captcha ("Could not verify that
    you are human"), the throttle (429), an origin that is not on the list
    (403), and a form that has since been unpublished (404). None of those keys
    name an input, so nothing was drawn at all. The visitor saw the button
    re-enable and no explanation, which is indistinguishable from a broken
    form, and it happened on exactly the paths this feature exists to defend.
    """

    def test_the_iframe_embed_has_somewhere_to_put_one(
        self, unauthenticated_client, org_a, form
    ):
        html = unauthenticated_client.get(embed_url(org_a, form)).content.decode()
        assert 'id="wf-general"' in html

    def test_the_iframe_embed_routes_unmatched_keys_there(
        self, unauthenticated_client, org_a, form
    ):
        """Structural, because there is no JS runtime here. It pins that the
        script has a branch for a key with no matching input, which is the one
        that was missing."""
        html = unauthenticated_client.get(embed_url(org_a, form)).content.decode()
        assert "leftover.push" in html
        assert "general.hidden" in html

    def test_the_script_embed_has_somewhere_to_put_one(
        self, unauthenticated_client, org_a, form
    ):
        js = unauthenticated_client.get(embed_js_url(org_a, form)).content.decode()
        assert "general" in js
        assert "leftover.push" in js

    def test_both_embeds_say_something_when_the_network_fails(
        self, unauthenticated_client, org_a, form
    ):
        """The bare `catch` re-enabled the button and said nothing, so an
        offline visitor got the same silence as a throttled one."""
        html = unauthenticated_client.get(embed_url(org_a, form)).content.decode()
        js = unauthenticated_client.get(embed_js_url(org_a, form)).content.decode()
        assert "Could not reach the server" in html
        assert "Could not reach the server" in js
