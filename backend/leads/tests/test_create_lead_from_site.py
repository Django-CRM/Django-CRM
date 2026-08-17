"""The legacy web-to-lead endpoint, `/api/leads/create-from-site/`.

This endpoint has existed for years with no test coverage at all, and carried
six defects because of it. The tests below pin each one.

REACHABILITY IS DELIBERATELY UNCHANGED. It still needs a JWT or an org API key,
exactly as before, which is why these tests use an authenticated client. Making
it anonymously reachable is impossible without either carving `apiSettings` out
of RLS or changing its URL, and the new `/api/public/forms/` endpoint is the
answer to that rather than weakening either. See
`docs/superpowers/specs/2026-08-16-web-forms-design.md`.
"""

import pytest
from django.core import mail

from common.models import APISettings, Tags
from common.utils import LEAD_SOURCE
from contacts.models import Contact
from leads.models import Lead
from webforms.models import WebForm

URL = "/api/leads/create-from-site/"


@pytest.fixture
def api_setting(org_a, admin_user, admin_profile, user_profile):
    """A legacy API key with a backing form, as the backfill migration makes.

    `lead_assigned_to` carries a DIFFERENT profile from `created_by`, because
    that is the only shape in which defect 4 is visible: the old view notified
    `created_by` and never read this M2M at all.
    """
    setting = APISettings.objects.create(
        title="Marketing site",
        website="https://example.com",
        org=org_a,
        created_by=admin_user,
    )
    setting.lead_assigned_to.add(user_profile)
    tag = Tags.objects.create(name="inbound", slug="inbound", org=org_a)
    setting.tags.add(tag)

    form = WebForm.objects.create(
        name=setting.title,
        org=org_a,
        is_published=True,
        assign_to=user_profile,
        lead_source="other",
        legacy_api_setting=setting,
        created_by=admin_user,
    )
    form.notify_profiles.add(user_profile)
    form.tags.add(tag)
    from webforms.models import WebFormField

    for order, name in enumerate(
        ["salutation", "first_name", "last_name", "email", "phone", "description"]
    ):
        WebFormField.objects.create(
            form=form,
            org=org_a,
            order=order,
            source=WebFormField.SOURCE_LEAD,
            lead_field=name,
            label=name.replace("_", " ").title(),
            is_required=(name == "email"),
        )
    return setting


def payload(setting, **overrides):
    body = {
        "apikey": setting.apikey,
        "title": "Ms",
        "first_name": "Pat",
        "last_name": "Prospect",
        "email": "pat@example.com",
        "phone": "1234567890",
        "message": "Please call me.",
    }
    body.update(overrides)
    return body


@pytest.mark.django_db
class TestHappyPath:
    def test_a_valid_apikey_creates_a_lead(self, admin_client, org_a, api_setting):
        response = admin_client.post(URL, payload(api_setting), format="json")
        assert response.status_code == 200, response.data
        lead = Lead.objects.get(org=org_a)
        assert lead.email == "pat@example.com"
        assert lead.first_name == "Pat"

    def test_the_legacy_title_param_maps_to_salutation_not_to_lead_title(
        self, admin_client, org_a, api_setting
    ):
        """`Lead.title` is the subject line, `Lead.salutation` the honorific.
        The legacy request parameter named `title` has always meant the
        second, and a migration that got this wrong would write honorifics
        into every lead's subject."""
        admin_client.post(URL, payload(api_setting), format="json")
        lead = Lead.objects.get(org=org_a)
        assert lead.salutation == "Ms"
        assert lead.title in (None, "")

    def test_the_legacy_message_param_maps_to_description(
        self, admin_client, org_a, api_setting
    ):
        admin_client.post(URL, payload(api_setting), format="json")
        assert Lead.objects.get(org=org_a).description == "Please call me."

    def test_the_response_body_is_unchanged(self, admin_client, api_setting):
        """A deprecated endpoint's contract is the one thing not to tidy.
        The spelling of "sucessfully" is part of it."""
        response = admin_client.post(URL, payload(api_setting), format="json")
        assert response.data == {
            "error": False,
            "message": "Lead Created sucessfully.",
        }


@pytest.mark.django_db
class TestRejection:
    def test_an_unknown_apikey_is_403(self, admin_client, api_setting):
        response = admin_client.post(
            URL, payload(api_setting, apikey="not-a-real-key"), format="json"
        )
        assert response.status_code == 403
        assert Lead.objects.count() == 0

    def test_a_missing_email_is_400(self, admin_client, api_setting):
        body = payload(api_setting)
        del body["email"]
        response = admin_client.post(URL, body, format="json")
        assert response.status_code == 400
        assert Lead.objects.count() == 0


@pytest.mark.django_db
class TestDefect3DuplicateEmail:
    def test_a_repeat_address_does_not_raise(self, admin_client, org_a, api_setting):
        """`Lead` carries UniqueConstraint(Lower("email"), "org") and the old
        view called `Lead.objects.create()` directly, so the second time
        anyone filled in the form it raised IntegrityError."""
        first = admin_client.post(URL, payload(api_setting), format="json")
        second = admin_client.post(URL, payload(api_setting), format="json")
        assert first.status_code == 200
        assert second.status_code == 200
        assert Lead.objects.filter(org=org_a).count() == 1

    def test_a_repeat_fills_a_blank_field_without_overwriting_a_set_one(
        self, admin_client, org_a, api_setting
    ):
        admin_client.post(
            URL, payload(api_setting, first_name="Pat", phone=""), format="json"
        )
        admin_client.post(
            URL,
            payload(api_setting, first_name="Impostor", phone="9999999999"),
            format="json",
        )
        lead = Lead.objects.get(org=org_a)
        assert lead.first_name == "Pat"
        assert lead.phone == "9999999999"


@pytest.mark.django_db
class TestDefect4Notification:
    def test_the_configured_recipients_are_notified(
        self, admin_client, org_a, api_setting, user_profile
    ):
        """`APISettings.lead_assigned_to` was never read by the old view,
        which notified `created_by` instead. For the whole life of the
        feature, the people configured to be told were told nothing.

        The view enqueues rather than sends, so this asserts on the delivered
        email by running the task the way a worker would. Asserting on
        `mail.outbox` straight after the request would pass vacuously: there
        is no eager mode in the test settings, so nothing sends inline and an
        empty outbox would satisfy a weaker assertion.
        """
        from webforms.models import WebFormSubmission
        from webforms.tasks import send_webform_submission_email

        admin_client.post(URL, payload(api_setting), format="json")
        submission = WebFormSubmission.objects.get()

        mail.outbox.clear()
        send_webform_submission_email(str(submission.id), str(org_a.id))
        recipients = {r for message in mail.outbox for r in message.recipients()}
        assert user_profile.user.email in recipients

    def test_the_view_enqueues_the_notification(
        self, admin_client, api_setting, monkeypatch
    ):
        """The seam itself: a view that forgets to enqueue is a lead nobody
        hears about, and the test above would still pass."""
        calls = []
        monkeypatch.setattr(
            "leads.views.lead_interactions.send_webform_submission_email.delay",
            lambda *args: calls.append(args),
        )
        admin_client.post(URL, payload(api_setting), format="json")
        assert len(calls) == 1


@pytest.mark.django_db
class TestDefect5Tags:
    def test_the_configured_tags_reach_the_lead(self, admin_client, org_a, api_setting):
        admin_client.post(URL, payload(api_setting), format="json")
        lead = Lead.objects.get(org=org_a)
        assert [tag.name for tag in lead.tags.all()] == ["inbound"]


@pytest.mark.django_db
class TestDefect7Validation:
    def test_a_malformed_email_is_400_rather_than_a_database_error(
        self, admin_client, api_setting
    ):
        response = admin_client.post(
            URL, payload(api_setting, email="not-an-email"), format="json"
        )
        assert response.status_code == 400
        assert Lead.objects.count() == 0

    def test_an_over_length_value_is_400(self, admin_client, api_setting):
        response = admin_client.post(
            URL, payload(api_setting, first_name="x" * 300), format="json"
        )
        assert response.status_code == 400
        assert Lead.objects.count() == 0


@pytest.mark.django_db
class TestDefect11LeadSource:
    def test_source_is_a_valid_choice_and_not_the_website_url(
        self, admin_client, org_a, api_setting
    ):
        """`Lead.source` declares choices=LEAD_SOURCE. The old view assigned
        `api_setting.website`, a URL, so every lead this endpoint created was
        invisible to every source-based filter and report."""
        admin_client.post(URL, payload(api_setting), format="json")
        lead = Lead.objects.get(org=org_a)
        assert lead.source in dict(LEAD_SOURCE)
        assert lead.source != api_setting.website


@pytest.mark.django_db
class TestContactBehaviourPreserved:
    def test_a_contact_is_still_created_and_linked(
        self, admin_client, org_a, api_setting
    ):
        """Preserved deliberately. Removing it would be a behaviour change on
        a deprecated endpoint that existing integrations may depend on."""
        admin_client.post(URL, payload(api_setting), format="json")
        lead = Lead.objects.get(org=org_a)
        assert lead.contacts.count() == 1
        assert lead.contacts.first().email == "pat@example.com"


@pytest.mark.django_db
class TestTenantIsolation:
    def test_another_orgs_key_is_refused_outright(
        self, org_b_client, org_a, api_setting
    ):
        """A member of one org holding another org's key gets nothing.

        This used to answer 200 and file the lead into the key's org, on the
        reasoning that the key names the tenant so the caller's JWT cannot
        redirect the write. True as far as it went, but it was only ever the
        observable behaviour on SQLite: in production the lookup runs under the
        caller's own RLS context, where a stranger's `apiSettings` row does not
        exist, so the endpoint already answered 403. The lookup is now filtered
        on `request.org` explicitly, which makes the two agree and means a
        leaked key is inert in the hands of anyone outside its org.
        """
        response = org_b_client.post(URL, payload(api_setting), format="json")
        assert response.status_code == 403
        assert not Lead.objects.exists()

    def test_the_lead_lands_in_the_api_keys_org(self, admin_client, org_a, api_setting):
        """The org is still taken from the key rather than the JWT, so the
        guard above cannot be mistaken for the caller's org deciding."""
        response = admin_client.post(URL, payload(api_setting), format="json")
        assert response.status_code == 200
        assert Lead.objects.get().org_id == org_a.id


@pytest.mark.django_db
class TestContactAttachmentWithoutAnEmail:
    """A submission with no address gets no contact, rather than somebody's.

    `get_or_create(org=..., email=None)` does not create a blank contact: it
    MATCHES the first contact in the org whose email is null and links that
    person's record to this lead. Reachable as soon as an admin makes the email
    field optional on a legacy form, which the editor allows.
    """

    def test_an_unrelated_null_email_contact_is_not_linked(
        self, admin_client, org_a, admin_user, api_setting
    ):
        stranger = Contact.objects.create(
            first_name="Someone",
            last_name="Else",
            email=None,
            org=org_a,
            created_by=admin_user,
        )
        api_setting.web_form.fields.filter(lead_field="email").update(is_required=False)

        response = admin_client.post(
            URL, {"apikey": api_setting.apikey, "first_name": "Anon"}, format="json"
        )

        assert response.status_code == 200
        lead = Lead.objects.get()
        assert stranger not in lead.contacts.all()
        assert lead.contacts.count() == 0
