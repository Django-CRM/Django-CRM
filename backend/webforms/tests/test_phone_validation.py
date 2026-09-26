"""A web form's phone field carries `flexible_phone_validator`, on both targets.

`_field_for` gave a phone row a bare CharField, so any 25 characters a stranger
typed reached `Lead.phone` or the auto-created `Contact.phone`. Both columns
declare the validator, but only `full_clean` runs it and the create paths do
not call that, so the form serializer is the one place it can be enforced.
"""

import pytest

from webforms.dynamic_serializer import build_serializer
from webforms.models import WebForm, WebFormField


def _form(org, target, phone_required=False):
    form = WebForm.objects.create(
        name=f"{target} form", org=org, is_published=True, target=target
    )
    key = "ticket_field" if target == WebForm.TARGET_TICKET else "lead_field"
    WebFormField.objects.create(
        form=form,
        org=org,
        order=0,
        source=(
            WebFormField.SOURCE_TICKET
            if target == WebForm.TARGET_TICKET
            else WebFormField.SOURCE_LEAD
        ),
        label="Email",
        is_required=True,
        **{key: "email"},
    )
    WebFormField.objects.create(
        form=form,
        org=org,
        order=1,
        source=(
            WebFormField.SOURCE_TICKET
            if target == WebForm.TARGET_TICKET
            else WebFormField.SOURCE_LEAD
        ),
        label="Phone",
        is_required=phone_required,
        **{key: "phone"},
    )
    return form


TARGETS = [WebForm.TARGET_LEAD, WebForm.TARGET_TICKET]


@pytest.mark.django_db
@pytest.mark.parametrize("target", TARGETS)
@pytest.mark.parametrize("phone", ["+1 (555) 010-2000", "020 7946 0958", "555.0100"])
def test_a_real_phone_number_is_accepted(org_a, target, phone):
    serializer = build_serializer(_form(org_a, target))(
        data={"email": "pat@example.com", "phone": phone}
    )

    assert serializer.is_valid(), serializer.errors
    assert serializer.field_values()["phone"] == phone


@pytest.mark.django_db
@pytest.mark.parametrize("target", TARGETS)
@pytest.mark.parametrize(
    "phone", ["call me maybe", "<script>x</script>", "123", "=HYPERLINK(1)"]
)
def test_junk_is_refused(org_a, target, phone):
    serializer = build_serializer(_form(org_a, target))(
        data={"email": "pat@example.com", "phone": phone}
    )

    assert not serializer.is_valid()
    assert "phone" in serializer.errors


@pytest.mark.django_db
@pytest.mark.parametrize("target", TARGETS)
def test_an_optional_phone_may_be_left_blank(org_a, target):
    serializer = build_serializer(_form(org_a, target))(
        data={"email": "pat@example.com", "phone": ""}
    )

    assert serializer.is_valid(), serializer.errors
    assert "phone" not in serializer.field_values()


@pytest.mark.django_db
@pytest.mark.parametrize("target", TARGETS)
def test_a_required_phone_may_not_be_blank(org_a, target):
    serializer = build_serializer(_form(org_a, target, phone_required=True))(
        data={"email": "pat@example.com", "phone": ""}
    )

    assert not serializer.is_valid()
    assert "phone" in serializer.errors
