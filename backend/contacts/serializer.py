from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from common.serializer import (
    AttachmentsSerializer,
    OrganizationSerializer,
    ProfileSerializer,
    TeamsSerializer,
)
from contacts.models import Contact

# Note: Removed unused serializer properties that were computed but never used by frontend:
# - get_team_users, get_team_and_assigned_users, get_assigned_users_not_in_teams
# - created_on_arrow (frontend computes its own humanized timestamps)


class ContactSerializer(serializers.ModelSerializer):
    """Serializer for reading Contact data"""

    teams = TeamsSerializer(read_only=True, many=True)
    assigned_to = ProfileSerializer(read_only=True, many=True)
    contact_attachment = AttachmentsSerializer(read_only=True, many=True)
    org = OrganizationSerializer()
    account_detail = serializers.SerializerMethodField()
    linked_accounts = serializers.SerializerMethodField()

    @extend_schema_field(dict)
    def get_account_detail(self, obj):
        """The `account` FK, resolved to something a page can print.

        The bare field is a UUID, so every caller that wanted to show which
        company somebody works for had to either fetch the account separately
        or fall back to `organization` -- which is free text and is frequently
        a *different* company from the linked one.
        """
        if not obj.account_id:
            return None
        return {"id": str(obj.account.id), "name": obj.account.name}

    @extend_schema_field(list)
    def get_linked_accounts(self, obj):
        """Membership of `Account.contacts`, which is the other account link.

        A Contact is joined to an Account twice over: this many-to-many, and
        the `account` FK the model calls "primary". They are independent, and
        in practice it is this one that carries the data -- the accounts page
        builds its people list from it. A caller that reads only the FK shows
        nobody an account; one that reads only the M2M cannot say which of two
        or three is the main one. Both are published so the choice is the
        reader's and is made in the open.
        """
        return [
            {"id": str(account.id), "name": account.name}
            for account in obj.account_contacts.all()
        ]

    class Meta:
        model = Contact
        fields = (
            "id",
            # Core Contact Information
            "first_name",
            "last_name",
            "email",
            "phone",
            # Professional Information
            "organization",
            "title",
            "department",
            # Communication Preferences
            "do_not_call",
            "linkedin_url",
            # Address
            "address_line",
            "city",
            "state",
            "postcode",
            "country",
            # Assignment
            "assigned_to",
            "teams",
            # Tags
            "tags",
            # Notes
            "description",
            # System
            "created_by",
            "created_at",
            "updated_at",
            "is_active",
            "org",
            "account",
            "account_detail",
            "linked_accounts",
            "contact_attachment",
            # Per-org custom fields (validated via common.custom_fields)
            "custom_fields",
        )


class ContactPickerSerializer(serializers.ModelSerializer):
    """A contact as an option in a form's contact select.

    The `contacts_list` catalogues on `/api/cases/`, `/api/opportunities/` and
    `/api/tasks/` used the full `ContactSerializer`, one row per contact in
    reach, each with its email, phone, address, notes and assignees. The web
    builds the option label from the name and mobile does not read them at
    all. The twin of `accounts.serializer.AccountPickerSerializer`.
    """

    class Meta:
        model = Contact
        fields = ("id", "first_name", "last_name")


class ContactLinkSerializer(ContactPickerSerializer):
    """A person linked to a ticket, deal or task: the picker's name, plus email.

    Those records nested the full `ContactSerializer`, so opening one handed
    over every linked person's phone, address and notes, contact access or
    not. The clients read the name and the email (mobile's deal screen shows
    it; its ticket model and the web's task page fall back to it when the
    name is blank), and the rest is on `/api/contacts/<id>/`.
    """

    class Meta(ContactPickerSerializer.Meta):
        fields = ContactPickerSerializer.Meta.fields + ("email",)


class CreateContactSerializer(serializers.ModelSerializer):
    """Serializer for creating/updating Contact data"""

    def __init__(self, *args, **kwargs):
        request_obj = kwargs.pop("request_obj", None)
        super().__init__(*args, **kwargs)
        # Always defined, so that a caller who forgets `request_obj` gets a
        # refusal from the org checks below rather than an AttributeError --
        # or, worse, a check that quietly passes because there was nothing to
        # compare against.
        self.org = request_obj.profile.org if request_obj else None

    def validate_account(self, account):
        """An account from somebody else's org is not a valid link.

        `account` is a plain ModelSerializer field, so DRF resolved it against
        `Account.objects.all()` -- every account in the database, not the ones
        this org can see. Passing a stranger's UUID attached one org's contact
        to another org's account and returned 200. Row-level security stops
        that in a correctly configured deployment, because the lookup runs
        under the tenant policy; it did not stop it here, where the dev role is
        a superuser. The org filter is the contract either way.
        """
        if account is not None and (self.org is None or account.org_id != self.org.id):
            raise serializers.ValidationError("No such account.")
        return account

    def validate_email(self, email):
        if email:
            if self.instance:
                if (
                    Contact.objects.filter(email__iexact=email, org=self.org)
                    .exclude(id=self.instance.id)
                    .exists()
                ):
                    raise serializers.ValidationError(
                        "Contact already exists with this email"
                    )
            else:
                if Contact.objects.filter(email__iexact=email, org=self.org).exists():
                    raise serializers.ValidationError(
                        "Contact already exists with this email"
                    )
        return email

    class Meta:
        model = Contact
        fields = (
            # Core Contact Information
            "first_name",
            "last_name",
            "email",
            "phone",
            # Professional Information
            "organization",
            "title",
            "department",
            # Communication Preferences
            "do_not_call",
            "linkedin_url",
            # Address
            "address_line",
            "city",
            "state",
            "postcode",
            "country",
            # Notes
            "description",
            # Account
            "account",
            # Status
            "is_active",
        )


class ContactDetailEditSwaggerSerializer(serializers.Serializer):
    comment = serializers.CharField()
    contact_attachment = serializers.FileField()


class ContactCommentEditSwaggerSerializer(serializers.Serializer):
    comment = serializers.CharField()
