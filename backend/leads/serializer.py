from datetime import timedelta

from django.utils import timezone
from rest_framework import serializers

from common.serializer import (
    AttachmentsSerializer,
    LeadCommentSerializer,
    ProfileSerializer,
    TagsSerializer,
    TeamsSerializer,
    UserSerializer,
)
from common.utils import LEAD_STATUS
from contacts.serializer import ContactSerializer
from leads.models import (
    DataSubjectRequest,
    Lead,
    LeadAttributionTouch,
    LeadPipeline,
    LeadStage,
    MarketingCampaign,
)
from leads.workflow import IRREVERSIBLE_STATUSES


class StrictFieldsMixin:
    """Reject undeclared input instead of silently discarding it."""

    def to_internal_value(self, data):
        if hasattr(data, "keys"):
            unknown = sorted(set(data.keys()) - set(self.fields.keys()))
            read_only = sorted(
                name
                for name in data.keys()
                if name in self.fields and self.fields[name].read_only
            )
            if unknown or read_only:
                raise serializers.ValidationError(
                    {
                        **{name: ["Unknown field."] for name in unknown},
                        **{name: ["Read-only field."] for name in read_only},
                    }
                )
        return super().to_internal_value(data)


class LeadSerializer(serializers.ModelSerializer):
    contacts = ContactSerializer(read_only=True, many=True)
    assigned_to = ProfileSerializer(read_only=True, many=True)
    created_by = UserSerializer()
    tags = TagsSerializer(read_only=True, many=True)
    lead_attachment = AttachmentsSerializer(read_only=True, many=True)
    teams = TeamsSerializer(read_only=True, many=True)
    lead_comments = LeadCommentSerializer(read_only=True, many=True)

    class Meta:
        model = Lead
        fields = (
            "id",
            # Core Lead Information
            "title",
            "salutation",
            "first_name",
            "last_name",
            "email",
            "phone",
            "job_title",
            "website",
            "linkedin_url",
            # Sales Pipeline
            "status",
            "source",
            "industry",
            "rating",
            "opportunity_amount",
            "currency",
            "probability",
            "close_date",
            # Address
            "address_line",
            "city",
            "state",
            "postcode",
            "country",
            # Assignment
            "assigned_to",
            "teams",
            # Activity
            "last_contacted",
            "next_follow_up",
            "description",
            # Related
            "contacts",
            "lead_attachment",
            "lead_comments",
            "tags",
            # System
            "created_by",
            "created_at",
            "updated_at",
            "is_active",
            "is_sample",
            "company_name",
            # Kanban
            "stage",
            "kanban_order",
            # Per-org custom fields (validated via common.custom_fields)
            "custom_fields",
        )
        # is_sample is server-set only (see leads/models.py). Read-only here
        # even though this serializer is a read/list path today (writes go
        # through LeadCreateSerializer, which never lists this field at all).
        # Defense in depth: a future write path added to this serializer
        # without checking Meta.fields first still can't make it writable.
        read_only_fields = ("is_sample",)


class LeadCreateSerializer(serializers.ModelSerializer):
    probability = serializers.IntegerField(
        max_value=100, required=False, allow_null=True
    )
    opportunity_amount = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, allow_null=True
    )
    close_date = serializers.DateField(required=False, allow_null=True)

    def __init__(self, *args, **kwargs):
        request_obj = kwargs.pop("request_obj", None)
        super().__init__(*args, **kwargs)
        if self.initial_data and self.initial_data.get("status") == "converted":
            self.fields["email"].required = True
        self.fields["first_name"].required = False
        self.fields["last_name"].required = False
        self.fields["salutation"].required = False
        self.org = request_obj.profile.org

    def validate_email(self, value):
        """One lead per address per org, matching the DB constraint exactly.

        `unique_lead_email_per_org` is a `UniqueConstraint(Lower("email"),
        "org")` conditional on a non-empty email, and nothing checked it before
        the insert, so a duplicate reached the database and came back as an
        IntegrityError, which the view does not catch. The caller got a 500 and
        no indication of which field was wrong.

        The comparison here has to be case-insensitive and has to skip empty
        values for the same reasons the constraint does, or the two disagree
        and the 500 comes back for the cases this misses.

        This is a check, not the enforcement. The constraint is still what
        guarantees it under a race. This exists so the ordinary case is a 400
        that names the field.
        """
        if not value:
            return value

        duplicates = Lead.objects.filter(org=self.org, email__iexact=value)
        if self.instance is not None:
            duplicates = duplicates.exclude(pk=self.instance.pk)
        if duplicates.exists():
            raise serializers.ValidationError(
                "Another lead in this organisation already uses that email address."
            )
        return value

    def validate_status(self, value):
        """Refuse transitions into and out of an irreversible status.

        Validate the *source* state, not just the target. On an update,
        `self.instance` is the lead as it stands in the database, which is the
        only trustworthy version. The client's idea of the current status is
        not evidence of anything.

        Two distinct failures are prevented here:

        1. Re-converting. `LeadDetailView.put` calls
           `convert_lead_to_account()` whenever the incoming status is
           "converted" and never checks whether it already happened, so a
           repeat PUT creates a second Opportunity against the same Account.
        2. Un-converting. The Account, Contact and Opportunity that conversion
           created are not removed when the status changes back, so the lead
           returns to the working list with duplicates already downstream of
           it.

        Creating a lead directly as "converted" is left alone: there is no
        prior state to contradict, and the create path does not run the
        conversion service.
        """
        if self.instance is None:
            return value

        current = self.instance.status
        if current not in IRREVERSIBLE_STATUSES:
            return value

        if value == current:
            raise serializers.ValidationError(
                f"This lead is already {current}. Converting it again would "
                "create a second opportunity against the same account."
            )
        raise serializers.ValidationError(
            f"A {current} lead cannot be changed back to '{value}'. The "
            "account, contact and opportunity created by the conversion are "
            "not removed, so the lead would reopen with duplicates already "
            "downstream of it."
        )

    class Meta:
        model = Lead
        fields = (
            # Core Lead Information
            "title",
            "salutation",
            "first_name",
            "last_name",
            "email",
            "phone",
            "job_title",
            "website",
            "linkedin_url",
            # Sales Pipeline
            "status",
            "source",
            "industry",
            "rating",
            "opportunity_amount",
            "currency",
            "probability",
            "close_date",
            # Address
            "address_line",
            "city",
            "state",
            "postcode",
            "country",
            # Activity
            "last_contacted",
            "next_follow_up",
            "description",
            # System
            "company_name",
            "is_active",
        )

    def create(self, validated_data):
        # Default currency from org if not provided and has opportunity_amount
        if not validated_data.get("currency") and validated_data.get(
            "opportunity_amount"
        ):
            request = self.context.get("request")
            if request and hasattr(request, "profile") and request.profile.org:
                validated_data["currency"] = request.profile.org.default_currency
        return super().create(validated_data)


class LeadCreateSwaggerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Lead
        fields = [
            # Core Lead Information
            "title",
            "salutation",
            "first_name",
            "last_name",
            "email",
            "phone",
            "job_title",
            "website",
            "linkedin_url",
            # Sales Pipeline
            "status",
            "source",
            "industry",
            "rating",
            "opportunity_amount",
            "probability",
            "close_date",
            # Address
            "address_line",
            "city",
            "state",
            "postcode",
            "country",
            # Assignment & Related
            "assigned_to",
            "teams",
            "contacts",
            "tags",
            # Activity
            "last_contacted",
            "next_follow_up",
            "description",
            # System
            "company_name",
        ]


class CreateLeadFromSiteSwaggerSerializer(serializers.Serializer):
    apikey = serializers.CharField()
    title = serializers.CharField()
    first_name = serializers.CharField()
    last_name = serializers.CharField()
    phone = serializers.CharField()
    email = serializers.CharField()
    source = serializers.CharField()
    description = serializers.CharField()


class LeadDetailEditSwaggerSerializer(serializers.Serializer):
    comment = serializers.CharField()
    lead_attachment = serializers.FileField()


class LeadCommentEditSwaggerSerializer(serializers.Serializer):
    comment = serializers.CharField()


class LeadUploadSwaggerSerializer(serializers.Serializer):
    leads_file = serializers.FileField()


class MarketingCampaignSerializer(StrictFieldsMixin, serializers.ModelSerializer):
    class Meta:
        model = MarketingCampaign
        fields = (
            "id",
            "code",
            "name",
            "status",
            "primary_channel",
            "starts_at",
            "ends_at",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")

    def validate_code(self, value):
        code = value.strip().lower()
        org = self.context["org"]
        if MarketingCampaign.objects.filter(org=org, code__iexact=code).exists():
            raise serializers.ValidationError(
                "A campaign with this code already exists in this organisation."
            )
        return code

    def validate(self, attrs):
        starts_at = attrs.get("starts_at")
        ends_at = attrs.get("ends_at")
        if starts_at and ends_at and ends_at < starts_at:
            raise serializers.ValidationError(
                {"ends_at": "Campaign end cannot precede start."}
            )
        return attrs


class LeadAttributionCreateSerializer(StrictFieldsMixin, serializers.Serializer):
    lead_ref = serializers.UUIDField()
    campaign_ref = serializers.UUIDField(required=False, allow_null=True)
    touch_type = serializers.ChoiceField(choices=("first", "assist", "last"))
    occurred_at = serializers.DateTimeField()
    source = serializers.RegexField(r"^[A-Za-z0-9._~+%-]+$", max_length=100)
    medium = serializers.RegexField(
        r"^[A-Za-z0-9._~+%-]*$", max_length=100, required=False, allow_blank=True
    )
    campaign_key = serializers.RegexField(
        r"^[A-Za-z0-9._~+%-]*$", max_length=160, required=False, allow_blank=True
    )
    content_key = serializers.RegexField(
        r"^[A-Za-z0-9._~+%-]*$", max_length=160, required=False, allow_blank=True
    )
    term_key = serializers.RegexField(
        r"^[A-Za-z0-9._~+%-]*$", max_length=160, required=False, allow_blank=True
    )
    landing_page_ref = serializers.RegexField(
        r"^[A-Za-z0-9._~-]*$", max_length=128, required=False, allow_blank=True
    )
    referrer_domain = serializers.RegexField(
        r"^[A-Za-z0-9.-]*$", max_length=253, required=False, allow_blank=True
    )
    lawful_basis = serializers.ChoiceField(
        choices=(
            "consent",
            "legitimate_interest",
            "contract",
            "legal_obligation",
        )
    )
    privacy_notice_version = serializers.RegexField(
        r"^[A-Za-z0-9._~-]+$", min_length=8, max_length=64
    )
    consent_evidence_ref = serializers.RegexField(
        r"^[A-Za-z0-9._~-]+$",
        min_length=8,
        max_length=128,
        required=False,
        allow_blank=True,
    )

    def validate(self, attrs):
        org = self.context["org"]
        lead = Lead.objects.filter(org=org, pk=attrs.pop("lead_ref")).first()
        if lead is None:
            raise serializers.ValidationError({"lead_ref": "Lead not found."})
        campaign_ref = attrs.pop("campaign_ref", None)
        campaign = None
        if campaign_ref:
            campaign = MarketingCampaign.objects.filter(
                org=org, pk=campaign_ref
            ).first()
            if campaign is None:
                raise serializers.ValidationError(
                    {"campaign_ref": "Campaign not found."}
                )
        if attrs["occurred_at"] > timezone.now():
            raise serializers.ValidationError(
                {"occurred_at": "Attribution touch cannot be in the future."}
            )
        if attrs["lawful_basis"] == "consent" and not attrs.get("consent_evidence_ref"):
            raise serializers.ValidationError(
                {"consent_evidence_ref": "Consent requires an evidence reference."}
            )
        if (
            not self.context.get("allow_existing_touch", False)
            and attrs["touch_type"] in {"first", "last"}
            and LeadAttributionTouch.objects.filter(
                lead=lead, touch_type=attrs["touch_type"]
            ).exists()
        ):
            raise serializers.ValidationError(
                {"touch_type": f"This lead already has a {attrs['touch_type']} touch."}
            )
        attrs["lead"] = lead
        attrs["campaign"] = campaign
        return attrs


class LeadAttributionSerializer(serializers.ModelSerializer):
    lead_ref = serializers.UUIDField(source="lead_id", read_only=True)
    campaign_ref = serializers.UUIDField(source="campaign_id", read_only=True)
    consent_evidence_present = serializers.SerializerMethodField()

    def get_consent_evidence_present(self, obj):
        return bool(obj.consent_evidence_ref)

    class Meta:
        model = LeadAttributionTouch
        fields = (
            "id",
            "lead_ref",
            "campaign_ref",
            "touch_type",
            "occurred_at",
            "source",
            "medium",
            "campaign_key",
            "content_key",
            "term_key",
            "landing_page_ref",
            "referrer_domain",
            "lawful_basis",
            "privacy_notice_version",
            "consent_evidence_present",
            "created_at",
        )
        read_only_fields = fields


class DataSubjectRequestCreateSerializer(StrictFieldsMixin, serializers.Serializer):
    lead_ref = serializers.UUIDField()
    request_type = serializers.ChoiceField(choices=("access", "correction", "deletion"))
    due_at = serializers.DateTimeField()

    def validate(self, attrs):
        org = self.context["org"]
        lead = Lead.objects.filter(org=org, pk=attrs["lead_ref"]).only("id").first()
        if lead is None:
            raise serializers.ValidationError({"lead_ref": "Lead not found."})
        now = timezone.now()
        if attrs["due_at"] <= now or attrs["due_at"] > now + timedelta(days=90):
            raise serializers.ValidationError(
                {"due_at": "Due date must be within the next 90 days."}
            )
        attrs["lead"] = lead
        return attrs


class DataSubjectRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = DataSubjectRequest
        fields = (
            "id",
            "request_type",
            "status",
            "version",
            "due_at",
            "legal_hold",
            "created_at",
        )
        read_only_fields = fields


class DataSubjectRequestActionSerializer(StrictFieldsMixin, serializers.Serializer):
    action = serializers.ChoiceField(
        choices=("verify_identity", "place_legal_hold", "release_legal_hold", "reject")
    )
    expected_version = serializers.IntegerField(min_value=1)
    reason_code = serializers.ChoiceField(
        choices=(
            "",
            "IDENTITY_NOT_VERIFIED",
            "DUPLICATE_REQUEST",
            "OUT_OF_SCOPE",
            "WITHDRAWN_BY_REQUESTER",
            "LEGAL_HOLD_REQUIRED",
        ),
        required=False,
        allow_blank=True,
    )
    evidence_ref = serializers.RegexField(
        r"^[A-Za-z0-9._~-]{8,128}$", required=False, allow_blank=True, write_only=True
    )

    def validate(self, attrs):
        action = attrs["action"]
        if action in {"verify_identity", "reject"} and not attrs.get("evidence_ref"):
            raise serializers.ValidationError(
                {"evidence_ref": "This action requires an opaque evidence reference."}
            )
        if action == "reject" and not attrs.get("reason_code"):
            raise serializers.ValidationError(
                {"reason_code": "Rejection requires an allowlisted reason code."}
            )
        return attrs


# ============================================
# Kanban Serializers
# ============================================


class LeadStageSerializer(serializers.ModelSerializer):
    """Serializer for lead stages."""

    lead_count = serializers.SerializerMethodField()

    class Meta:
        model = LeadStage
        fields = [
            "id",
            "name",
            "order",
            "color",
            "stage_type",
            "maps_to_status",
            "win_probability",
            "wip_limit",
            "lead_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ("id", "created_at", "updated_at", "org")

    def get_lead_count(self, obj):
        return obj.leads.count()


class LeadPipelineSerializer(serializers.ModelSerializer):
    """Serializer for lead pipelines with nested stages."""

    stages = LeadStageSerializer(many=True, read_only=True)
    stage_count = serializers.SerializerMethodField()
    lead_count = serializers.SerializerMethodField()

    class Meta:
        model = LeadPipeline
        fields = [
            "id",
            "name",
            "description",
            "is_default",
            "is_active",
            "stages",
            "stage_count",
            "lead_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ("id", "created_at", "updated_at", "org")

    def get_stage_count(self, obj):
        return obj.stages.count()

    def get_lead_count(self, obj):
        return Lead.objects.filter(stage__pipeline=obj).count()


class LeadPipelineListSerializer(serializers.ModelSerializer):
    """Simplified pipeline serializer for lists."""

    stage_count = serializers.SerializerMethodField()
    lead_count = serializers.SerializerMethodField()

    class Meta:
        model = LeadPipeline
        fields = [
            "id",
            "name",
            "description",
            "is_default",
            "is_active",
            "stage_count",
            "lead_count",
            "created_at",
        ]

    def get_stage_count(self, obj):
        return obj.stages.count()

    def get_lead_count(self, obj):
        return Lead.objects.filter(stage__pipeline=obj).count()


class LeadKanbanCardSerializer(serializers.ModelSerializer):
    """Lightweight serializer for kanban cards (minimal fields for performance)."""

    assigned_to = ProfileSerializer(read_only=True, many=True)
    full_name = serializers.SerializerMethodField()

    class Meta:
        model = Lead
        fields = [
            "id",
            "title",
            "full_name",
            "company_name",
            "email",
            "rating",
            "opportunity_amount",
            "currency",
            "status",
            "stage",
            "kanban_order",
            "next_follow_up",
            "is_follow_up_overdue",
            "assigned_to",
            "created_at",
        ]

    def get_full_name(self, obj):
        return str(obj)


class LeadMoveSerializer(serializers.Serializer):
    """Serializer for moving leads in kanban."""

    stage_id = serializers.UUIDField(required=False, allow_null=True)
    status = serializers.ChoiceField(choices=LEAD_STATUS, required=False)
    kanban_order = serializers.DecimalField(
        max_digits=15, decimal_places=6, required=False
    )
    above_lead_id = serializers.UUIDField(required=False, allow_null=True)
    below_lead_id = serializers.UUIDField(required=False, allow_null=True)

    def validate(self, attrs):
        # Must provide either stage_id or status
        if not attrs.get("stage_id") and not attrs.get("status"):
            raise serializers.ValidationError(
                "Either stage_id or status must be provided"
            )
        return attrs
