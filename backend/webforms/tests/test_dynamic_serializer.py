"""Validation for one form's shape.

A web form is filled in by a stranger, so every rule here is a security rule
rather than a convenience. The dynamic part matters because the schema is rows
in a table: a form that collects three fields must reject a fourth.
"""

import pytest

from common.models import CustomFieldDefinition
from webforms.dynamic_serializer import HONEYPOT_FIELD, build_serializer
from webforms.models import WebForm, WebFormField


@pytest.fixture
def form(org_a):
    web_form = WebForm.objects.create(name="Contact us", org=org_a, is_published=True)
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


def budget_definition(org, is_required=False):
    return CustomFieldDefinition.objects.create(
        org=org,
        target_model="Lead",
        key="budget",
        label="Budget",
        field_type="number",
        is_required=is_required,
    )


def add_custom(form, org, definition, order=9):
    return WebFormField.objects.create(
        form=form,
        org=org,
        order=order,
        source=WebFormField.SOURCE_CUSTOM,
        custom_field=definition,
        label=definition.label,
    )


@pytest.mark.django_db
class TestAcceptance:
    def test_a_valid_payload_passes(self, form):
        serializer = build_serializer(form)(
            data={"email": "pat@example.com", "first_name": "Pat"}
        )
        assert serializer.is_valid(), serializer.errors

    def test_an_optional_field_may_be_omitted(self, form):
        serializer = build_serializer(form)(data={"email": "pat@example.com"})
        assert serializer.is_valid(), serializer.errors

    def test_lead_values_are_keyed_by_lead_attribute_name(self, form):
        serializer = build_serializer(form)(
            data={"email": "pat@example.com", "first_name": "Pat"}
        )
        assert serializer.is_valid(), serializer.errors
        assert serializer.lead_values() == {
            "email": "pat@example.com",
            "first_name": "Pat",
        }

    def test_an_omitted_optional_field_is_absent_rather_than_blank(self, form):
        serializer = build_serializer(form)(data={"email": "pat@example.com"})
        assert serializer.is_valid(), serializer.errors
        assert "first_name" not in serializer.lead_values()


@pytest.mark.django_db
class TestRejection:
    def test_a_missing_required_field_is_rejected(self, form):
        serializer = build_serializer(form)(data={"first_name": "Pat"})
        assert not serializer.is_valid()
        assert "email" in serializer.errors

    def test_a_malformed_email_is_rejected(self, form):
        serializer = build_serializer(form)(data={"email": "not-an-email"})
        assert not serializer.is_valid()
        assert "email" in serializer.errors

    def test_a_field_the_form_does_not_collect_is_dropped_not_stored(self, form):
        """An unlisted key must not reach the Lead. Dropping it rather than
        answering 400 is deliberate: a 400 would tell a prober which Lead
        columns exist."""
        serializer = build_serializer(form)(
            data={"email": "pat@example.com", "opportunity_amount": "999999"}
        )
        assert serializer.is_valid(), serializer.errors
        assert "opportunity_amount" not in serializer.validated_data
        assert "opportunity_amount" not in serializer.lead_values()

    def test_an_over_length_value_is_rejected(self, form, org_a):
        WebFormField.objects.create(
            form=form,
            org=org_a,
            order=2,
            source=WebFormField.SOURCE_LEAD,
            lead_field="phone",
            label="Phone",
        )
        serializer = build_serializer(form)(
            data={"email": "pat@example.com", "phone": "1" * 40}
        )
        assert not serializer.is_valid()
        assert "phone" in serializer.errors

    def test_a_disposable_domain_is_rejected_when_the_form_asks(self, form):
        form.reject_disposable_email = True
        form.save(update_fields=["reject_disposable_email"])
        serializer = build_serializer(form)(data={"email": "pat@mailinator.com"})
        assert not serializer.is_valid()
        assert "email" in serializer.errors

    def test_a_disposable_domain_is_accepted_when_the_form_does_not(self, form):
        form.reject_disposable_email = False
        form.save(update_fields=["reject_disposable_email"])
        serializer = build_serializer(form)(data={"email": "pat@mailinator.com"})
        assert serializer.is_valid(), serializer.errors

    def test_a_value_outside_a_choice_field_is_rejected(self, form, org_a):
        WebFormField.objects.create(
            form=form,
            org=org_a,
            order=3,
            source=WebFormField.SOURCE_LEAD,
            lead_field="country",
            label="Country",
        )
        serializer = build_serializer(form)(
            data={"email": "pat@example.com", "country": "ZZZ"}
        )
        assert not serializer.is_valid()
        assert "country" in serializer.errors

    def test_a_valid_choice_value_passes(self, form, org_a):
        WebFormField.objects.create(
            form=form,
            org=org_a,
            order=3,
            source=WebFormField.SOURCE_LEAD,
            lead_field="country",
            label="Country",
        )
        serializer = build_serializer(form)(
            data={"email": "pat@example.com", "country": "IN"}
        )
        assert serializer.is_valid(), serializer.errors


@pytest.mark.django_db
class TestCustomFields:
    def test_a_valid_custom_value_is_coerced(self, form, org_a):
        add_custom(form, org_a, budget_definition(org_a))
        serializer = build_serializer(form)(
            data={"email": "pat@example.com", "budget": "5000"}
        )
        assert serializer.is_valid(), serializer.errors
        assert serializer.custom_values() == {"budget": 5000.0}

    def test_a_custom_value_of_the_wrong_type_is_rejected(self, form, org_a):
        add_custom(form, org_a, budget_definition(org_a))
        serializer = build_serializer(form)(
            data={"email": "pat@example.com", "budget": "not a number"}
        )
        assert not serializer.is_valid()
        assert "budget" in serializer.errors

    def test_a_custom_value_never_leaks_into_lead_values(self, form, org_a):
        add_custom(form, org_a, budget_definition(org_a))
        serializer = build_serializer(form)(
            data={"email": "pat@example.com", "budget": "5000"}
        )
        assert serializer.is_valid(), serializer.errors
        assert "budget" not in serializer.lead_values()

    def test_a_dropdown_value_outside_its_options_is_rejected(self, form, org_a):
        definition = CustomFieldDefinition.objects.create(
            org=org_a,
            target_model="Lead",
            key="plan",
            label="Plan",
            field_type="dropdown",
            options=[{"value": "pro", "label": "Pro"}],
        )
        add_custom(form, org_a, definition)
        serializer = build_serializer(form)(
            data={"email": "pat@example.com", "plan": "enterprise"}
        )
        assert not serializer.is_valid()
        assert "plan" in serializer.errors

    def test_a_required_definition_the_form_does_not_collect_does_not_block(
        self, form, org_a
    ):
        """`common.custom_fields.validate_payload` errors on every required
        definition that is absent, including ones this form has no input for.
        Taken literally that would make a single required Lead custom field
        break every web form in the org, losing the lead entirely. The form's
        own field list governs here."""
        budget_definition(org_a, is_required=True)
        serializer = build_serializer(form)(data={"email": "pat@example.com"})
        assert serializer.is_valid(), serializer.errors
        assert serializer.custom_values() == {}

    def test_a_required_definition_the_form_does_collect_still_blocks(
        self, form, org_a
    ):
        definition = budget_definition(org_a, is_required=True)
        row = add_custom(form, org_a, definition)
        row.is_required = True
        row.save(update_fields=["is_required"])
        serializer = build_serializer(form)(data={"email": "pat@example.com"})
        assert not serializer.is_valid()
        assert "budget" in serializer.errors


@pytest.mark.django_db
class TestHoneypot:
    def test_the_honeypot_field_is_always_present(self, form):
        assert HONEYPOT_FIELD in build_serializer(form)().fields

    def test_an_empty_honeypot_passes(self, form):
        serializer = build_serializer(form)(
            data={"email": "pat@example.com", HONEYPOT_FIELD: ""}
        )
        assert serializer.is_valid(), serializer.errors
        assert serializer.honeypot_tripped() is False

    def test_a_filled_honeypot_is_detected(self, form):
        serializer = build_serializer(form)(
            data={"email": "pat@example.com", HONEYPOT_FIELD: "http://spam.example"}
        )
        assert serializer.is_valid(), serializer.errors
        assert serializer.honeypot_tripped() is True

    def test_a_whitespace_only_honeypot_is_not_tripped(self, form):
        serializer = build_serializer(form)(
            data={"email": "pat@example.com", HONEYPOT_FIELD: "   "}
        )
        assert serializer.is_valid(), serializer.errors
        assert serializer.honeypot_tripped() is False

    def test_the_honeypot_never_reaches_the_lead_values(self, form):
        serializer = build_serializer(form)(
            data={"email": "pat@example.com", HONEYPOT_FIELD: "x"}
        )
        assert serializer.is_valid(), serializer.errors
        assert HONEYPOT_FIELD not in serializer.lead_values()
