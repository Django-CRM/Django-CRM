import re

from disposable_email_domains import blocklist as disposable_domains
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from rest_framework.validators import UniqueValidator
from rest_framework_simplejwt.tokens import RefreshToken

from common.custom_fields import (
    is_supported_target,
    validate_definition_options,
)
from common.models import (
    Activity,
    Address,
    APISettings,
    Attachments,
    Comment,
    CustomFieldDefinition,
    Document,
    Notification,
    Org,
    PersonalAccessToken,
    Profile,
    Tags,
    Teams,
    User,
)
from common.permissions import is_org_admin
from common.utils import CURRENCY_SYMBOLS
from common.validators import flexible_phone_validator


class OrgAwareRefreshToken(RefreshToken):
    """
    Custom RefreshToken that includes org context in the token payload.

    This ensures the org context is cryptographically signed and cannot be
    forged by the client. The middleware should validate org_id from this
    token instead of trusting the org header.

    Embedded claims (to avoid extra API calls):
    - org_id: Organization UUID
    - org_name: Organization name (for display)
    - role: User's role in the org (ADMIN/USER)
    - org_settings: Currency and locale settings
    """

    @classmethod
    def for_user_and_org(cls, user, org, profile=None):
        """
        Generate a refresh token for a user with org context.

        Args:
            user: User instance
            org: Org instance or org_id UUID
            profile: Optional Profile instance for role

        Returns:
            OrgAwareRefreshToken with org claims
        """
        token = cls.for_user(user)

        # Add user info to token (avoids extra API calls for display)
        if user:
            token["user_email"] = user.email
            # Build display name from email (User model doesn't have first/last name)
            token["user_name"] = user.email.split("@")[0] if user.email else ""
            token["user_profile_pic"] = user.profile_pic or ""

        # Add org context to the token payload
        if org:
            org_id = str(org.id) if hasattr(org, "id") else str(org)
            token["org_id"] = org_id
            # Add org_name for display (avoids /api/auth/profile call)
            if hasattr(org, "name"):
                token["org_name"] = org.name
            # Add org settings for currency/locale
            if hasattr(org, "default_currency"):
                token["org_settings"] = {
                    "default_currency": org.default_currency or "USD",
                    "currency_symbol": CURRENCY_SYMBOLS.get(
                        org.default_currency or "USD", "$"
                    ),
                    "default_country": org.default_country,
                }

        # Add role if profile provided (avoids /api/auth/profile call)
        if profile:
            token["role"] = profile.role

        return token


class OrganizationSerializer(serializers.ModelSerializer):
    """Public org representation, nested into most CRM records.

    `api_key` is deliberately excluded: it authenticates its bearer as the
    org's first ADMIN profile (see `common.external_auth.APIKeyAuthentication`),
    so including it here would hand every org member -- including role="USER"
    -- a path to admin. Admins read and rotate it via `OrgApiKeyView`.
    """

    class Meta:
        model = Org
        fields = ("id", "name")


class OrgSettingsSerializer(serializers.ModelSerializer):
    """The org's own settings: company profile, locale, and case-handling switches.

    This is the ONLY writable representation of the org an admin edits through the
    settings page (`OrgSettingsView`). The field list is an allow-list on purpose:

    - `api_key` is absent. It authenticates its bearer as the org's first ADMIN
      profile (see `OrganizationSerializer`), so it is never editable or readable
      through a settings form; admins rotate it via `OrgApiKeyView`.
    - `is_active` (the org-level kill switch) and `id` are not writable here either
      -- an org must not be able to deactivate itself, or change its own id, from a
      profile-edit form.

    `csat_enabled` and `auto_close_children_on_parent_close` are real `Org` columns
    that previously had NO write path through any serializer; this is where they are
    edited. `member_count` and `created_at` are read-only header context.

    `vertical` and `terminology` are read-only here too: they are written exclusively
    by the vertical-pack applier (`common/packs/applier.py`), never by a client. A
    writable path would let any admin PATCH `terminology` to arbitrary tenant-supplied
    text (still safe to render -- see `$lib/terminology.js` on the frontend -- but not
    something this endpoint is meant to let anyone set by hand) or overwrite `vertical`,
    which is meant to record which pack was actually applied.
    """

    currency_symbol = serializers.SerializerMethodField()
    logo_url = serializers.SerializerMethodField()
    member_count = serializers.SerializerMethodField()

    class Meta:
        model = Org
        fields = [
            "id",
            "name",
            # Company profile
            "company_name",
            "logo",
            "logo_url",
            "address_line",
            "city",
            "state",
            "postcode",
            "country",
            "phone",
            "email",
            "website",
            "tax_id",
            # Locale settings
            "default_currency",
            "default_country",
            "currency_symbol",
            "timezone",
            # Case-handling behaviour (org-wide switches). Editable only here.
            "csat_enabled",
            "auto_close_children_on_parent_close",
            # Vertical pack. Read-only -- see the class docstring.
            "vertical",
            "terminology",
            # Read-only context for the settings page header.
            "member_count",
            "created_at",
        ]
        read_only_fields = [
            "id",
            "currency_symbol",
            "logo_url",
            "member_count",
            "created_at",
            "vertical",
            "terminology",
        ]

    @extend_schema_field(str)
    def get_currency_symbol(self, obj):
        return CURRENCY_SYMBOLS.get(obj.default_currency or "USD", "$")

    @extend_schema_field(str)
    def get_logo_url(self, obj):
        if obj.logo:
            request = self.context.get("request")
            if request:
                return request.build_absolute_uri(obj.logo.url)
            return obj.logo.url
        return None

    @extend_schema_field(int)
    def get_member_count(self, obj):
        # Active members in this org, for the settings header. Naturally
        # org-scoped: obj is always request.profile.org.
        return obj.profiles.filter(is_active=True).count()


class TagsSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tags
        fields = (
            "id",
            "name",
            "slug",
            "color",
            "description",
            "is_active",
            "created_at",
        )
        read_only_fields = ("id", "slug", "created_at")


class SocialLoginSerializer(serializers.Serializer):
    token = serializers.CharField()


class CommentUserSerializer(serializers.ModelSerializer):
    """Simplified user serializer for comments"""

    user_details = serializers.SerializerMethodField()

    class Meta:
        model = Profile
        fields = ("id", "user_details")

    def get_user_details(self, obj):
        if obj.user:
            return {"email": obj.user.email, "profile_pic": obj.user.profile_pic}
        return None


class CommentSerializer(serializers.ModelSerializer):
    """Serializer for Comment model using ContentType"""

    content_type = serializers.SlugRelatedField(slug_field="model", read_only=True)
    commented_by = CommentUserSerializer(read_only=True)

    class Meta:
        model = Comment
        fields = (
            "id",
            "comment",
            "commented_on",
            "commented_by",
            "content_type",
            "object_id",
            "org",
            "is_internal",
        )
        # Which record a comment hangs off, and which org owns it, are the
        # server's to decide. Left writable, the edit endpoints (which are
        # `partial=True` and gated only on "you wrote it") let an author move
        # their own comment onto any record in the org by id, including cases
        # `cases.access` would refuse them, or restamp it into another org.
        # `Comment.clean()` compares the new org against the new target's org,
        # so a matching cross-org pair passed that check and saved; only the
        # RLS policy stopped it, and RLS is the safety net, not the contract.
        #
        # Making them read-only also repairs the edit endpoints. This
        # serializer is used non-partial by four PUT handlers, where
        # `object_id` and `org` were required fields no honest client sends,
        # so every ordinary "fix my typo" edit failed validation.
        #
        # Callers that legitimately set these do it through `save()`, where the
        # value comes from the server rather than the request body.
        read_only_fields = (
            "commented_by",
            "content_type",
            "object_id",
            "org",
        )


class CommentCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating comments with ContentType"""

    content_type = serializers.CharField(write_only=True)
    object_id = serializers.UUIDField(write_only=True)

    class Meta:
        model = Comment
        fields = (
            "comment",
            "content_type",
            "object_id",
        )

    def create(self, validated_data):
        from django.contrib.contenttypes.models import ContentType

        content_type_str = validated_data.pop("content_type")
        try:
            content_type = ContentType.objects.get(model=content_type_str.lower())
        except ContentType.DoesNotExist as exc:
            raise serializers.ValidationError(
                f"Invalid content type: {content_type_str}"
            ) from exc

        validated_data["content_type"] = content_type
        return super().create(validated_data)


class NotificationSerializer(serializers.ModelSerializer):
    """In-app notification. List/feed shape returned by GET and SSE."""

    actor = CommentUserSerializer(read_only=True)

    class Meta:
        model = Notification
        fields = (
            "id",
            "verb",
            "actor",
            "entity_type",
            "entity_id",
            "entity_name",
            "data",
            "link",
            "read_at",
            "created_at",
        )
        read_only_fields = fields


class LeadCommentSerializer(serializers.ModelSerializer):
    """Comment serializer with user details for display"""

    commented_by = CommentUserSerializer(read_only=True)

    class Meta:
        model = Comment
        fields = (
            "id",
            "comment",
            "commented_on",
            "commented_by",
        )


class CustomFieldDefinitionSerializer(serializers.ModelSerializer):
    """Per-org custom-field definition. key/target_model/field_type are immutable
    after creation. Admins must create a new definition to change shape."""

    class Meta:
        model = CustomFieldDefinition
        fields = (
            "id",
            "target_model",
            "key",
            "label",
            "field_type",
            "options",
            "is_required",
            "is_filterable",
            "display_order",
            "is_active",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")

    def validate_target_model(self, value):
        if not is_supported_target(value):
            raise serializers.ValidationError(
                f"target_model {value!r} is not yet wired for custom fields"
            )
        return value

    def validate_key(self, value):
        if not value:
            raise serializers.ValidationError("key is required")
        if not re.fullmatch(r"[a-z][a-z0-9_]*", value):
            raise serializers.ValidationError(
                "key must be a lowercase slug starting with a letter (a-z, 0-9, _)"
            )
        return value

    def validate(self, attrs):
        if self.instance is not None:
            for frozen in ("key", "target_model", "field_type"):
                if frozen in attrs and getattr(self.instance, frozen) != attrs[frozen]:
                    raise serializers.ValidationError(
                        {frozen: f"{frozen} cannot be changed after creation"}
                    )
            field_type = attrs.get("field_type", self.instance.field_type)
        else:
            field_type = attrs.get("field_type")

        options = attrs.get("options", getattr(self.instance, "options", None))
        validate_definition_options(field_type, options)

        org = self.context.get("org")
        target_model = attrs.get(
            "target_model", getattr(self.instance, "target_model", None)
        )
        key = attrs.get("key", getattr(self.instance, "key", None))
        if org is not None and target_model and key:
            qs = CustomFieldDefinition.objects.filter(
                org=org, target_model=target_model, key=key
            )
            if self.instance is not None:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise serializers.ValidationError(
                    {"key": f"a {target_model} field with key {key!r} already exists"}
                )

        return attrs


class ActivitySerializer(serializers.ModelSerializer):
    """Activity timeline row, used by audit-log feeds (Cases first)."""

    user = CommentUserSerializer(read_only=True)

    class Meta:
        model = Activity
        fields = (
            "id",
            "action",
            "user",
            "entity_type",
            "entity_id",
            "entity_name",
            "description",
            "metadata",
            "created_at",
        )


class OrgProfileCreateSerializer(serializers.ModelSerializer):
    """
    It is for creating organization
    """

    name = serializers.CharField(max_length=255)

    class Meta:
        model = Org
        # "id" is Org's UUIDField(editable=False) primary key -- DRF makes it
        # read-only automatically because of `editable=False`, so listing it
        # here only exposes the created org's id in the response; it does not
        # open a new mass-assignment surface (a client-supplied "id" in the
        # request body is still ignored, same as before this field existed).
        # Callers (org/new/+page.server.js) need this id to switch the
        # session into the org they just created and to apply a vertical
        # pack to it -- see the C1 fix in the vertical-packs final review.
        # `timezone` is optional on purpose. Mobile builds ship from an app
        # store and update whenever the user gets round to it, so requiring a
        # field an installed build does not send would break org creation for
        # them with no server-side fix available. Omitted means UTC, which the
        # admin can change in settings afterwards.
        fields = ["id", "name", "timezone"]
        extra_kwargs = {
            "name": {"required": True},
            "timezone": {"required": False},
        }

    def validate_name(self, name):
        if bool(re.search(r"[~\!@#\$%\^&\*\(\)\+{}\":;'/\[\]]", name)):
            raise serializers.ValidationError(
                "organization name should not contain any special characters"
            )
        if Org.objects.filter(name=name).exists():
            raise serializers.ValidationError(
                "Organization already exists with this name"
            )
        return name


class ShowOrganizationListSerializer(serializers.ModelSerializer):
    """
    we are using it for show orjanization list
    """

    org = OrganizationSerializer()

    class Meta:
        model = Profile
        fields = (
            "role",
            "alternate_phone",
            "has_sales_access",
            "has_marketing_access",
            "is_organization_admin",
            "org",
        )


class BillingAddressSerializer(serializers.ModelSerializer):
    class Meta:
        model = Address
        fields = ("address_line", "street", "city", "state", "postcode", "country")

    def __init__(self, *args, **kwargs):
        account_view = kwargs.pop("account", False)

        super().__init__(*args, **kwargs)

        if account_view:
            self.fields["address_line"].required = True
            self.fields["street"].required = True
            self.fields["city"].required = True
            self.fields["state"].required = True
            self.fields["postcode"].required = True
            self.fields["country"].required = True


class CreateUserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = (
            "email",
            "profile_pic",
        )

    def __init__(self, *args, **kwargs):
        self.org = kwargs.pop("org", None)
        super().__init__(*args, **kwargs)
        self.fields["email"].required = True
        # Membership is per-org: one account can belong to several orgs, so the
        # model-level unique check would wrongly reject inviting someone who
        # already signed up elsewhere. validate_email enforces the real rule --
        # one profile per email per org -- and the view reuses the existing
        # User instead of creating a duplicate.
        self.fields["email"].validators = [
            validator
            for validator in self.fields["email"].validators
            if not isinstance(validator, UniqueValidator)
        ]

    def validate_email(self, email):
        if self.instance:
            if self.instance.email.lower() == email.lower():
                return email
            # Renaming an account: the address must be free globally, since
            # there is no way to fold two existing accounts together.
            if (
                User.objects.filter(email__iexact=email)
                .exclude(pk=self.instance.pk)
                .exists()
            ):
                raise serializers.ValidationError("Email already exists")
            if Profile.objects.filter(user__email__iexact=email, org=self.org).exists():
                raise serializers.ValidationError("Email already exists")
            return email
        # Creating a membership: an account owned elsewhere is reused by the
        # view, so only same-org duplicates are rejected here.
        if not Profile.objects.filter(user__email__iexact=email, org=self.org).exists():
            return email.lower()
        raise serializers.ValidationError("Given Email id already exists")


class CreateProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = Profile
        # `is_organization_admin` is deliberately absent. It is derived from
        # `role` in `Profile.save`, and accepting it here was a second, hidden
        # way to grant admin: no frontend surface shows or sets the flag, so a
        # profile carrying it read as a plain user everywhere a human looked
        # while `is_org_admin` answered True. One fact, one input.
        fields = (
            "role",
            "phone",
            "alternate_phone",
            "has_sales_access",
            "has_marketing_access",
        )

    # The fields that grant access rather than describe a person. Only an admin
    # acting on someone other than themselves may set these; see the caller
    # notes in common/views/user_views.py.
    PRIVILEGED_FIELDS = (
        "role",
        "has_sales_access",
        "has_marketing_access",
    )

    def __init__(self, *args, **kwargs):
        # `can_grant_privileges` gates the fields above. It defaults to False so
        # a caller that forgets to pass it fails closed: the worst outcome of a
        # missing flag is that a privileged field is ignored, never that an
        # unprivileged caller gets to set one. A plain member editing their own
        # profile could otherwise PATCH themselves to role="ADMIN". A live
        # privilege escalation this closes by making these fields read_only
        # (so they are silently ignored) when the caller may not grant them.
        can_grant = kwargs.pop("can_grant_privileges", False)
        super().__init__(*args, **kwargs)
        self._can_grant_privileges = can_grant
        self.fields["alternate_phone"].required = False
        self.fields["phone"].required = False
        if can_grant:
            self.fields["role"].required = True
        else:
            for name in self.PRIVILEGED_FIELDS:
                # read_only and required are mutually exclusive in DRF, so this
                # also drops role's requirement for the self-edit path.
                self.fields[name].read_only = True

    def validate(self, attrs):
        """Say no out loud when a privileged field was actually being changed.

        Making the fields read_only above is what makes this safe, and it is
        the half that must never be removed. It is not the half a caller can
        see: DRF drops a read_only field without a word, so `PATCH {"role":
        "USER"}` on your own profile answered ``200 User Updated Successfully``
        and changed nothing. Both clients then told the user the role had
        changed, which is a worse failure than a refusal.

        Only a *different* value is refused. A client echoing the whole profile
        back, including the role it already has, is asking for no change and
        gets none, the same as before.
        """
        if self._can_grant_privileges or self.instance is None:
            return attrs
        submitted = self.initial_data if isinstance(self.initial_data, dict) else {}
        errors = {
            name: ["Only an admin can change this, and never on themselves."]
            for name in self.PRIVILEGED_FIELDS
            if name in submitted
            and not self._unchanged(submitted[name], getattr(self.instance, name))
        }
        if errors:
            raise serializers.ValidationError(errors)
        return attrs

    @staticmethod
    def _unchanged(submitted, current):
        """Is the submitted value the one already stored?

        Compared as text, and as a boolean when the stored value is one, since
        a form sends ``"true"`` where the column holds ``True``.
        """
        if isinstance(current, bool):
            accepted = (
                serializers.BooleanField.TRUE_VALUES
                if current
                else serializers.BooleanField.FALSE_VALUES
            )
            # Compared one by one rather than with `in`, because a caller can
            # send a list or a dict here and those are not hashable.
            return any(submitted == value for value in accepted)
        return str(submitted) == str(current)


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "email", "name", "profile_pic"]


class ProfileSerializer(serializers.ModelSerializer):
    # address = BillingAddressSerializer()
    user_details = serializers.SerializerMethodField()

    @extend_schema_field(dict)
    def get_user_details(self, obj):
        return obj.user_details

    class Meta:
        model = Profile
        fields = (
            "id",
            "user_details",
            "role",
            "address",
            "has_marketing_access",
            "has_sales_access",
            "phone",
            "date_of_joining",
            "is_active",
            "created_at",
        )


class ProfileSelfUpdateSerializer(serializers.Serializer):
    """The two fields a user may change on their OWN profile via PATCH /api/profile/.

    Deliberately just ``phone`` (on Profile) and ``name`` (on User). Role, org
    and the ``has_*_access`` flags are NOT here:
    those decide permissions and are an admin's to set. Leaving them out is what
    keeps this self-edit endpoint from becoming the same self-serve privilege
    escalation ``CreateProfileSerializer`` once was. A field the serializer does
    not name cannot be written, no matter what the request body contains.

    ``phone`` is validated (not written raw as the view used to), so a bad value
    is a clean 400 instead of an unvalidated string in the column. A blank phone
    is allowed and clears it, ``allow_blank`` short-circuits before the regex,
    which requires 7-25 characters.
    """

    name = serializers.CharField(required=False, allow_blank=True, max_length=255)
    phone = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=20,
        validators=[flexible_phone_validator],
    )


class AttachmentsSerializer(serializers.ModelSerializer):
    """Serializer for Attachments model using ContentType"""

    file_path = serializers.SerializerMethodField()
    content_type = serializers.SlugRelatedField(slug_field="model", read_only=True)

    @extend_schema_field(str)
    def get_file_path(self, obj):
        if obj.attachment:
            return obj.attachment.url
        return None

    class Meta:
        model = Attachments
        fields = [
            "id",
            "created_by",
            "file_name",
            "created_at",
            "file_path",
            "content_type",
            "object_id",
            "org",
        ]


class AttachmentsCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating attachments with ContentType"""

    content_type = serializers.CharField(write_only=True)
    object_id = serializers.UUIDField(write_only=True)

    class Meta:
        model = Attachments
        fields = (
            "file_name",
            "attachment",
            "content_type",
            "object_id",
        )

    def create(self, validated_data):
        from django.contrib.contenttypes.models import ContentType

        content_type_str = validated_data.pop("content_type")
        try:
            content_type = ContentType.objects.get(model=content_type_str.lower())
        except ContentType.DoesNotExist as exc:
            raise serializers.ValidationError(
                f"Invalid content type: {content_type_str}"
            ) from exc

        validated_data["content_type"] = content_type
        return super().create(validated_data)


class DocumentSerializer(serializers.ModelSerializer):
    shared_to = ProfileSerializer(read_only=True, many=True)
    teams = serializers.SerializerMethodField()
    created_by = UserSerializer()
    org = OrganizationSerializer()
    size_bytes = serializers.SerializerMethodField()

    @extend_schema_field(list)
    def get_teams(self, obj):
        # `member_count` is what a reader needs to understand a team share;
        # "Support (3)" says how many people the pick actually reaches, which a
        # bare team name does not. Naming the team rather than flattening it to
        # its members keeps the share meaning what was chosen as people join and
        # leave. Only the (unpaginated, small) documents page consumes this.
        return [
            {"id": str(team.id), "name": team.name, "member_count": team.users.count()}
            for team in obj.teams.all()
        ]

    @extend_schema_field(int)
    def get_size_bytes(self, obj):
        # The file's size on disk, so the list can show "284 kB" instead of a
        # bare filename. Guarded because `.size` reaches the storage backend: a
        # row whose file is missing (a stale path, a not-yet-synced media dir)
        # must degrade to an unknown size, never 500 the whole list.
        try:
            return obj.document_file.size
        except (ValueError, OSError):
            return None

    class Meta:
        model = Document
        fields = [
            "id",
            "title",
            "document_file",
            "status",
            "shared_to",
            "teams",
            "size_bytes",
            "created_at",
            "created_by",
            "org",
        ]


class DocumentCreateSerializer(serializers.ModelSerializer):
    def __init__(self, *args, **kwargs):
        request_obj = kwargs.pop("request_obj", None)
        super().__init__(*args, **kwargs)
        self.fields["title"].required = True
        self.org = request_obj.profile.org

    def validate_title(self, title):
        if self.instance:
            if (
                Document.objects.filter(title__iexact=title, org=self.org)
                .exclude(id=self.instance.id)
                .exists()
            ):
                raise serializers.ValidationError(
                    "Document with this Title already exists"
                )
        else:
            if Document.objects.filter(title__iexact=title, org=self.org).exists():
                raise serializers.ValidationError(
                    "Document with this Title already exists"
                )
        return title

    class Meta:
        model = Document
        fields = ["title", "document_file", "status", "org"]
        read_only_fields = ["org"]


def find_urls(string):
    # website_regex = "^((http|https)://)?([A-Za-z0-9.-]+\.[A-Za-z]{2,63})?$"  # (http(s)://)google.com or google.com
    # website_regex = "^https?://([A-Za-z0-9.-]+\.[A-Za-z]{2,63})?$"  # (http(s)://)google.com
    # http(s)://google.com
    website_regex = r"^https?://[A-Za-z0-9.-]+\.[A-Za-z]{2,63}$"
    # http(s)://google.com:8000
    website_regex_port = r"^https?://[A-Za-z0-9.-]+\.[A-Za-z]{2,63}:[0-9]{2,4}$"
    url = re.findall(website_regex, string)
    url_port = re.findall(website_regex_port, string)
    if url and url[0] != "":
        return url
    return url_port


class APISettingsSerializer(serializers.ModelSerializer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    class Meta:
        model = APISettings
        fields = ("title", "website")

    def validate_website(self, website):
        if website and not (
            website.startswith("http://") or website.startswith("https://")
        ):
            raise serializers.ValidationError("Please provide valid schema")
        if not len(find_urls(website)) > 0:
            raise serializers.ValidationError(
                "Please provide a valid URL with schema and without trailing slash - Example: http://google.com"
            )
        return website


class APISettingsListSerializer(serializers.ModelSerializer):
    created_by = UserSerializer()
    lead_assigned_to = ProfileSerializer(read_only=True, many=True)
    tags = serializers.SerializerMethodField()
    org = OrganizationSerializer()

    @extend_schema_field(list)
    def get_tags(self, obj):
        return obj.tags.all().values()

    class Meta:
        model = APISettings
        fields = [
            "id",
            "title",
            "apikey",
            "website",
            "created_at",
            "created_by",
            "lead_assigned_to",
            "tags",
            "org",
        ]

    def to_representation(self, instance):
        """`apikey` is a live credential, so only an admin reads it back.

        It is the sole thing `CreateLeadFromSite` authenticates on (see
        `leads/views/lead_interactions.py`, which matches on the key alone and
        has its website check commented out). Anyone holding it can post Lead
        and Contact rows into the org anonymously, attributed to whoever
        created the setting. Handing that to every USER-role member of the org
        made a shared credential out of an admin one.

        Unlike the `webhook_secret` on `InboundMailboxSerializer`, this cannot
        be `write_only`: the key exists to be pasted into a website form, so
        the admin who manages the integration has to read it back. Admin-only
        on read is the same treatment `topic_arn` gets there.

        Popped when the view passes no request in context, since a serializer
        with no caller cannot prove the caller is an admin. Both call sites in
        `common/views/settings_views.py` pass it.
        """
        data = super().to_representation(instance)
        request = self.context.get("request")
        if not is_org_admin(getattr(request, "profile", None)):
            data.pop("apikey", None)
        return data


class APISettingsSwaggerSerializer(serializers.ModelSerializer):
    class Meta:
        model = APISettings
        fields = [
            "title",
            "website",
            "lead_assigned_to",
            "tags",
        ]


class DocumentCreateSwaggerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Document
        fields = [
            "title",
            "document_file",
            "teams",
            "shared_to",
        ]


class DocumentEditSwaggerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Document
        fields = ["title", "document_file", "teams", "shared_to", "status"]


class UserCreateSwaggerSerializer(serializers.Serializer):
    """
    It is swagger for creating or updating user
    """

    ROLE_CHOICES = ["ADMIN", "USER"]

    email = serializers.CharField(max_length=1000, required=True)
    role = serializers.ChoiceField(choices=ROLE_CHOICES, required=True)
    phone = serializers.CharField(
        max_length=12, required=False, allow_blank=True, allow_null=True
    )
    alternate_phone = serializers.CharField(
        max_length=12, required=False, allow_blank=True, allow_null=True
    )
    address_line = serializers.CharField(
        max_length=10000, required=False, allow_blank=True
    )
    street = serializers.CharField(max_length=1000, required=False, allow_blank=True)
    city = serializers.CharField(max_length=1000, required=False, allow_blank=True)
    state = serializers.CharField(max_length=1000, required=False, allow_blank=True)
    pincode = serializers.CharField(max_length=1000, required=False, allow_blank=True)
    country = serializers.CharField(max_length=1000, required=False, allow_blank=True)


class UserUpdateStatusSwaggerSerializer(serializers.Serializer):
    STATUS_CHOICES = ["Active", "Inactive"]

    status = serializers.ChoiceField(choices=STATUS_CHOICES, required=True)


# JWT Authentication Serializers for SvelteKit Integration


class MagicLinkRequestSerializer(serializers.Serializer):
    """Serializer for requesting a magic link or OTP code."""

    email = serializers.EmailField(required=True)
    delivery = serializers.ChoiceField(
        choices=("link", "code"), required=False, default="link"
    )

    def validate_email(self, value):
        domain = value.rsplit("@", 1)[-1].lower()
        if domain in disposable_domains:
            raise serializers.ValidationError(
                "Disposable email addresses are not allowed."
            )
        return value


class MagicLinkVerifySerializer(serializers.Serializer):
    """Serializer for verifying a magic link token."""

    token = serializers.CharField(required=True, max_length=64)


class MagicLinkVerifyCodeSerializer(serializers.Serializer):
    """Serializer for verifying an OTP code (mobile flow)."""

    email = serializers.EmailField(required=True)
    code = serializers.RegexField(r"^\d{6}$", required=True, max_length=6)


class UserDetailSerializer(serializers.ModelSerializer):
    """Detailed user serializer with profile and organizations"""

    organizations = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "email", "profile_pic", "is_active", "organizations"]

    @extend_schema_field(list)
    def get_organizations(self, obj):
        """Get all organizations the user belongs to"""
        profiles = Profile.objects.filter(user=obj, is_active=True)
        return [
            {
                "id": str(profile.org.id),
                "name": profile.org.name,
                "role": profile.role,
                "is_organization_admin": profile.is_organization_admin,
                "has_sales_access": profile.has_sales_access,
                "has_marketing_access": profile.has_marketing_access,
            }
            for profile in profiles
        ]


class ProfileDetailSerializer(serializers.ModelSerializer):
    """The caller's OWN profile, in whichever org their token names.

    `teams` and `last_login` are here because the mobile profile screen reads
    this endpoint while the web one reads `ProfileView`, which had both, so the
    two screens described the same person differently. Widening this serializer
    rather than pointing mobile at the other endpoint keeps each client on the
    endpoint it already uses.

    Safe to widen because this is only ever the requester's own row:
    `ProfileDetailView` serializes `request.profile` and takes no id. Neither
    field is on the shared `ProfileSerializer`, which is nested into lists of
    OTHER people's profiles, and adding them there would publish when every
    colleague last signed in.
    """

    user = UserSerializer(read_only=True)
    org = OrganizationSerializer(read_only=True)
    teams = serializers.SerializerMethodField()
    last_login = serializers.DateTimeField(source="user.last_login", read_only=True)

    @extend_schema_field(serializers.ListField(child=serializers.CharField()))
    def get_teams(self, obj):
        return list(obj.user_teams.values_list("name", flat=True))

    class Meta:
        model = Profile
        fields = [
            "id",
            "user",
            "org",
            "role",
            "is_organization_admin",
            "has_sales_access",
            "has_marketing_access",
            "phone",
            "date_of_joining",
            "is_active",
            "teams",
            "last_login",
        ]


# Activity Serializers for Dashboard Recent Activities


class ActivityUserSerializer(serializers.Serializer):
    """Simplified user info for activity display"""

    id = serializers.UUIDField(source="user.id")
    email = serializers.EmailField(source="user.email")
    name = serializers.SerializerMethodField()
    profile_pic = serializers.CharField(source="user.profile_pic", allow_null=True)

    @extend_schema_field(str)
    def get_name(self, obj):
        """Get display name from email"""
        return obj.user.email.split("@")[0]


class DashboardActivitySerializer(serializers.ModelSerializer):
    """Recent-activity row for the dashboard feed.

    Renamed 2026-08-05. It was a second class also called
    `ActivitySerializer`, 580 lines below the first, so it silently shadowed
    it and **both** consumers got this shape. `cases/views.py` imports the
    other one for the ticket activity timeline, whose frontend component reads
    `metadata` (five places) and `created_at`; neither is in this list, so the
    timeline rendered no metadata and an invalid date. The dashboard reads
    `timestamp` and `humanized_time`, which is why only one of the two broke
    visibly.
    """

    user = ActivityUserSerializer(read_only=True)
    action_display = serializers.CharField(source="get_action_display", read_only=True)
    timestamp = serializers.DateTimeField(source="created_at", read_only=True)
    humanized_time = serializers.CharField(source="created_on_arrow", read_only=True)

    class Meta:
        model = Activity
        fields = [
            "id",
            "user",
            "action",
            "action_display",
            "entity_type",
            "entity_id",
            "entity_name",
            "description",
            "timestamp",
            "humanized_time",
        ]


class TeamsSerializer(serializers.ModelSerializer):
    users = ProfileSerializer(read_only=True, many=True)
    created_by = UserSerializer()

    class Meta:
        model = Teams
        fields = (
            "id",
            "name",
            "description",
            "users",
            "created_at",
            "created_by",
        )


class TeamCreateSerializer(serializers.ModelSerializer):
    def __init__(self, *args, **kwargs):
        request_obj = kwargs.pop("request_obj", None)
        super().__init__(*args, **kwargs)
        self.org = request_obj.profile.org

        self.fields["name"].required = True
        self.fields["description"].required = False

    def validate_name(self, name):
        if self.instance:
            if (
                Teams.objects.filter(name__iexact=name, org=self.org)
                .exclude(id=self.instance.id)
                .exists()
            ):
                raise serializers.ValidationError("Team already exists with this name")
        else:
            if Teams.objects.filter(name__iexact=name, org=self.org).exists():
                raise serializers.ValidationError("Team already exists with this name")
        return name

    class Meta:
        model = Teams
        fields = (
            "name",
            "description",
            "created_at",
            "created_by",
            "org",
        )
        read_only_fields = ("created_at", "created_by", "org")


class TeamswaggerCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Teams
        fields = (
            "name",
            "description",
            "users",
        )


class PersonalAccessTokenListSerializer(serializers.ModelSerializer):
    class Meta:
        model = PersonalAccessToken
        fields = (
            "id",
            "name",
            "token_prefix",
            "scopes",
            "expires_at",
            "last_used_at",
            "created_at",
            "revoked_at",
        )
        read_only_fields = fields


class PersonalAccessTokenCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = PersonalAccessToken
        fields = ("name", "scopes", "expires_at")

    def validate_name(self, value):
        value = (value or "").strip()
        if not value:
            raise serializers.ValidationError("Name is required.")
        if len(value) > 255:
            raise serializers.ValidationError("Name too long (max 255).")
        return value

    def validate_scopes(self, value):
        """Reject scopes that mean nothing, and return them canonicalised.

        This used to accept any list of up to 32 arbitrary strings, from a time
        when nothing read the field. Now that `common.scopes` enforces it, an
        unvalidated scope is worse than none: a caller who misspells a resource
        believes they restricted the token, and gets one that matches nothing at
        all. Say so at creation instead.

        An empty list stays valid and still means unrestricted. See the
        `common.scopes` module docstring for why.
        """
        from common.scopes import normalize_scope

        if value in (None, ""):
            return []
        if not isinstance(value, list) or not all(isinstance(s, str) for s in value):
            raise serializers.ValidationError("scopes must be a list of strings.")
        if len(value) > 32:
            raise serializers.ValidationError("Too many scopes (max 32).")

        seen = []
        for raw in value:
            try:
                scope = normalize_scope(raw)
            except ValueError as exc:
                raise serializers.ValidationError(str(exc)) from exc
            if scope not in seen:
                seen.append(scope)
        return seen

    def validate_expires_at(self, value):
        from django.utils import timezone

        if value is not None and value <= timezone.now():
            raise serializers.ValidationError("expires_at must be in the future.")
        return value
