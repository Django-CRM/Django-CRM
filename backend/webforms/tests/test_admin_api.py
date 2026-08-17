"""The authenticated management API.

Creating a form mints something that writes leads into the org anonymously, so
the write half is admin-only while the read half is open to every member. That
is the same split `common/views/settings_views.py` uses for API settings, and
the reason is the same.
"""

import pytest

from common.models import CustomFieldDefinition
from webforms.models import WebForm, WebFormField

LIST_URL = "/api/webforms/"


def detail_url(form):
    return f"/api/webforms/{form.id}/"


@pytest.fixture
def form(org_a):
    return WebForm.objects.create(name="Contact us", org=org_a)


def email_field(form, org):
    return WebFormField.objects.create(
        form=form,
        org=org,
        order=0,
        source=WebFormField.SOURCE_LEAD,
        lead_field="email",
        label="Email",
        is_required=True,
    )


@pytest.mark.django_db
class TestRead:
    def test_a_member_can_list(self, user_client, form):
        response = user_client.get(LIST_URL)
        assert response.status_code == 200
        assert len(response.data["results"]) == 1

    def test_a_member_can_read_the_detail(self, user_client, form):
        assert user_client.get(detail_url(form)).status_code == 200

    def test_another_orgs_form_is_not_listed(self, org_b_client, form):
        response = org_b_client.get(LIST_URL)
        assert response.data["results"] == []

    def test_another_orgs_form_detail_is_404(self, org_b_client, form):
        assert org_b_client.get(detail_url(form)).status_code == 404

    def test_an_unauthenticated_caller_is_refused(self, unauthenticated_client, form):
        assert unauthenticated_client.get(LIST_URL).status_code in (401, 403)

    def test_the_detail_carries_both_embed_snippets(self, admin_client, form):
        response = admin_client.get(detail_url(form))
        assert "<iframe" in response.data["embed_html"]
        assert "<script" in response.data["embed_js"]
        assert str(form.id) in response.data["embed_html"]
        assert str(form.id) in response.data["embed_js"]

    def test_the_embed_snippets_carry_an_absolute_url(self, admin_client, form):
        """Built server-side because they need the API's own base URL, which
        the browser does not have. A relative URL in a snippet pasted onto a
        customer's site would point at the customer's own server."""
        response = admin_client.get(detail_url(form))
        assert "http://testserver/api/public/forms/" in response.data["embed_html"]

    def test_the_captcha_secret_is_never_returned(self, admin_client, form):
        form.captcha_secret = "secret-value"
        form.save(update_fields=["captcha_secret"])
        response = admin_client.get(detail_url(form))
        assert "captcha_secret" not in response.data
        assert "secret-value" not in str(response.data)

    def test_the_list_carries_a_submission_count(self, admin_client, org_a, form):
        from webforms.service import submit_form

        email_field(form, org_a)
        submit_form(form, {"email": "pat@example.com"})
        response = admin_client.get(LIST_URL)
        assert response.data["results"][0]["submission_count"] == 1


@pytest.mark.django_db
class TestWriteIsAdminOnly:
    def test_a_member_cannot_create(self, user_client):
        response = user_client.post(LIST_URL, {"name": "Nope"}, format="json")
        assert response.status_code == 403
        assert WebForm.objects.count() == 0

    def test_an_admin_can_create(self, admin_client, org_a):
        response = admin_client.post(LIST_URL, {"name": "Contact us"}, format="json")
        assert response.status_code == 201
        assert WebForm.objects.get().org_id == org_a.id

    def test_a_member_cannot_update(self, user_client, form):
        response = user_client.put(
            detail_url(form), {"name": "Hijacked"}, format="json"
        )
        assert response.status_code == 403
        form.refresh_from_db()
        assert form.name == "Contact us"

    def test_a_member_cannot_delete(self, user_client, form):
        assert user_client.delete(detail_url(form)).status_code == 403
        assert WebForm.objects.filter(pk=form.pk).exists()

    def test_a_member_cannot_publish(self, user_client, form):
        assert user_client.post(f"{detail_url(form)}publish/").status_code == 403

    def test_a_member_cannot_unpublish(self, user_client, form):
        assert user_client.post(f"{detail_url(form)}unpublish/").status_code == 403

    def test_an_admin_cannot_write_to_another_orgs_form(self, org_b_client, form):
        response = org_b_client.put(detail_url(form), {"name": "X"}, format="json")
        assert response.status_code == 404

    def test_an_admin_cannot_publish_another_orgs_form(self, org_b_client, form):
        assert org_b_client.post(f"{detail_url(form)}publish/").status_code == 404


@pytest.mark.django_db
class TestServerDerivedFields:
    def test_org_in_the_body_is_ignored(self, admin_client, org_a, org_b):
        admin_client.post(
            LIST_URL, {"name": "Contact us", "org": str(org_b.id)}, format="json"
        )
        assert WebForm.objects.get().org_id == org_a.id

    def test_is_published_cannot_be_set_on_create(self, admin_client):
        admin_client.post(
            LIST_URL, {"name": "Contact us", "is_published": True}, format="json"
        )
        assert WebForm.objects.get().is_published is False

    def test_is_published_cannot_be_flipped_by_a_plain_update(self, admin_client, form):
        """Publishing validates the source state and the form's shape. A plain
        update must not be a second door onto the same transition."""
        admin_client.put(
            detail_url(form),
            {"name": "Contact us", "is_published": True},
            format="json",
        )
        form.refresh_from_db()
        assert form.is_published is False

    def test_created_by_in_the_body_is_ignored(self, admin_client, user_b):
        admin_client.post(
            LIST_URL,
            {"name": "Contact us", "created_by": str(user_b.id)},
            format="json",
        )
        assert WebForm.objects.get().created_by_id != user_b.id


@pytest.mark.django_db
class TestValidation:
    def test_a_redirect_url_with_a_javascript_scheme_is_rejected(
        self, admin_client, form
    ):
        """This value is handed to the embed, which navigates to it. A
        `javascript:` payload here would be an XSS on the customer's own
        site."""
        response = admin_client.put(
            detail_url(form),
            {
                "name": "Contact us",
                "success_mode": "redirect",
                "redirect_url": "javascript:alert(1)",
            },
            format="json",
        )
        assert response.status_code == 400

    def test_a_data_url_redirect_is_rejected(self, admin_client, form):
        response = admin_client.put(
            detail_url(form),
            {
                "name": "Contact us",
                "success_mode": "redirect",
                "redirect_url": "data:text/html,<script>alert(1)</script>",
            },
            format="json",
        )
        assert response.status_code == 400

    def test_an_https_redirect_is_accepted(self, admin_client, form):
        response = admin_client.put(
            detail_url(form),
            {
                "name": "Contact us",
                "success_mode": "redirect",
                "redirect_url": "https://example.com/thanks",
            },
            format="json",
        )
        assert response.status_code == 200, response.data

    def test_allowed_origins_must_be_a_list(self, admin_client, form):
        response = admin_client.put(
            detail_url(form),
            {"name": "Contact us", "allowed_origins": "https://example.com"},
            format="json",
        )
        assert response.status_code == 400

    def test_an_origin_with_a_path_is_rejected(self, admin_client, form):
        """An Origin header never has a path, so an entry carrying one can
        never match and would look configured while doing nothing."""
        response = admin_client.put(
            detail_url(form),
            {
                "name": "Contact us",
                "allowed_origins": ["https://example.com/contact"],
            },
            format="json",
        )
        assert response.status_code == 400

    def test_a_valid_origin_list_is_accepted(self, admin_client, form):
        response = admin_client.put(
            detail_url(form),
            {
                "name": "Contact us",
                "allowed_origins": ["https://example.com", "https://www.example.com"],
            },
            format="json",
        )
        assert response.status_code == 200, response.data


@pytest.mark.django_db
class TestPublishing:
    def test_publishing_a_form_with_an_email_field_succeeds(
        self, admin_client, org_a, form
    ):
        email_field(form, org_a)
        response = admin_client.post(f"{detail_url(form)}publish/")
        assert response.status_code == 200
        form.refresh_from_db()
        assert form.is_published is True

    def test_publishing_a_form_without_an_email_field_is_400(self, admin_client, form):
        """Email is the dedupe key that keeps Lead's unique constraint from
        producing a 500 on a repeat submission. A form that cannot collect one
        is not publishable."""
        response = admin_client.post(f"{detail_url(form)}publish/")
        assert response.status_code == 400
        form.refresh_from_db()
        assert form.is_published is False

    def test_publishing_an_already_published_form_is_400(
        self, admin_client, org_a, form
    ):
        """Validates the SOURCE state, not only the target. A caller who
        thinks the form is unpublished is a caller working from stale data."""
        email_field(form, org_a)
        admin_client.post(f"{detail_url(form)}publish/")
        assert admin_client.post(f"{detail_url(form)}publish/").status_code == 400

    def test_unpublishing_an_unpublished_form_is_400(self, admin_client, form):
        assert admin_client.post(f"{detail_url(form)}unpublish/").status_code == 400

    def test_unpublishing_a_published_form_succeeds(self, admin_client, org_a, form):
        email_field(form, org_a)
        admin_client.post(f"{detail_url(form)}publish/")
        response = admin_client.post(f"{detail_url(form)}unpublish/")
        assert response.status_code == 200
        form.refresh_from_db()
        assert form.is_published is False

    def test_a_redirect_form_needs_a_url_to_publish(self, admin_client, org_a, form):
        email_field(form, org_a)
        form.success_mode = WebForm.SUCCESS_REDIRECT
        form.redirect_url = ""
        form.save()
        assert admin_client.post(f"{detail_url(form)}publish/").status_code == 400


@pytest.mark.django_db
class TestFieldWrites:
    def test_fields_are_written_as_an_ordered_list(self, admin_client, form):
        response = admin_client.put(
            detail_url(form),
            {
                "name": "Contact us",
                "fields": [
                    {
                        "source": "lead",
                        "lead_field": "email",
                        "label": "Email",
                        "is_required": True,
                    },
                    {"source": "lead", "lead_field": "first_name", "label": "Name"},
                ],
            },
            format="json",
        )
        assert response.status_code == 200, response.data
        rows = list(form.fields.order_by("order"))
        assert [r.lead_field for r in rows] == ["email", "first_name"]
        assert [r.order for r in rows] == [0, 1]

    def test_order_comes_from_list_position_not_from_the_client(
        self, admin_client, form
    ):
        """A client that sends its own `order` must not be able to reorder
        someone else's form by lying about it."""
        admin_client.put(
            detail_url(form),
            {
                "name": "Contact us",
                "fields": [
                    {
                        "source": "lead",
                        "lead_field": "email",
                        "label": "Email",
                        "order": 99,
                    },
                    {
                        "source": "lead",
                        "lead_field": "first_name",
                        "label": "Name",
                        "order": 3,
                    },
                ],
            },
            format="json",
        )
        assert [r.order for r in form.fields.order_by("order")] == [0, 1]

    def test_reordering_replaces_the_whole_list_atomically(self, admin_client, form):
        payload = {
            "name": "Contact us",
            "fields": [
                {"source": "lead", "lead_field": "email", "label": "Email"},
                {"source": "lead", "lead_field": "first_name", "label": "Name"},
            ],
        }
        admin_client.put(detail_url(form), payload, format="json")
        payload["fields"].reverse()
        admin_client.put(detail_url(form), payload, format="json")
        assert [r.lead_field for r in form.fields.order_by("order")] == [
            "first_name",
            "email",
        ]
        assert form.fields.count() == 2

    def test_a_rejected_field_list_leaves_the_existing_fields_intact(
        self, admin_client, org_a, form
    ):
        """The write deletes and recreates, so a failure partway through must
        roll back rather than leave the form with no fields at all."""
        email_field(form, org_a)
        response = admin_client.put(
            detail_url(form),
            {
                "name": "Contact us",
                "fields": [
                    {"source": "lead", "lead_field": "not_a_column", "label": "X"}
                ],
            },
            format="json",
        )
        assert response.status_code == 400
        assert [r.lead_field for r in form.fields.all()] == ["email"]

    def test_a_lead_field_outside_the_whitelist_is_400(self, admin_client, form):
        response = admin_client.put(
            detail_url(form),
            {
                "name": "Contact us",
                "fields": [
                    {
                        "source": "lead",
                        "lead_field": "opportunity_amount",
                        "label": "Budget",
                    }
                ],
            },
            format="json",
        )
        assert response.status_code == 400

    def test_a_custom_field_from_another_org_is_400(self, admin_client, org_b, form):
        foreign = CustomFieldDefinition.objects.create(
            org=org_b,
            target_model="Lead",
            key="budget",
            label="B",
            field_type="number",
        )
        response = admin_client.put(
            detail_url(form),
            {
                "name": "Contact us",
                "fields": [
                    {
                        "source": "custom",
                        "custom_field": str(foreign.id),
                        "label": "Budget",
                    }
                ],
            },
            format="json",
        )
        assert response.status_code == 400

    def test_a_custom_field_targeting_a_non_lead_model_is_400(
        self, admin_client, org_a, form
    ):
        definition = CustomFieldDefinition.objects.create(
            org=org_a,
            target_model="Case",
            key="sev",
            label="S",
            field_type="text",
        )
        response = admin_client.put(
            detail_url(form),
            {
                "name": "Contact us",
                "fields": [
                    {
                        "source": "custom",
                        "custom_field": str(definition.id),
                        "label": "Severity",
                    }
                ],
            },
            format="json",
        )
        assert response.status_code == 400

    def test_a_lead_row_with_no_target_is_400(self, admin_client, form):
        response = admin_client.put(
            detail_url(form),
            {
                "name": "Contact us",
                "fields": [{"source": "lead", "lead_field": "", "label": "X"}],
            },
            format="json",
        )
        assert response.status_code == 400

    def test_a_row_naming_both_targets_is_400(self, admin_client, org_a, form):
        definition = CustomFieldDefinition.objects.create(
            org=org_a,
            target_model="Lead",
            key="budget",
            label="B",
            field_type="number",
        )
        response = admin_client.put(
            detail_url(form),
            {
                "name": "Contact us",
                "fields": [
                    {
                        "source": "lead",
                        "lead_field": "email",
                        "custom_field": str(definition.id),
                        "label": "Both",
                    }
                ],
            },
            format="json",
        )
        assert response.status_code == 400


@pytest.mark.django_db
class TestSubmissions:
    def _one_submission(self, form, org):
        from webforms.service import submit_form

        email_field(form, org)
        form.is_published = True
        form.save(update_fields=["is_published"])
        return submit_form(form, {"email": "pat@example.com"})

    def test_a_member_can_read_submissions(self, user_client, org_a, form):
        self._one_submission(form, org_a)
        response = user_client.get(f"{detail_url(form)}submissions/")
        assert response.status_code == 200
        assert len(response.data["results"]) == 1

    def test_another_org_cannot_read_submissions(self, org_b_client, org_a, form):
        self._one_submission(form, org_a)
        assert org_b_client.get(f"{detail_url(form)}submissions/").status_code == 404

    def test_rejected_submissions_are_included(self, admin_client, org_a, form):
        from webforms.models import WebFormSubmission
        from webforms.service import submit_form

        self._one_submission(form, org_a)
        submit_form(
            form, {}, rejected=WebFormSubmission.REJECTED_SPAM, reason="honeypot"
        )
        response = admin_client.get(f"{detail_url(form)}submissions/")
        statuses = {row["status"] for row in response.data["results"]}
        assert statuses == {"accepted", "rejected_spam"}


@pytest.mark.django_db
class TestListTotals:
    """The list carries org-wide totals, computed over every form.

    Both clients show "N published" beside the destination, and the list is
    paginated at ten. Counting the returned page would under-report the moment
    an org has an eleventh form, and a settings screen quietly showing the
    wrong count is worse than one showing none.
    """

    def _submission(self, form, org, email="pat@example.com", **kwargs):
        from webforms.service import submit_form

        return submit_form(form, {"email": email}, **kwargs)

    def test_the_totals_count_every_form_not_just_the_page(
        self, admin_client, org_a, form
    ):
        for index in range(12):
            WebForm.objects.create(name=f"Form {index}", org=org_a)
        response = admin_client.get(LIST_URL)
        assert len(response.data["results"]) == 10  # PAGE_SIZE
        assert response.data["totals"]["count"] == 13

    def test_published_counts_only_published_forms(self, admin_client, org_a, form):
        email_field(form, org_a)
        WebForm.objects.create(name="Draft", org=org_a)
        published = WebForm.objects.create(name="Live", org=org_a, is_published=True)
        assert published.is_published
        response = admin_client.get(LIST_URL)
        assert response.data["totals"]["count"] == 3
        assert response.data["totals"]["published"] == 1

    def test_the_submission_totals_split_accepted_from_spam(
        self, admin_client, org_a, form
    ):
        from webforms.models import WebFormSubmission

        email_field(form, org_a)
        self._submission(form, org_a, email="one@example.com")
        self._submission(form, org_a, email="two@example.com")
        self._submission(
            form, org_a, rejected=WebFormSubmission.REJECTED_SPAM, reason="honeypot"
        )
        totals = admin_client.get(LIST_URL).data["totals"]
        assert totals["submissions_30d"] == 2
        assert totals["spam_30d"] == 1

    def test_a_duplicate_counts_as_an_accepted_submission(
        self, admin_client, org_a, form
    ):
        """`accepted_duplicate` is a real lead reaching the org, it merged into
        an existing row rather than making a new one. Counting only `accepted`
        would make a form that mostly hears from returning visitors look dead."""
        email_field(form, org_a)
        self._submission(form, org_a)
        self._submission(form, org_a)
        assert admin_client.get(LIST_URL).data["totals"]["submissions_30d"] == 2

    def test_an_old_submission_is_outside_the_window(self, admin_client, org_a, form):
        import datetime

        from django.utils import timezone

        from webforms.models import WebFormSubmission

        email_field(form, org_a)
        self._submission(form, org_a)
        WebFormSubmission.objects.update(
            created_at=timezone.now() - datetime.timedelta(days=31)
        )
        assert admin_client.get(LIST_URL).data["totals"]["submissions_30d"] == 0

    def test_another_orgs_forms_are_not_counted(self, admin_client, org_a, org_b, form):
        WebForm.objects.create(name="Theirs", org=org_b, is_published=True)
        totals = admin_client.get(LIST_URL).data["totals"]
        assert totals["count"] == 1
        assert totals["published"] == 0


@pytest.mark.django_db
class TestCaptchaSecretVisibility:
    """The secret is never returned, but whether one exists has to be.

    Turnstile fails closed, so a form with a provider set and no secret rejects
    every submission. The page cannot warn about that, and cannot tell an admin
    whether the blank secret box means "none stored" or "one stored, hidden",
    unless the API says which. `has_captcha_secret` is that boolean and nothing
    more: it reveals existence, never the value.
    """

    def test_it_is_false_when_no_secret_is_stored(self, admin_client, form):
        response = admin_client.get(detail_url(form))
        assert response.data["has_captcha_secret"] is False

    def test_it_is_true_when_one_is_stored(self, admin_client, form):
        form.captcha_secret = "1x0000000000000000000000000000000AA"
        form.save(update_fields=["captcha_secret"])
        response = admin_client.get(detail_url(form))
        assert response.data["has_captcha_secret"] is True
        assert "1x0000000000000000000000000000000AA" not in str(response.data)

    def test_it_cannot_be_written(self, admin_client, form):
        """Read-only, so a caller cannot claim a secret exists. If it were
        writable the flag would drift from the column it describes."""
        admin_client.put(
            detail_url(form),
            {"name": "Contact us", "has_captcha_secret": True},
            format="json",
        )
        form.refresh_from_db()
        assert form.captcha_secret == ""
        assert admin_client.get(detail_url(form)).data["has_captcha_secret"] is False

    def test_an_update_that_omits_the_secret_keeps_the_stored_one(
        self, admin_client, form
    ):
        """The page cannot re-send a value it was never given. An update
        carrying every other field must not blank the secret as a side effect,
        or saving an unrelated setting would silently break the captcha."""
        form.captcha_provider = WebForm.CAPTCHA_TURNSTILE
        form.captcha_site_key = "site-key"
        form.captcha_secret = "stored-secret"
        form.save()
        response = admin_client.put(
            detail_url(form), {"submit_button_label": "Send"}, format="json"
        )
        assert response.status_code == 200
        form.refresh_from_db()
        assert form.captcha_secret == "stored-secret"
        assert form.submit_button_label == "Send"


@pytest.mark.django_db
class TestCrossOrgReferences:
    """Every relation on a form has to resolve inside the caller's own org.

    `assign_to`, `notify_profiles` and `tags` are plain model relations, so DRF
    built each one with a queryset of EVERY row in the table. Posting another
    tenant's profile id was accepted, and the damage was not confined to a bad
    column: `webforms/tasks.py` mails the submission to
    `notify_profiles -> user.email`, so a form in one org could be pointed at a
    stranger in another and would send them this org's inbound leads.

    RLS does not cover this. `profile` is deliberately absent from
    ORG_SCOPED_TABLES (it is read before any tenant context exists), so there is
    no policy to fall back on and the org filter is the whole control.
    """

    def test_assign_to_refuses_another_orgs_profile(
        self, admin_client, org_b, profile_b
    ):
        response = admin_client.post(
            LIST_URL, {"name": "Probe", "assign_to": str(profile_b.id)}, format="json"
        )
        assert response.status_code == 400
        assert "assign_to" in response.data

    def test_notify_profiles_refuses_another_orgs_profile(
        self, admin_client, org_b, profile_b
    ):
        response = admin_client.post(
            LIST_URL,
            {"name": "Probe", "notify_profiles": [str(profile_b.id)]},
            format="json",
        )
        assert response.status_code == 400
        assert "notify_profiles" in response.data

    def test_tags_refuses_another_orgs_tag(self, admin_client, org_b):
        from common.models import Tags

        tag = Tags.objects.create(name="Theirs", org=org_b)
        response = admin_client.post(
            LIST_URL, {"name": "Probe", "tags": [str(tag.id)]}, format="json"
        )
        assert response.status_code == 400
        assert "tags" in response.data

    def test_an_update_cannot_reassign_across_orgs_either(
        self, admin_client, form, org_b, profile_b
    ):
        """Create and update are separate code paths, and only one of them was
        ever exercised by the happy-path tests."""
        response = admin_client.put(
            detail_url(form), {"assign_to": str(profile_b.id)}, format="json"
        )
        assert response.status_code == 400
        form.refresh_from_db()
        assert form.assign_to is None

    def test_the_org_own_profile_and_tag_are_still_accepted(
        self, admin_client, org_a, admin_profile
    ):
        """The guard has to be able to return both answers. A queryset narrowed
        to nothing would pass every test above and break the feature."""
        from common.models import Tags

        tag = Tags.objects.create(name="Inbound", org=org_a)
        response = admin_client.post(
            LIST_URL,
            {
                "name": "Ours",
                "assign_to": str(admin_profile.id),
                "notify_profiles": [str(admin_profile.id)],
                "tags": [str(tag.id)],
            },
            format="json",
        )
        assert response.status_code == 201
        created = WebForm.objects.get(id=response.data["id"])
        assert created.assign_to == admin_profile
        assert list(created.notify_profiles.all()) == [admin_profile]
        assert list(created.tags.all()) == [tag]
