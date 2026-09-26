"""Web forms that open a ticket instead of creating a lead.

A ticket form is the same anonymous endpoint as a lead form, with the same
controls (captcha, honeypot, throttles, allowed origins). What changes is the
record: a Case created the way a new inbound email creates one, a Contact
found or created by email within the form's org, and none of the lead-only
behaviour (source, merge, lead wording).
"""

import pytest
from django.core import mail
from django.core.cache import cache

from cases.models import Case, RoutingRule
from common.models import CustomFieldDefinition, Profile, Tags, User
from contacts.models import Contact
from leads.models import Lead
from webforms.dynamic_serializer import HONEYPOT_FIELD
from webforms.models import WebForm, WebFormField, WebFormSubmission
from webforms.service import submit_form

LIST_URL = "/api/webforms/"


def submit_url(org, form):
    return f"/api/public/forms/{org.id}/{form.id}/submit/"


def detail_url(form):
    return f"/api/webforms/{form.id}/"


@pytest.fixture(autouse=True)
def _clear_throttle_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def superuser_client(user_client, regular_user):
    """The `user_client` caller, still a plain USER profile, made superuser."""
    regular_user.is_superuser = True
    regular_user.save(update_fields=["is_superuser"])
    return user_client


def _ticket_form(org, **extra):
    form = WebForm.objects.create(
        name="Support request",
        org=org,
        target=WebForm.TARGET_TICKET,
        is_published=True,
        ticket_priority="High",
        ticket_type="Incident",
        **extra,
    )
    for order, key in enumerate(
        ["email", "first_name", "last_name", "phone", "name", "description"]
    ):
        WebFormField.objects.create(
            form=form,
            org=org,
            order=order,
            source=WebFormField.SOURCE_TICKET,
            ticket_field=key,
            label=key,
            is_required=key == "email",
        )
    return form


@pytest.fixture
def form(org_a):
    return _ticket_form(org_a)


def _post(client, target_org, web_form, **data):
    body = {"email": "pat@example.com", "name": "Printer on fire"}
    body.update(data)
    return client.post(submit_url(target_org, web_form), body, format="json")


# --------------------------------------------------------------------------
# Submission creates a ticket
# --------------------------------------------------------------------------


@pytest.mark.django_db
class TestTicketSubmission:
    def test_an_anonymous_post_opens_a_ticket_in_the_forms_org(
        self, unauthenticated_client, org_a, form
    ):
        response = _post(
            unauthenticated_client, org_a, form, description="It is smoking."
        )
        assert response.status_code == 200, response.data

        case = Case.objects.get(org=org_a)
        assert case.name == "Printer on fire"
        assert case.description == "It is smoking."
        assert case.status == "New"
        assert case.priority == "High"
        assert case.case_type == "Incident"
        submission = WebFormSubmission.objects.get(form=form)
        assert submission.status == WebFormSubmission.ACCEPTED
        assert submission.case_id == case.id
        assert submission.lead_id is None

    def test_no_lead_is_written_by_a_ticket_form(
        self, unauthenticated_client, org_a, form
    ):
        _post(unauthenticated_client, org_a, form)
        assert not Lead.objects.filter(org=org_a).exists()

    def test_sla_targets_are_stamped_like_any_new_case(
        self, unauthenticated_client, org_a, form
    ):
        _post(unauthenticated_client, org_a, form)
        case = Case.objects.get(org=org_a)
        assert case.sla_first_response_hours is not None
        assert case.sla_resolution_hours is not None

    def test_a_subject_longer_than_case_name_is_truncated_not_refused(
        self, unauthenticated_client, org_a, form
    ):
        response = _post(unauthenticated_client, org_a, form, name="x" * 200)
        assert response.status_code == 200, response.data
        assert Case.objects.get(org=org_a).name == "x" * 64

    def test_a_form_without_a_subject_names_the_ticket_after_the_form(
        self, unauthenticated_client, org_a, form
    ):
        _post(unauthenticated_client, org_a, form, name="")
        assert Case.objects.get(org=org_a).name == "Support request"

    def test_a_blank_ticket_type_creates_a_case_with_no_type(
        self, unauthenticated_client, org_a, form
    ):
        form.ticket_type = ""
        form.save(update_fields=["ticket_type"])
        _post(unauthenticated_client, org_a, form)
        assert Case.objects.get(org=org_a).case_type is None

    def test_the_forms_tags_are_applied(self, unauthenticated_client, org_a, form):
        tag = Tags.objects.create(org=org_a, name="web")
        form.tags.add(tag)
        _post(unauthenticated_client, org_a, form)
        assert list(Case.objects.get(org=org_a).tags.all()) == [tag]

    def test_a_visitor_cannot_set_priority_status_or_org(
        self, unauthenticated_client, org_a, org_b, form
    ):
        """Fields not on the form are dropped, so a stranger cannot raise
        their own ticket to Urgent or aim it at another tenant."""
        _post(
            unauthenticated_client,
            org_a,
            form,
            priority="Urgent",
            status="Closed",
            org=str(org_b.id),
        )
        case = Case.objects.get(org=org_a)
        assert case.priority == "High"
        assert case.status == "New"
        assert not Case.objects.filter(org=org_b).exists()


# --------------------------------------------------------------------------
# Contact matching
# --------------------------------------------------------------------------


@pytest.mark.django_db
class TestTicketContact:
    def test_an_existing_contact_is_matched_case_insensitively(
        self, unauthenticated_client, org_a, form
    ):
        existing = Contact.objects.create(
            org=org_a, first_name="Pat", last_name="Old", email="Pat@Example.com"
        )
        _post(
            unauthenticated_client,
            org_a,
            form,
            email="pat@example.COM",
            first_name="Mallory",
            phone="+1 555 0100",
        )
        case = Case.objects.get(org=org_a)
        assert list(case.contacts.all()) == [existing]
        assert Contact.objects.filter(org=org_a).count() == 1
        # A stranger who knows the address cannot rewrite the contact.
        existing.refresh_from_db()
        assert existing.first_name == "Pat"
        assert existing.phone in (None, "")

    def test_an_unknown_address_creates_a_contact_from_the_submission(
        self, unauthenticated_client, org_a, form
    ):
        _post(
            unauthenticated_client,
            org_a,
            form,
            first_name="Pat",
            last_name="Lee",
            phone="+1 555 0100",
        )
        contact = Contact.objects.get(org=org_a)
        assert contact.email == "pat@example.com"
        assert (contact.first_name, contact.last_name) == ("Pat", "Lee")
        assert contact.phone == "+1 555 0100"
        assert contact.auto_created is True
        assert list(Case.objects.get(org=org_a).contacts.all()) == [contact]

    def test_a_contact_with_the_address_in_another_org_is_not_used(
        self, unauthenticated_client, org_a, org_b, form
    ):
        from common.testing import rls_org

        with rls_org(org_b):
            foreign = Contact.objects.create(
                org=org_b, first_name="Pat", last_name="B", email="pat@example.com"
            )
        _post(unauthenticated_client, org_a, form)
        contact = Case.objects.get(org=org_a).contacts.get()
        assert contact.org_id == org_a.id
        assert contact.id != foreign.id

    def test_a_missing_first_name_falls_back_to_the_address(
        self, unauthenticated_client, org_a, form
    ):
        _post(unauthenticated_client, org_a, form, email="sam@example.com")
        assert Contact.objects.get(org=org_a).first_name == "sam"


# --------------------------------------------------------------------------
# Assignment and routing
# --------------------------------------------------------------------------


def _member(org, email, *, active=True):
    user = User.objects.create_user(email=email, password="x")
    return Profile.objects.create(user=user, org=org, role="USER", is_active=active)


@pytest.mark.django_db
class TestTicketAssignment:
    def test_an_active_assignee_in_the_org_is_applied(
        self, unauthenticated_client, org_a, form
    ):
        agent = _member(org_a, "agent@test.com")
        form.assign_to = agent
        form.save(update_fields=["assign_to"])
        _post(unauthenticated_client, org_a, form)
        assert list(Case.objects.get(org=org_a).assigned_to.all()) == [agent]

    def test_an_inactive_assignee_is_never_applied(
        self, unauthenticated_client, org_a, form
    ):
        gone = _member(org_a, "gone@test.com", active=False)
        form.assign_to = gone
        form.save(update_fields=["assign_to"])
        _post(unauthenticated_client, org_a, form)
        assert not Case.objects.get(org=org_a).assigned_to.exists()

    def test_an_assignee_from_another_org_is_never_applied(
        self, unauthenticated_client, org_a, org_b, form
    ):
        stranger = _member(org_b, "stranger@test.com")
        form.assign_to = stranger
        form.save(update_fields=["assign_to"])
        _post(unauthenticated_client, org_a, form)
        assert not Case.objects.get(org=org_a).assigned_to.exists()

    def test_routing_matches_the_senders_domain_as_for_inbound_email(
        self, unauthenticated_client, org_a, form
    ):
        agent = _member(org_a, "router@test.com")
        rule = RoutingRule.objects.create(
            org=org_a,
            name="Example customers",
            conditions=[
                {"field": "from_email_domain", "op": "eq", "value": "example.com"}
            ],
            strategy="direct",
        )
        rule.target_assignees.set([agent])
        _post(unauthenticated_client, org_a, form)
        assert list(Case.objects.get(org=org_a).assigned_to.all()) == [agent]

    def test_a_rule_on_the_forms_tag_routes_the_ticket_once(
        self, unauthenticated_client, org_a, form
    ):
        """Routing used to run on the first save, before the tags were on."""
        from common.models import Activity

        form.tags.add(Tags.objects.create(org=org_a, name="billing"))
        agent = _member(org_a, "billing@test.com")
        rule = RoutingRule.objects.create(
            org=org_a,
            name="Billing",
            conditions=[{"field": "tags", "op": "eq", "value": "billing"}],
            strategy="direct",
        )
        rule.target_assignees.set([agent])
        _post(unauthenticated_client, org_a, form)
        case = Case.objects.get(org=org_a)
        assert list(case.assigned_to.all()) == [agent]
        assert (
            Activity.objects.filter(
                entity_type="Case", entity_id=case.pk, action="ROUTED"
            ).count()
            == 1
        )


# --------------------------------------------------------------------------
# Controls unchanged on ticket forms
# --------------------------------------------------------------------------


@pytest.mark.django_db
class TestTicketFormControls:
    def test_the_honeypot_writes_no_ticket(self, unauthenticated_client, org_a, form):
        response = _post(
            unauthenticated_client, org_a, form, **{HONEYPOT_FIELD: "http://spam"}
        )
        assert response.status_code == 200
        assert not Case.objects.filter(org=org_a).exists()
        assert (
            WebFormSubmission.objects.get(form=form).status
            == WebFormSubmission.REJECTED_SPAM
        )

    def test_a_failed_captcha_is_400_and_writes_no_ticket(
        self, unauthenticated_client, org_a, form, monkeypatch
    ):
        monkeypatch.setattr(
            "webforms.public_views.captcha.verify", lambda *a, **k: False
        )
        response = _post(unauthenticated_client, org_a, form)
        assert response.status_code == 400
        assert not Case.objects.filter(org=org_a).exists()
        assert not Contact.objects.filter(org=org_a).exists()

    def test_a_passing_captcha_opens_the_ticket(
        self, unauthenticated_client, org_a, form, monkeypatch
    ):
        monkeypatch.setattr(
            "webforms.public_views.captcha.verify", lambda *a, **k: True
        )
        assert _post(unauthenticated_client, org_a, form).status_code == 200
        assert Case.objects.filter(org=org_a).count() == 1

    def test_an_origin_outside_the_allowed_list_is_403(
        self, unauthenticated_client, org_a, form
    ):
        form.allowed_origins = ["https://good.example"]
        form.save(update_fields=["allowed_origins"])
        response = unauthenticated_client.post(
            submit_url(org_a, form),
            {"email": "pat@example.com"},
            format="json",
            HTTP_ORIGIN="https://evil.example",
        )
        assert response.status_code == 403
        assert not Case.objects.filter(org=org_a).exists()

    def test_a_lead_column_posted_to_a_ticket_form_is_dropped(
        self, unauthenticated_client, org_a, form
    ):
        _post(unauthenticated_client, org_a, form, company_name="Acme", website="x")
        submission = WebFormSubmission.objects.get(form=form)
        assert "website" not in submission.payload


# --------------------------------------------------------------------------
# Custom fields
# --------------------------------------------------------------------------


@pytest.mark.django_db
class TestTicketCustomFields:
    def test_a_case_custom_field_lands_on_the_ticket(
        self, unauthenticated_client, org_a, form
    ):
        definition = CustomFieldDefinition.objects.create(
            org=org_a,
            target_model="Case",
            key="order_number",
            label="Order",
            field_type="text",
        )
        WebFormField.objects.create(
            form=form,
            org=org_a,
            order=9,
            source=WebFormField.SOURCE_CUSTOM,
            custom_field=definition,
            label="Order",
        )
        _post(unauthenticated_client, org_a, form, order_number="A-1")
        assert Case.objects.get(org=org_a).custom_fields == {"order_number": "A-1"}


# --------------------------------------------------------------------------
# Notification wording
# --------------------------------------------------------------------------


@pytest.mark.django_db
class TestTicketNotification:
    def test_the_email_says_ticket_and_links_the_ticket(
        self, unauthenticated_client, org_a, form, admin_profile, settings
    ):
        settings.CELERY_TASK_ALWAYS_EAGER = True
        form.notify_profiles.add(admin_profile)
        mail.outbox.clear()
        from webforms.tasks import send_webform_submission_email

        submission = submit_form(form, {"email": "pat@example.com", "name": "Help"})
        send_webform_submission_email(str(submission.id), str(org_a.id))

        assert len(mail.outbox) == 1
        message = mail.outbox[0]
        assert message.subject == "New ticket: Support request"
        body = message.alternatives[0][0] if message.alternatives else message.body
        assert "Open the ticket" in body
        assert f"/tickets/{submission.case_id}" in body
        assert "lead" not in body.lower()


# --------------------------------------------------------------------------
# The management API
# --------------------------------------------------------------------------


def _ticket_row(key, **extra):
    return {"source": "ticket", "ticket_field": key, "label": key, **extra}


@pytest.mark.django_db
class TestTicketFormWrites:
    def test_an_admin_creates_a_ticket_form(self, admin_client, org_a):
        response = admin_client.post(
            LIST_URL,
            {
                "name": "Support",
                "target": "ticket",
                "ticket_priority": "Urgent",
                "ticket_type": "Problem",
                "fields": [_ticket_row("email"), _ticket_row("name")],
            },
            format="json",
        )
        assert response.status_code == 201, response.data
        form = WebForm.objects.get(id=response.data["id"])
        assert form.target == "ticket"
        assert (form.ticket_priority, form.ticket_type) == ("Urgent", "Problem")
        assert [f.ticket_field for f in form.fields.order_by("order")] == [
            "email",
            "name",
        ]

    def test_a_superuser_without_an_admin_role_can_create(
        self, superuser_client, org_a
    ):
        response = superuser_client.post(
            LIST_URL, {"name": "Support", "target": "ticket"}, format="json"
        )
        assert response.status_code == 201, response.data

    def test_a_member_cannot_create_a_ticket_form(self, user_client, org_a):
        response = user_client.post(
            LIST_URL, {"name": "Support", "target": "ticket"}, format="json"
        )
        assert response.status_code == 403
        assert not WebForm.objects.filter(org=org_a).exists()

    def test_a_member_cannot_update_a_ticket_form(self, user_client, form):
        response = user_client.put(
            detail_url(form), {"ticket_priority": "Low"}, format="json"
        )
        assert response.status_code == 403
        form.refresh_from_db()
        assert form.ticket_priority == "High"

    def test_an_admin_updates_the_ticket_defaults(self, admin_client, form):
        response = admin_client.put(
            detail_url(form),
            {"ticket_priority": "Low", "ticket_type": ""},
            format="json",
        )
        assert response.status_code == 200, response.data
        form.refresh_from_db()
        assert (form.ticket_priority, form.ticket_type) == ("Low", "")

    @pytest.mark.parametrize(
        "body",
        [
            {"ticket_priority": "Critical"},
            {"ticket_type": "Complaint"},
            {"target": "opportunity"},
        ],
    )
    def test_values_outside_the_case_choices_are_400(self, admin_client, form, body):
        assert (
            admin_client.put(detail_url(form), body, format="json").status_code == 400
        )

    def test_a_lead_field_on_a_ticket_form_is_400(self, admin_client, form):
        response = admin_client.put(
            detail_url(form),
            {"fields": [{"source": "lead", "lead_field": "email", "label": "E"}]},
            format="json",
        )
        assert response.status_code == 400
        assert form.fields.filter(source="ticket").count() == 6

    def test_a_ticket_field_on_a_lead_form_is_400(self, admin_client, org_a):
        lead_form = WebForm.objects.create(name="Contact", org=org_a)
        response = admin_client.put(
            detail_url(lead_form),
            {"fields": [_ticket_row("email")]},
            format="json",
        )
        assert response.status_code == 400
        assert not lead_form.fields.exists()

    def test_a_ticket_field_outside_the_whitelist_is_400(self, admin_client, form):
        response = admin_client.put(
            detail_url(form),
            {"fields": [_ticket_row("priority")]},
            format="json",
        )
        assert response.status_code == 400

    def test_a_row_whose_source_does_not_match_its_field_is_400(
        self, admin_client, form
    ):
        response = admin_client.put(
            detail_url(form),
            {"fields": [{"source": "custom", "ticket_field": "email", "label": "E"}]},
            format="json",
        )
        assert response.status_code == 400

    def test_a_row_with_no_label_is_400_not_500(self, admin_client, form):
        response = admin_client.put(
            detail_url(form),
            {"fields": [{"source": "ticket", "ticket_field": "email"}]},
            format="json",
        )
        assert response.status_code == 400

    def test_a_case_custom_field_is_accepted_on_a_ticket_form(
        self, admin_client, org_a, form
    ):
        definition = CustomFieldDefinition.objects.create(
            org=org_a, target_model="Case", key="sev", label="S", field_type="text"
        )
        response = admin_client.put(
            detail_url(form),
            {
                "fields": [
                    _ticket_row("email"),
                    {
                        "source": "custom",
                        "custom_field": str(definition.id),
                        "label": "S",
                    },
                ]
            },
            format="json",
        )
        assert response.status_code == 200, response.data

    def test_a_lead_custom_field_is_refused_on_a_ticket_form(
        self, admin_client, org_a, form
    ):
        definition = CustomFieldDefinition.objects.create(
            org=org_a, target_model="Lead", key="budget", label="B", field_type="text"
        )
        response = admin_client.put(
            detail_url(form),
            {
                "fields": [
                    {
                        "source": "custom",
                        "custom_field": str(definition.id),
                        "label": "B",
                    }
                ]
            },
            format="json",
        )
        assert response.status_code == 400

    def test_a_case_custom_field_from_another_org_is_refused(
        self, admin_client, org_b, form
    ):
        from common.testing import rls_org

        with rls_org(org_b):
            foreign = CustomFieldDefinition.objects.create(
                org=org_b, target_model="Case", key="sev", label="S", field_type="text"
            )
        response = admin_client.put(
            detail_url(form),
            {
                "fields": [
                    {"source": "custom", "custom_field": str(foreign.id), "label": "S"}
                ]
            },
            format="json",
        )
        assert response.status_code == 400

    def test_the_detail_and_list_carry_the_target(self, user_client, form):
        assert user_client.get(detail_url(form)).data["target"] == "ticket"
        assert user_client.get(LIST_URL).data["results"][0]["target"] == "ticket"


@pytest.mark.django_db
class TestTargetChange:
    def test_the_target_changes_with_its_field_list_before_any_submission(
        self, admin_client, form
    ):
        response = admin_client.put(
            detail_url(form),
            {
                "target": "lead",
                "fields": [{"source": "lead", "lead_field": "email", "label": "E"}],
            },
            format="json",
        )
        assert response.status_code == 200, response.data
        form.refresh_from_db()
        assert form.target == "lead"

    def test_the_target_cannot_change_without_a_new_field_list(
        self, admin_client, form
    ):
        response = admin_client.put(detail_url(form), {"target": "lead"}, format="json")
        assert response.status_code == 400
        assert "fields" in response.data
        form.refresh_from_db()
        assert form.target == "ticket"

    def test_the_target_cannot_change_once_the_form_has_submissions(
        self, admin_client, form
    ):
        submit_form(form, {"email": "pat@example.com"})
        response = admin_client.put(
            detail_url(form),
            {
                "target": "lead",
                "fields": [{"source": "lead", "lead_field": "email", "label": "E"}],
            },
            format="json",
        )
        assert response.status_code == 400
        assert "submissions" in str(response.data["target"])
        form.refresh_from_db()
        assert form.target == "ticket"

    def test_a_form_backing_a_legacy_api_key_stays_a_lead_form(
        self, admin_client, org_a
    ):
        from common.models import APISettings

        setting = APISettings.objects.create(
            org=org_a, title="Site", website="https://example.com"
        )
        legacy = WebForm.objects.create(
            name="Legacy", org=org_a, legacy_api_setting=setting
        )
        response = admin_client.put(
            detail_url(legacy),
            {"target": "ticket", "fields": [_ticket_row("email")]},
            format="json",
        )
        assert response.status_code == 400
        legacy.refresh_from_db()
        assert legacy.target == "lead"

    def test_resending_the_same_target_after_submissions_is_fine(
        self, admin_client, form
    ):
        submit_form(form, {"email": "pat@example.com"})
        response = admin_client.put(
            detail_url(form), {"target": "ticket", "name": "Renamed"}, format="json"
        )
        assert response.status_code == 200, response.data


@pytest.mark.django_db
class TestTicketPublishAndSubmissions:
    def test_a_ticket_form_without_an_email_field_cannot_publish(
        self, admin_client, org_a
    ):
        draft = WebForm.objects.create(name="S", org=org_a, target="ticket")
        WebFormField.objects.create(
            form=draft,
            org=org_a,
            source=WebFormField.SOURCE_TICKET,
            ticket_field="name",
            label="Subject",
        )
        response = admin_client.post(f"{detail_url(draft)}publish/")
        assert response.status_code == 400
        assert "contact" in response.data["errors"]

    def test_a_ticket_form_with_an_email_field_publishes(self, admin_client, form):
        form.is_published = False
        form.save(update_fields=["is_published"])
        response = admin_client.post(f"{detail_url(form)}publish/")
        assert response.status_code == 200, response.data

    def test_the_submission_list_links_the_ticket(self, user_client, form):
        submission = submit_form(form, {"email": "pat@example.com", "name": "Help"})
        response = user_client.get(f"{detail_url(form)}submissions/")
        row = response.data["results"][0]
        assert row["case"] == submission.case_id
        assert row["case_name"] == "Help"
        assert row["lead"] is None


@pytest.mark.django_db
class TestSubmissionConstraint:
    def test_a_submission_cannot_link_both_a_lead_and_a_case(self, org_a, form):
        from django.db import IntegrityError, transaction

        lead = Lead.objects.create(org=org_a, first_name="P", email="p@example.com")
        case = Case.objects.create(org=org_a, name="C", status="New", priority="Low")
        with pytest.raises(IntegrityError), transaction.atomic():
            WebFormSubmission.objects.create(
                org=org_a,
                form=form,
                lead=lead,
                case=case,
                status=WebFormSubmission.ACCEPTED,
            )
