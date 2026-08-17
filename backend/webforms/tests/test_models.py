"""Model-level guarantees for the webforms app.

These are the rules the database itself has to hold, as opposed to the
serializer rules tested elsewhere. A constraint that only exists in a
serializer is one a management command or a data migration can walk straight
past.
"""

import datetime

import pytest
from django.db import IntegrityError, transaction

from common.models import CustomFieldDefinition
from common.utils import LEAD_SOURCE
from webforms.models import WebForm, WebFormDailyStat, WebFormField


@pytest.mark.django_db
class TestWebFormField:
    def _form(self, org_a):
        return WebForm.objects.create(name="Contact us", org=org_a)

    def test_a_lead_field_row_needs_no_custom_field(self, org_a):
        field = WebFormField.objects.create(
            form=self._form(org_a),
            org=org_a,
            order=0,
            source=WebFormField.SOURCE_LEAD,
            lead_field="email",
            label="Email",
        )
        assert field.custom_field is None

    def test_a_row_cannot_carry_both_a_lead_field_and_a_custom_field(self, org_a):
        definition = CustomFieldDefinition.objects.create(
            org=org_a,
            target_model="Lead",
            key="budget",
            label="Budget",
            field_type="number",
        )
        with pytest.raises(IntegrityError), transaction.atomic():
            WebFormField.objects.create(
                form=self._form(org_a),
                org=org_a,
                order=0,
                source=WebFormField.SOURCE_LEAD,
                lead_field="email",
                custom_field=definition,
                label="Both",
            )

    def test_a_row_cannot_carry_neither(self, org_a):
        with pytest.raises(IntegrityError), transaction.atomic():
            WebFormField.objects.create(
                form=self._form(org_a),
                org=org_a,
                order=0,
                source=WebFormField.SOURCE_LEAD,
                lead_field="",
                custom_field=None,
                label="Neither",
            )

    def test_the_same_lead_field_cannot_appear_twice_on_one_form(self, org_a):
        form = self._form(org_a)
        WebFormField.objects.create(
            form=form,
            org=org_a,
            order=0,
            source=WebFormField.SOURCE_LEAD,
            lead_field="email",
            label="Email",
        )
        with pytest.raises(IntegrityError), transaction.atomic():
            WebFormField.objects.create(
                form=form,
                org=org_a,
                order=1,
                source=WebFormField.SOURCE_LEAD,
                lead_field="email",
                label="Email again",
            )

    def test_the_same_lead_field_may_appear_on_two_different_forms(self, org_a):
        """The uniqueness is per form, not per org. Two forms both collecting
        an email address is the normal case, not a conflict."""
        for name in ("Contact us", "Request a demo"):
            WebFormField.objects.create(
                form=WebForm.objects.create(name=name, org=org_a),
                org=org_a,
                order=0,
                source=WebFormField.SOURCE_LEAD,
                lead_field="email",
                label="Email",
            )
        assert WebFormField.objects.filter(lead_field="email").count() == 2


@pytest.mark.django_db
class TestWebFormDailyStat:
    def test_one_row_per_form_per_day(self, org_a):
        form = WebForm.objects.create(name="Contact us", org=org_a)
        WebFormDailyStat.objects.create(
            form=form, org=org_a, date=datetime.date(2026, 8, 16)
        )
        with pytest.raises(IntegrityError), transaction.atomic():
            WebFormDailyStat.objects.create(
                form=form, org=org_a, date=datetime.date(2026, 8, 16)
            )

    def test_the_same_day_on_two_forms_is_fine(self, org_a):
        for name in ("Contact us", "Request a demo"):
            WebFormDailyStat.objects.create(
                form=WebForm.objects.create(name=name, org=org_a),
                org=org_a,
                date=datetime.date(2026, 8, 16),
            )
        assert WebFormDailyStat.objects.count() == 2


@pytest.mark.django_db
def test_lead_source_default_is_a_valid_lead_source_choice(org_a):
    """`Lead.source` declares choices=LEAD_SOURCE. Writing anything else into
    it, as the legacy view does with a website URL, makes the row invisible to
    every source-based filter and report."""
    form = WebForm.objects.create(name="Contact us", org=org_a)
    assert form.lead_source in dict(LEAD_SOURCE)


@pytest.mark.django_db
def test_a_form_is_unpublished_until_someone_publishes_it(org_a):
    assert WebForm.objects.create(name="Contact us", org=org_a).is_published is False
