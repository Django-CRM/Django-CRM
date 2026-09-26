from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from accounts.models import Account, AccountEmail, AccountEmailLog
from cases.access import visible_cases_qs
from common.serializer import (
    AttachmentsSerializer,
    OrganizationSerializer,
    ProfileSerializer,
    TagsSerializer,
    TeamsSerializer,
    UserSerializer,
)
from contacts.access import visible_contacts_qs
from contacts.serializer import ContactSerializer
from opportunity.access import visible_deals_qs
from opportunity.stages import stage_index
from tasks.access import visible_tasks_qs

# Note: Removed unused serializer properties that were computed but never used by frontend:
# - get_team_users, get_team_and_assigned_users, get_assigned_users_not_in_teams
# - created_on_arrow (frontend computes its own humanized timestamps)


class AccountSerializer(serializers.ModelSerializer):
    """Serializer for reading Account data"""

    created_by = UserSerializer()
    org = OrganizationSerializer()
    tags = TagsSerializer(read_only=True, many=True)
    assigned_to = ProfileSerializer(read_only=True, many=True)
    contacts = serializers.SerializerMethodField()
    teams = TeamsSerializer(read_only=True, many=True)
    account_attachment = AttachmentsSerializer(read_only=True, many=True)
    country_display = serializers.SerializerMethodField()
    cases = serializers.SerializerMethodField()
    tasks = serializers.SerializerMethodField()
    opportunities = serializers.SerializerMethodField()
    rollups = serializers.SerializerMethodField()

    @extend_schema_field(str)
    def get_country_display(self, obj):
        return obj.get_country_display() if obj.country else None

    @extend_schema_field(dict)
    def get_rollups(self, obj):
        """What this account is worth, owes and is complaining about.

        Computed by `accounts.views.annotate_rollups` (counts) and
        `attach_money_rollups` (money, per currency), which the list and detail
        endpoints apply, over only the deals, tickets and invoices the viewer
        may open. It is deliberately absent, `null`, rather than
        zero-filled anywhere else: a page that was never given the numbers
        should say nothing, not quietly claim every total is zero.
        """
        from accounts.views import ROLLUP_FIELDS

        if not hasattr(obj, "money_rollups"):
            return None
        return {
            **{field: getattr(obj, field) for field in ROLLUP_FIELDS},
            **obj.money_rollups,
        }

    # The records hanging off the account. Being able to see an account, or a
    # ticket, deal or invoice that nests one, is not a licence to read every
    # record on it, so these lists are emitted only for a caller that names the
    # viewer with `context={"profile": ...}` (`AccountDetailView` does) and are
    # absent everywhere else. Absent rather than empty: an empty list would
    # claim the account has none. Each is then narrowed to the viewer's read
    # rule. `contacts` matches the detail view's top-level `contacts`; sending
    # it back is safe, because every contact write keeps the linked contacts
    # the caller cannot see (`contacts.access.replace_visible_contacts`).
    RELATED_FIELDS = ("contacts", "cases", "tasks", "opportunities")

    def get_fields(self):
        fields = super().get_fields()
        if self.context.get("profile") is None:
            for name in self.RELATED_FIELDS:
                del fields[name]
        return fields

    @extend_schema_field(ContactSerializer(many=True))
    def get_contacts(self, obj):
        """Contacts on this account that the viewer may open."""
        visible = visible_contacts_qs(self.context["profile"]).values("id")
        contacts = obj.contacts.filter(id__in=visible)
        return ContactSerializer(contacts, many=True, context=self.context).data

    @extend_schema_field(list)
    def get_cases(self, obj):
        """Cases on this account that the viewer may open."""
        cases = visible_cases_qs(self.context["profile"]).filter(account=obj)
        return [{"id": str(c.id), "name": c.name} for c in cases]

    @extend_schema_field(list)
    def get_tasks(self, obj):
        """Tasks on this account that the viewer may open."""
        tasks = visible_tasks_qs(self.context["profile"]).filter(account=obj)
        return [{"id": str(t.id), "title": t.title} for t in tasks]

    @extend_schema_field(list)
    def get_opportunities(self, obj):
        """Deals on this account that the viewer may open.

        `stage` is the code, `stage_label` what the org calls it: stages are
        configurable per pipeline, so a code alone reads as `DEMO_BOOKED`.
        One read of the org's stages serves every row.
        """
        profile = self.context["profile"]
        deals = list(visible_deals_qs(profile, profile.user).filter(account=obj))
        stages = stage_index(obj.org_id) if deals else {}
        rows = []
        for o in deals:
            stage = o.current_stage(stages)
            rows.append(
                {
                    "id": str(o.id),
                    "name": o.name,
                    "stage": o.stage,
                    "stage_label": stage.label if stage else o.stage,
                    "amount": str(o.amount) if o.amount else "0",
                }
            )
        return rows

    class Meta:
        model = Account
        fields = (
            "id",
            # Core Account Information
            "name",
            "email",
            "phone",
            "website",
            # Business Information
            "industry",
            "number_of_employees",
            "annual_revenue",
            "currency",
            # Address
            "address_line",
            "city",
            "state",
            "postcode",
            "country",
            "country_display",
            # Assignment
            "assigned_to",
            "teams",
            "contacts",
            # Tags
            "tags",
            # Notes
            "description",
            # Related
            "account_attachment",
            "cases",
            "tasks",
            "opportunities",
            "rollups",
            # System
            "created_by",
            "created_at",
            "is_active",
            "org",
            # Per-org custom fields (validated via common.custom_fields)
            "custom_fields",
        )


class AccountPickerSerializer(serializers.ModelSerializer):
    """An account as an option in a form's account select: `id` and `name`.

    The `accounts_list` catalogues on `/api/cases/`, `/api/opportunities/` and
    `/api/tasks/` used the full `AccountSerializer`, one row per account in
    reach, each carrying the account's contacts, deals, tickets and tasks.
    The web reads only these two fields from them, and mobile does not read
    them at all.
    """

    class Meta:
        model = Account
        fields = ("id", "name")


class EmailSerializer(serializers.ModelSerializer):
    def __init__(self, *args, **kwargs):
        # `AccountCreateMailView` passes `request_obj=request`, matching the
        # call shape of `AccountCreateSerializer` next door. This class did not
        # pop it and forwarded `**kwargs` straight to `super()`, so DRF's
        # `Serializer.__init__` raised `TypeError` and the endpoint failed on
        # every call it had ever received. The org is derived from the request
        # in the view rather than here, so the value itself is not needed; the
        # kwarg is accepted so the two sibling serializers stay callable the
        # same way.
        kwargs.pop("request_obj", None)
        super().__init__(*args, **kwargs)

    class Meta:
        model = AccountEmail
        fields = (
            "message_subject",
            "message_body",
            "timezone",
            "scheduled_date_time",
            "scheduled_later",
            "created_at",
            "from_email",
            "rendered_message_body",
        )

    def validate_message_body(self, message_body):
        count = 0
        for i in message_body:
            if i == "{":
                count += 1
            elif i == "}":
                count -= 1
            if count < 0:
                raise serializers.ValidationError(
                    "Brackets do not match, Enter valid tags."
                )
        if count != 0:
            raise serializers.ValidationError(
                "Brackets do not match, Enter valid tags."
            )
        return message_body


class EmailLogSerializer(serializers.ModelSerializer):
    email = EmailSerializer()

    class Meta:
        model = AccountEmailLog
        fields = ["email", "contact", "is_sent"]


class AccountWriteSerializer(serializers.ModelSerializer):
    """Serializer for API documentation of Account write operations"""

    class Meta:
        model = Account
        fields = [
            "name",
            "phone",
            "email",
            "website",
            "industry",
            "number_of_employees",
            "annual_revenue",
            "address_line",
            "city",
            "state",
            "postcode",
            "country",
            "description",
        ]


class AccountCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating/updating Account data"""

    def __init__(self, *args, **kwargs):
        request_obj = kwargs.pop("request_obj", None)
        kwargs.pop("account", None)  # Remove unused 'account' parameter passed by views
        super().__init__(*args, **kwargs)
        if request_obj:
            self.org = request_obj.profile.org

    def validate_name(self, name):
        if self.instance:
            if self.instance.name != name:
                if not Account.objects.filter(name__iexact=name, org=self.org).exists():
                    return name
                raise serializers.ValidationError(
                    "Account already exists with this name"
                )
            return name
        if not Account.objects.filter(name__iexact=name, org=self.org).exists():
            return name
        raise serializers.ValidationError("Account already exists with this name")

    def validate_annual_revenue(self, annual_revenue):
        """Reject negative revenue here rather than letting the database do it.

        `Account` carries a `account_revenue_non_negative` CheckConstraint, so
        the value could never be stored, but with nothing in front of it the
        constraint surfaced as an IntegrityError, i.e. a 500, with no indication
        of which field was at fault. `number_of_employees` is a
        PositiveIntegerField and DRF derives `min_value=0` from it for free,
        which is why the sibling field already answered 400 and this one did not.
        """
        if annual_revenue is not None and annual_revenue < 0:
            raise serializers.ValidationError("Annual revenue cannot be negative.")
        return annual_revenue

    class Meta:
        model = Account
        fields = (
            # Core Account Information
            "name",
            "email",
            "phone",
            "website",
            # Business Information
            "industry",
            "number_of_employees",
            "annual_revenue",
            "currency",
            # Address
            "address_line",
            "city",
            "state",
            "postcode",
            "country",
            # Notes
            "description",
            # Status
            "is_active",
        )

    def create(self, validated_data):
        # Default currency from org if not provided and has annual_revenue
        if not validated_data.get("currency") and validated_data.get("annual_revenue"):
            request = self.context.get("request")
            if request and hasattr(request, "profile") and request.profile.org:
                validated_data["currency"] = request.profile.org.default_currency
        return super().create(validated_data)


class AccountDetailEditSwaggerSerializer(serializers.Serializer):
    comment = serializers.CharField()
    account_attachment = serializers.FileField()


class AccountCommentEditSwaggerSerializer(serializers.Serializer):
    comment = serializers.CharField()


class EmailWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = AccountEmail
        fields = (
            "from_email",
            "recipients",
            "message_subject",
            "scheduled_later",
            "timezone",
            "scheduled_date_time",
            "message_body",
        )
