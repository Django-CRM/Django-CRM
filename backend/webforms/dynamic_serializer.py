"""Builds a DRF serializer from one form's WebFormField rows.

The schema is rows in a table, so the serializer is built per form. Two things
that buys, which a hand-written serializer would not:

* a form that collects three fields cannot be used to write a fourth. Every
  Lead column not on the form is simply absent from the serializer, so DRF
  drops it. Dropping rather than answering 400 is deliberate: a 400 would tell
  a prober which Lead columns exist.
* the honeypot is declared here rather than in the view, so a second caller
  cannot forget it.
"""

from disposable_email_domains import blocklist as disposable_domains
from rest_framework import serializers

from common.custom_fields import validate_payload
from webforms.constants import LEAD_FIELD_CHOICE_SOURCES, LEAD_FIELD_MAX_LENGTHS
from webforms.models import WebFormField

# A field real visitors never see and never fill. Named to look attractive to a
# form-filling bot. Kept out of `lead_values()` so it can never reach a Lead.
HONEYPOT_FIELD = "company_website_url"


def _field_for(row):
    """One DRF field for one WebFormField row."""
    required = row.is_required

    if row.source == WebFormField.SOURCE_CUSTOM:
        # Typed loosely here and validated properly by common.custom_fields in
        # `validate()` below, which is the single validator for every custom
        # field in the app. Duplicating its type rules here is how the two
        # drift apart.
        return serializers.CharField(
            required=required, allow_blank=not required, max_length=1000
        )

    if row.lead_field == "email":
        return serializers.EmailField(required=required, allow_blank=not required)

    choices = LEAD_FIELD_CHOICE_SOURCES.get(row.lead_field)
    if choices is not None:
        return serializers.ChoiceField(
            choices=choices, required=required, allow_blank=not required
        )

    return serializers.CharField(
        required=required,
        allow_blank=not required,
        max_length=LEAD_FIELD_MAX_LENGTHS.get(row.lead_field, 255),
    )


def build_serializer(form):
    """Return a Serializer class for this form's current field set."""
    rows = list(form.fields.select_related("custom_field").all())
    lead_rows = [r for r in rows if r.source == WebFormField.SOURCE_LEAD]
    custom_rows = [
        r
        for r in rows
        if r.source == WebFormField.SOURCE_CUSTOM and r.custom_field_id is not None
    ]

    lead_keys = [r.lead_field for r in lead_rows]
    custom_keys = [r.custom_field.key for r in custom_rows]
    required_custom_keys = {r.custom_field.key for r in custom_rows if r.is_required}
    org = form.org

    attrs = {HONEYPOT_FIELD: serializers.CharField(required=False, allow_blank=True)}
    for row in lead_rows:
        attrs[row.lead_field] = _field_for(row)
    for row in custom_rows:
        attrs[row.custom_field.key] = _field_for(row)

    def validate_email(self, value):
        if not value or not form.reject_disposable_email:
            return value
        domain = value.rsplit("@", 1)[-1].lower()
        if domain in disposable_domains:
            raise serializers.ValidationError(
                "Disposable email addresses are not allowed."
            )
        return value

    def validate(self, attrs_in):
        """Coerce and validate the custom-field half through the shared validator.

        `validate_payload` reports an error for every REQUIRED definition in the
        org that is absent, including definitions this form has no input for.
        Applied literally that would mean one required Lead custom field breaks
        every web form in the org and the lead is lost entirely, so errors are
        narrowed to the keys this form actually collects. Requiredness for a web
        form is the form's own `WebFormField.is_required`, which DRF has already
        enforced by the time this runs.
        """
        submitted = {
            key: attrs_in[key]
            for key in custom_keys
            if attrs_in.get(key) not in (None, "")
        }
        cleaned, errors = validate_payload("Lead", submitted, org)

        relevant = {
            key: message
            for key, message in errors.items()
            if key in submitted or key in required_custom_keys
        }
        if relevant:
            raise serializers.ValidationError(relevant)

        self._custom_values = {
            key: value for key, value in cleaned.items() if key in custom_keys
        }
        return attrs_in

    def lead_values(self):
        """Validated values keyed by Lead attribute name.

        The honeypot and every custom key are excluded: the first is never a
        Lead column, and the second belongs in `custom_values()` because it
        lands in a JSON column rather than on an attribute.
        """
        return {
            key: value
            for key, value in self.validated_data.items()
            if key in lead_keys and value not in (None, "")
        }

    def custom_values(self):
        """Coerced custom-field values, keyed by definition key."""
        return dict(getattr(self, "_custom_values", {}))

    def honeypot_tripped(self):
        return bool(self.validated_data.get(HONEYPOT_FIELD, "").strip())

    attrs.update(
        {
            "validate_email": validate_email,
            "validate": validate,
            "lead_values": lead_values,
            "custom_values": custom_values,
            "honeypot_tripped": honeypot_tripped,
        }
    )
    return type("WebFormSubmissionSerializer", (serializers.Serializer,), attrs)
