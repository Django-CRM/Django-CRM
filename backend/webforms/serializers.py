"""Serializers for the authenticated web form management API.

Two rules run through all of this:

* anything the server derives is `read_only` or popped. `org`, `created_by` and
  `is_published` are never taken from a request body. Publishing in particular
  has its own endpoint because it validates the source state and the form's
  shape, and a writable `is_published` would be a second door onto the same
  transition with none of those checks.
* the field list is replaced wholesale, with `order` assigned from list
  position. A client that sends its own `order` cannot reorder a form by lying
  about it, and a drag-reorder is one atomic request rather than N.
"""

from urllib.parse import urlparse

from django.db import transaction
from rest_framework import serializers

from common.models import CustomFieldDefinition, Profile, Tags
from webforms.constants import LEAD_FIELD_VALUES
from webforms.models import WebForm, WebFormField, WebFormSubmission

ALLOWED_URL_SCHEMES = ("http", "https")


class WebFormFieldSerializer(serializers.ModelSerializer):
    custom_field = serializers.PrimaryKeyRelatedField(
        queryset=CustomFieldDefinition.objects.all(),
        required=False,
        allow_null=True,
    )

    class Meta:
        model = WebFormField
        fields = (
            "id",
            "source",
            "lead_field",
            "custom_field",
            "label",
            "placeholder",
            "is_required",
        )
        # `order` is absent from `fields` entirely rather than declared
        # read-only: the parent assigns it from list position, so a client
        # sending one is ignored and cannot reorder a form by lying about it.
        read_only_fields = ("id",)

    def validate_lead_field(self, value):
        if value and value not in LEAD_FIELD_VALUES:
            raise serializers.ValidationError(
                f"'{value}' is not a field a web form may collect."
            )
        return value

    def validate(self, attrs):
        lead_field = attrs.get("lead_field") or ""
        custom_field = attrs.get("custom_field")

        if lead_field and custom_field is not None:
            raise serializers.ValidationError(
                "A field names either a lead field or a custom field, not both."
            )
        if not lead_field and custom_field is None:
            raise serializers.ValidationError(
                "A field must name either a lead field or a custom field."
            )

        if custom_field is not None:
            org = self.context["org"]
            if custom_field.org_id != org.id:
                # Deliberately the same message as the wrong-target case: a
                # caller must not be able to probe which custom field ids exist
                # in another org by comparing error text.
                raise serializers.ValidationError(
                    {"custom_field": "No such custom field for leads."}
                )
            if custom_field.target_model != "Lead":
                raise serializers.ValidationError(
                    {"custom_field": "No such custom field for leads."}
                )
        return attrs


class WebFormListSerializer(serializers.ModelSerializer):
    submission_count = serializers.IntegerField(read_only=True)
    field_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = WebForm
        fields = (
            "id",
            "name",
            "is_published",
            "created_at",
            "submission_count",
            "field_count",
        )


class WebFormDetailSerializer(serializers.ModelSerializer):
    fields = WebFormFieldSerializer(many=True, required=False)
    embed_html = serializers.SerializerMethodField()
    embed_js = serializers.SerializerMethodField()
    # Whether a secret is stored, never which one. Turnstile fails closed, so a
    # form with a provider and no secret refuses every submission; without this
    # flag a client cannot tell that state from a working one, and cannot tell
    # an empty secret box ("none stored") from a hidden one ("stored, and
    # sending blank would wipe it").
    has_captcha_secret = serializers.SerializerMethodField()

    class Meta:
        model = WebForm
        fields = (
            "id",
            "name",
            "is_published",
            "allowed_origins",
            "submit_button_label",
            "success_mode",
            "success_message",
            "redirect_url",
            "assign_to",
            "notify_profiles",
            "lead_source",
            "tags",
            "captcha_provider",
            "captcha_site_key",
            "captcha_secret",
            "reject_disposable_email",
            "created_at",
            "fields",
            "embed_html",
            "embed_js",
            "has_captcha_secret",
        )
        read_only_fields = ("id", "created_at", "is_published")
        extra_kwargs = {
            # The org pastes this into its own Cloudflare dashboard; we only
            # ever send it to Cloudflare. Never read back.
            "captcha_secret": {"write_only": True},
        }

    def __init__(self, *args, **kwargs):
        """Narrow every relation to the caller's own org.

        `assign_to`, `notify_profiles` and `tags` are plain model relations, so
        DRF builds each with a queryset of every row in the table. Left alone,
        an admin could point a form at another tenant's profile, and that is
        not confined to a wrong column: `webforms/tasks.py` mails each
        submission to `notify_profiles -> user.email`, so the form would send
        this org's inbound leads to a stranger in a different one.

        RLS is not a fallback here. `profile` is deliberately outside
        ORG_SCOPED_TABLES because it is read before any tenant context exists,
        so there is no policy behind this and the filter IS the control.

        With no org in context (schema generation, and nothing else) every
        queryset stays empty, so the failure direction is refusal rather than
        an unscoped lookup.
        """
        super().__init__(*args, **kwargs)
        org = self.context.get("org")
        if org is None:
            request = self.context.get("request")
            org = getattr(getattr(request, "profile", None), "org", None)
        self._scope("assign_to", Profile.objects.filter(org=org) if org else None)
        self._scope("notify_profiles", Profile.objects.filter(org=org) if org else None)
        self._scope("tags", Tags.objects.filter(org=org) if org else None)

    def _scope(self, name, queryset):
        """Point one relation's lookup at `queryset`, or at nothing.

        A `many=True` relation validates through its `child_relation`, so
        assigning to the wrapper would leave the real lookup untouched and the
        guard would read as present while doing nothing.
        """
        field = self.fields[name]
        target = getattr(field, "child_relation", field)
        target.queryset = queryset if queryset is not None else target.queryset.none()

    # ---- embed snippets -------------------------------------------------
    #
    # Built here rather than in a client because they need the API's own base
    # URL. A browser knows the frontend's origin, not this one, and a relative
    # URL pasted onto a customer's site would point at the customer's server.

    def _public_base(self, obj):
        request = self.context.get("request")
        path = f"/api/public/forms/{obj.org_id}/{obj.id}/"
        if request is None:
            return path
        return request.build_absolute_uri(path)

    def get_embed_html(self, obj):
        return (
            f'<iframe src="{self._public_base(obj)}embed/" '
            f'style="width:100%;border:0" height="500" '
            f'title="{obj.name}"></iframe>'
        )

    def get_has_captcha_secret(self, obj):
        return bool(obj.captcha_secret)

    def get_embed_js(self, obj):
        return (
            f'<div id="bottlecrm-webform-{obj.id}"></div>\n'
            f'<script src="{self._public_base(obj)}embed.js" async></script>'
        )

    # ---- validation -----------------------------------------------------

    def validate_redirect_url(self, value):
        """Only http and https.

        This value is handed to the embed, which navigates to it. A
        `javascript:` or `data:` payload here would be stored XSS on the
        customer's own site, authored through our admin API.
        """
        if not value:
            return value
        parsed = urlparse(value)
        if parsed.scheme.lower() not in ALLOWED_URL_SCHEMES:
            raise serializers.ValidationError(
                "Enter an http or https URL. Other schemes are not allowed."
            )
        return value

    def validate_allowed_origins(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError(
                'Provide a list of origins, for example ["https://example.com"].'
            )
        cleaned = []
        for entry in value:
            if not isinstance(entry, str) or not entry.strip():
                raise serializers.ValidationError(
                    "Each origin must be a non-empty string."
                )
            entry = entry.strip()
            parsed = urlparse(entry)
            if parsed.scheme.lower() not in ALLOWED_URL_SCHEMES or not parsed.netloc:
                raise serializers.ValidationError(
                    f"'{entry}' is not a valid origin. Use a scheme and a host, "
                    f"for example https://example.com."
                )
            if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
                # An Origin header never carries a path, so an entry with one
                # can never match. It would look configured while doing
                # nothing, which is worse than being rejected.
                raise serializers.ValidationError(
                    f"'{entry}' has a path. An origin is only a scheme, host "
                    f"and optional port."
                )
            cleaned.append(f"{parsed.scheme}://{parsed.netloc}")
        return cleaned

    def validate(self, attrs):
        provider = attrs.get(
            "captcha_provider", getattr(self.instance, "captcha_provider", "")
        )
        if provider == WebForm.CAPTCHA_TURNSTILE:
            site_key = attrs.get(
                "captcha_site_key", getattr(self.instance, "captcha_site_key", "")
            )
            secret = attrs.get(
                "captcha_secret", getattr(self.instance, "captcha_secret", "")
            )
            if not site_key or not secret:
                raise serializers.ValidationError(
                    "Turnstile needs both a site key and a secret. Verification "
                    "fails closed, so a half-configured captcha rejects every "
                    "submission."
                )
        return attrs

    # ---- writes ---------------------------------------------------------

    def _write_fields(self, instance, rows):
        """Replace the form's field list with `rows`, in order.

        Delete-then-recreate rather than a diff. The list is short, the write
        is inside the caller's transaction, and a diff would have to reconcile
        reordering against the two unique constraints for no practical gain.
        """
        instance.fields.all().delete()
        for index, row in enumerate(rows):
            WebFormField.objects.create(
                form=instance,
                org=instance.org,
                order=index,
                source=row["source"],
                lead_field=row.get("lead_field") or "",
                custom_field=row.get("custom_field"),
                label=row["label"],
                placeholder=row.get("placeholder", ""),
                is_required=row.get("is_required", False),
            )

    @transaction.atomic
    def create(self, validated_data):
        rows = validated_data.pop("fields", None)
        tags = validated_data.pop("tags", None)
        notify = validated_data.pop("notify_profiles", None)
        instance = WebForm.objects.create(
            org=self.context["org"],
            created_by=self.context["request"].profile.user,
            **validated_data,
        )
        if tags is not None:
            instance.tags.set(tags)
        if notify is not None:
            instance.notify_profiles.set(notify)
        if rows is not None:
            self._write_fields(instance, rows)
        return instance

    @transaction.atomic
    def update(self, instance, validated_data):
        rows = validated_data.pop("fields", None)
        tags = validated_data.pop("tags", None)
        notify = validated_data.pop("notify_profiles", None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        if tags is not None:
            instance.tags.set(tags)
        if notify is not None:
            instance.notify_profiles.set(notify)
        if rows is not None:
            self._write_fields(instance, rows)
        return instance


class WebFormSubmissionSerializer(serializers.ModelSerializer):
    lead_name = serializers.SerializerMethodField()

    class Meta:
        model = WebFormSubmission
        fields = (
            "id",
            "created_at",
            "status",
            "payload",
            "lead",
            "lead_name",
            "submitted_ip",
            "referer",
        )
        # `reject_reason` is absent on purpose. It is internal triage detail
        # and naming the control that caught a bot is how the next bot gets
        # past it. Agents see the status, which is what they act on.

    def get_lead_name(self, obj):
        if obj.lead is None:
            return None
        return str(obj.lead)
