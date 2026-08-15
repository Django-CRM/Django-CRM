import binascii
import hashlib
import os
import secrets
import time
import uuid

from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils import timezone
from django.utils.text import slugify
from django.utils.timesince import timesince
from django.utils.translation import gettext_lazy as _

from common.base import BaseModel, BaseOrgModel
from common.utils import (
    COUNTRIES,
    CURRENCY_CODES,
    ROLES,
    is_document_file_audio,
    is_document_file_code,
    is_document_file_image,
    is_document_file_pdf,
    is_document_file_sheet,
    is_document_file_text,
    is_document_file_video,
    is_document_file_zip,
)
from common.validators import validate_iana_timezone

from .manager import UserManager


class User(AbstractBaseUser, PermissionsMixin):
    id = models.UUIDField(
        default=uuid.uuid4, unique=True, editable=False, db_index=True, primary_key=True
    )
    email = models.EmailField(_("email address"), blank=True, unique=True)
    name = models.CharField(_("name"), max_length=255, blank=True, default="")
    profile_pic = models.CharField(max_length=1000, null=True, blank=True)
    activation_key = models.CharField(max_length=150, null=True, blank=True)
    key_expires = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(_("staff status"), default=False)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UserManager()

    class Meta:
        verbose_name = "User"
        verbose_name_plural = "Users"
        db_table = "users"
        ordering = ("-is_active",)

    def save(self, *args, **kwargs):
        # On first save only, fall back to the email local-part when no name
        # was supplied. Keeps `name` non-empty without overwriting later
        # edits (PATCHing name to "" leaves it empty by user intent).
        if self._state.adding and not self.name and self.email:
            self.name = self.email.split("@", 1)[0][:255]
        super().save(*args, **kwargs)

    def __str__(self):
        return self.email


class Address(BaseModel):
    address_line = models.CharField(
        _("Address"), max_length=255, blank=True, default=""
    )
    street = models.CharField(_("Street"), max_length=55, blank=True, default="")
    city = models.CharField(_("City"), max_length=255, blank=True, default="")
    state = models.CharField(_("State"), max_length=255, blank=True, default="")
    postcode = models.CharField(
        _("Post/Zip-code"), max_length=64, blank=True, default=""
    )
    country = models.CharField(max_length=3, choices=COUNTRIES, blank=True, default="")
    org = models.ForeignKey(
        "Org",
        on_delete=models.CASCADE,
        related_name="addresses",
    )

    class Meta:
        verbose_name = "Address"
        verbose_name_plural = "Addresses"
        db_table = "address"
        ordering = ("-created_at",)

    def __str__(self):
        return self.city if self.city else ""


def generate_unique_key():
    return str(uuid.uuid4())


class Org(BaseModel):
    name = models.CharField(max_length=100, blank=True, null=True)
    api_key = models.TextField(default=generate_unique_key, unique=True, editable=False)
    is_active = models.BooleanField(default=True)

    # Company Profile (for invoices, documents, etc.)
    company_name = models.CharField(
        max_length=255, blank=True, help_text="Legal company name for invoices"
    )
    logo = models.ImageField(
        upload_to="org_logos/", blank=True, null=True, help_text="Company logo"
    )
    address_line = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=100, blank=True)
    postcode = models.CharField(max_length=20, blank=True)
    country = models.CharField(max_length=3, choices=COUNTRIES, blank=True)
    phone = models.CharField(max_length=25, blank=True)
    email = models.EmailField(blank=True)
    website = models.URLField(blank=True)
    tax_id = models.CharField(
        max_length=50, blank=True, help_text="Tax ID / VAT / Registration number"
    )

    # Locale settings
    default_currency = models.CharField(
        max_length=3, choices=CURRENCY_CODES, default="USD"
    )
    default_country = models.CharField(
        max_length=2, choices=COUNTRIES, blank=True, null=True
    )
    # The org's calendar day. Every "today", "overdue" and "this month" in the
    # app is resolved against this, because a day boundary is a fact about the
    # people using the org, not about the server. `RequireOrgContext` activates
    # it per request so `timezone.localdate()` answers in it; Celery has no
    # middleware and must call `common.tasks.set_org_timezone` itself.
    #
    # UTC by default so a client that never sends one, an older mobile build in
    # particular, still creates a usable org. Stored as an IANA name rather than
    # an offset, since only a name knows when the clocks move.
    timezone = models.CharField(
        max_length=64,
        default="UTC",
        validators=[validate_iana_timezone],
        help_text="IANA timezone (e.g. America/New_York). Sets the org's day.",
    )

    # CSAT (Tier 2 csat). Org-level kill switch, when False, the
    # post-close signal short-circuits before any survey email is sent.
    csat_enabled = models.BooleanField(default=True)

    # Tier 3 parent/child: when True, closing a parent case offers to
    # cascade-close any open descendants. The endpoint still requires an
    # explicit confirmation; this flag only controls the default state.
    auto_close_children_on_parent_close = models.BooleanField(default=False)

    # Vertical packs. Descriptive only: records which pack was applied at
    # signup. NEVER used in a permission check or a queryset filter.
    vertical = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="Pack id applied to this org, e.g. 'real-estate'. Descriptive only.",
    )
    terminology = models.JSONField(
        default=dict,
        blank=True,
        help_text="Label overrides, e.g. {'lead.plural': 'Enquiries'}.",
    )

    class Meta:
        verbose_name = "Organization"
        verbose_name_plural = "Organizations"
        db_table = "organization"
        ordering = ("-created_at",)

    def __str__(self):
        return str(self.name)


class Tags(BaseModel):
    """Tags for categorizing CRM entities (Accounts, Leads, Opportunities, etc.)"""

    COLOR_CHOICES = (
        ("gray", "Gray"),
        ("red", "Red"),
        ("orange", "Orange"),
        ("amber", "Amber"),
        ("yellow", "Yellow"),
        ("lime", "Lime"),
        ("green", "Green"),
        ("emerald", "Emerald"),
        ("teal", "Teal"),
        ("cyan", "Cyan"),
        ("sky", "Sky"),
        ("blue", "Blue"),
        ("indigo", "Indigo"),
        ("violet", "Violet"),
        ("purple", "Purple"),
        ("fuchsia", "Fuchsia"),
        ("pink", "Pink"),
        ("rose", "Rose"),
    )

    name = models.CharField(max_length=50)
    slug = models.CharField(max_length=50, blank=True)
    color = models.CharField(max_length=20, choices=COLOR_CHOICES, default="blue")
    description = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True)
    org = models.ForeignKey(
        "Org",
        on_delete=models.CASCADE,
        related_name="tags",
    )

    class Meta:
        verbose_name = "Tag"
        verbose_name_plural = "Tags"
        db_table = "tags"
        ordering = ("name",)
        unique_together = ["slug", "org"]

    def __str__(self):
        return f"{self.name}"

    def save(self, *args, **kwargs):
        self.slug = slugify(self.name)
        super().save(*args, **kwargs)


class Profile(BaseModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="profiles")
    org = models.ForeignKey(Org, on_delete=models.CASCADE, related_name="profiles")
    phone = models.CharField(max_length=20, null=True, blank=True)
    alternate_phone = models.CharField(max_length=20, null=True, blank=True)
    address = models.ForeignKey(
        Address,
        related_name="address_users",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
    )
    role = models.CharField(max_length=50, choices=ROLES, default="USER")
    has_sales_access = models.BooleanField(default=False)
    has_marketing_access = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    is_organization_admin = models.BooleanField(default=False)
    date_of_joining = models.DateField(null=True, blank=True)

    class Meta:
        verbose_name = "Profile"
        verbose_name_plural = "Profiles"
        db_table = "profile"
        ordering = ("-created_at",)
        unique_together = [["user", "org"], ["phone", "org"]]

    def __str__(self):
        return f"{self.user.email} <{self.org.name}>"

    def save(self, *args, **kwargs):
        # `role` and `is_organization_admin` are two columns for one binary
        # fact (`ROLES` is ADMIN/USER and nothing else), and they used to be
        # settable independently, which let an admin mint a colleague the UI
        # showed as a plain user and could never demote. `role` is what every
        # surface displays and what `common.permissions.is_org_admin` reads;
        # the column is kept because it is in API responses, so it is derived
        # here rather than left to drift.
        self.is_organization_admin = self.role == "ADMIN"
        super().save(*args, **kwargs)

    @property
    def is_admin(self):
        """Alias kept for the API response and for existing callers.

        Reads `role`, not the column, so it answers the same as
        `is_org_admin` even on an unsaved instance.
        """
        return self.role == "ADMIN"

    @property
    def user_details(self):
        return {
            "email": self.user.email,
            "id": self.user.id,
            "name": self.user.name,
            "is_active": self.user.is_active,
            "profile_pic": self.user.profile_pic,
            "last_login": self.user.last_login,
        }


# The body was a CharField(max_length=255) until the customer portal needed
# customers to write real support replies, which 255 characters cannot hold.
# Widening it to TextField removed the only bound on the input, so this puts an
# intentional one back at the API layer.
#
# Read this before trusting it: Django does NOT enforce `max_length` on a
# TextField at the model or database level, only in a form widget. `full_clean`
# will not raise, so a direct ORM write of any size still succeeds. What makes
# this a real bound is DRF, which maps a model TextField's `max_length` into a
# serializer validator, so every request-driven write path is checked: the agent
# UI, the mobile client and the portal alike. Server-side code that writes
# comments directly is trusted and deliberately unbounded.
#
# The column stays an unbounded TEXT, so raising this later needs no data
# migration. Enforcement is pinned by
# `contacts/tests/test_contact_access_and_links.py::test_a_comment_too_long_to_store_is_reported`.
COMMENT_MAX_LENGTH = 10_000


class Comment(BaseModel):
    """
    Generic comment model using ContentType framework.
    Can be attached to any model (Account, Lead, Contact, Opportunity, Case, Task, Invoice, Profile).
    """

    # Generic relation to any model
    content_type = models.ForeignKey(
        ContentType, on_delete=models.CASCADE, related_name="comments"
    )
    object_id = models.UUIDField()
    content_object = GenericForeignKey("content_type", "object_id")

    comment = models.TextField(max_length=COMMENT_MAX_LENGTH)
    commented_on = models.DateTimeField(auto_now_add=True)
    commented_by = models.ForeignKey(
        Profile, on_delete=models.CASCADE, blank=True, null=True
    )
    # A reply written through the customer portal has no Profile behind it, so
    # `commented_by` stays null and this carries the author instead. The check
    # constraint below is what stops a row claiming to be written by both a
    # colleague and a customer.
    commented_by_contact = models.ForeignKey(
        "contacts.Contact",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="portal_comments",
    )
    is_internal = models.BooleanField(default=False, db_index=True)
    org = models.ForeignKey(
        "Org",
        on_delete=models.CASCADE,
        related_name="comments",
    )

    class Meta:
        verbose_name = "Comment"
        verbose_name_plural = "Comments"
        db_table = "comment"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["content_type", "object_id"]),
            models.Index(fields=["org", "-created_at"]),
            models.Index(fields=["content_type", "object_id", "is_internal"]),
        ]
        constraints = [
            # An author is either a colleague or a customer, never both. `save`
            # calls `full_clean`, which validates constraints, so this is
            # enforced on ordinary writes and not only at the database.
            models.CheckConstraint(
                condition=models.Q(commented_by__isnull=True)
                | models.Q(commented_by_contact__isnull=True),
                name="comment_single_author",
            ),
        ]

    def __str__(self):
        return f"{self.comment}"

    def clean(self):
        """
        Validate that the comment's org matches the content object's org.

        SECURITY: This prevents cross-org data references where a comment
        in org_a could reference an object in org_b.
        """
        from django.core.exceptions import ValidationError

        if self.content_object and hasattr(self.content_object, "org"):
            if self.content_object.org_id != self.org_id:
                raise ValidationError(
                    {
                        "org": "Comment organization must match the referenced object's organization."
                    }
                )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class CommentFiles(BaseModel):
    """
    File attachments for comments.
    Security: org field added for RLS protection and org-level isolation.
    """

    comment = models.ForeignKey(Comment, on_delete=models.CASCADE)
    comment_file = models.FileField(
        "File", upload_to="CommentFiles", null=True, blank=True
    )
    # Security fix: Add org field for RLS protection
    org = models.ForeignKey(
        "Org",
        on_delete=models.CASCADE,
        related_name="comment_files",
        null=True,  # Temporarily nullable for migration
        blank=True,
    )

    class Meta:
        verbose_name = "CommentFile"
        verbose_name_plural = "CommentFiles"
        db_table = "commentFiles"
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.comment.comment}"

    def save(self, *args, **kwargs):
        # Auto-populate org from parent comment if not set
        if not self.org_id and self.comment_id:
            self.org_id = self.comment.org_id
        super().save(*args, **kwargs)


class Attachments(BaseModel):
    """
    Generic attachment model using ContentType framework.
    Can be attached to any model (Account, Lead, Contact, Opportunity, Case, Task, Invoice).
    """

    # Generic relation to any model
    content_type = models.ForeignKey(
        ContentType, on_delete=models.CASCADE, related_name="attachments"
    )
    object_id = models.UUIDField()
    content_object = GenericForeignKey("content_type", "object_id")

    file_name = models.CharField(max_length=60)
    attachment = models.FileField(max_length=1001, upload_to="attachments/%Y/%m/")
    org = models.ForeignKey(
        "Org",
        on_delete=models.CASCADE,
        related_name="attachments",
    )

    class Meta:
        verbose_name = "Attachment"
        verbose_name_plural = "Attachments"
        db_table = "attachments"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["content_type", "object_id"]),
            models.Index(fields=["org", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.file_name}"

    def file_type(self):
        name_ext_list = self.attachment.url.split(".")
        if len(name_ext_list) > 1:
            ext = name_ext_list[int(len(name_ext_list) - 1)]
            if is_document_file_audio(ext):
                return ("audio", "fa fa-file-audio")
            if is_document_file_video(ext):
                return ("video", "fa fa-file-video")
            if is_document_file_image(ext):
                return ("image", "fa fa-file-image")
            if is_document_file_pdf(ext):
                return ("pdf", "fa fa-file-pdf")
            if is_document_file_code(ext):
                return ("code", "fa fa-file-code")
            if is_document_file_text(ext):
                return ("text", "fa fa-file-alt")
            if is_document_file_sheet(ext):
                return ("sheet", "fa fa-file-excel")
            if is_document_file_zip(ext):
                return ("zip", "fa fa-file-archive")
            return ("file", "fa fa-file")
        return ("file", "fa fa-file")

    def clean(self):
        """
        Validate that the attachment's org matches the content object's org.

        SECURITY: This prevents cross-org data references where an attachment
        in org_a could reference an object in org_b.
        """
        from django.core.exceptions import ValidationError

        if self.content_object and hasattr(self.content_object, "org"):
            if self.content_object.org_id != self.org_id:
                raise ValidationError(
                    {
                        "org": "Attachment organization must match the referenced object's organization."
                    }
                )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


def document_path(self, filename):
    hash_ = int(time.time())
    return f"docs/{hash_}/{filename}"


class Document(BaseModel):
    DOCUMENT_STATUS_CHOICE = (("active", "active"), ("inactive", "inactive"))

    title = models.TextField(blank=True, null=True)
    document_file = models.FileField(upload_to=document_path, max_length=5000)
    status = models.CharField(
        choices=DOCUMENT_STATUS_CHOICE, max_length=64, default="active"
    )
    shared_to = models.ManyToManyField(Profile, related_name="document_shared_to")
    teams = models.ManyToManyField("Teams", related_name="document_teams")
    org = models.ForeignKey(
        Org,
        on_delete=models.CASCADE,
        related_name="documents",
    )

    class Meta:
        verbose_name = "Document"
        verbose_name_plural = "Documents"
        db_table = "document"
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.title}"

    def file_type(self):
        name_ext_list = self.document_file.url.split(".")
        if len(name_ext_list) > 1:
            ext = name_ext_list[int(len(name_ext_list) - 1)]
            if is_document_file_audio(ext):
                return ("audio", "fa fa-file-audio")
            if is_document_file_video(ext):
                return ("video", "fa fa-file-video")
            if is_document_file_image(ext):
                return ("image", "fa fa-file-image")
            if is_document_file_pdf(ext):
                return ("pdf", "fa fa-file-pdf")
            if is_document_file_code(ext):
                return ("code", "fa fa-file-code")
            if is_document_file_text(ext):
                return ("text", "fa fa-file-alt")
            if is_document_file_sheet(ext):
                return ("sheet", "fa fa-file-excel")
            if is_document_file_zip(ext):
                return ("zip", "fa fa-file-archive")
            return ("file", "fa fa-file")
        return ("file", "fa fa-file")


def generate_key():
    # Security: Increased from 8 bytes (64 bits) to 32 bytes (256 bits)
    return binascii.hexlify(os.urandom(32)).decode()


class APISettings(BaseModel):
    title = models.TextField()
    # Security: Increased max_length to accommodate 32-byte keys (64 hex chars)
    apikey = models.CharField(max_length=64, blank=True)
    website = models.URLField(max_length=255, null=True)
    lead_assigned_to = models.ManyToManyField(
        Profile, related_name="lead_assignee_users"
    )
    tags = models.ManyToManyField(Tags, blank=True)
    org = models.ForeignKey(
        Org,
        on_delete=models.CASCADE,
        related_name="api_settings",
    )

    class Meta:
        verbose_name = "APISetting"
        verbose_name_plural = "APISettings"
        db_table = "apiSettings"
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.title}"

    def save(self, *args, **kwargs):
        if not self.apikey or self.apikey is None or self.apikey == "":
            self.apikey = generate_key()
        super().save(*args, **kwargs)


class MagicLinkToken(models.Model):
    """One-time magic link tokens for passwordless authentication.

    Supports two delivery modes (`delivery`):
      - "link": classic email-link flow, the user clicks a URL containing `token`.
      - "code": short 6-digit OTP flow for mobile clients. `code_hash` stores the
        hashed code; `attempts` tracks failed verify attempts so we can lock the
        row after too many guesses (codes are short, brute-force matters).
    """

    DELIVERY_LINK = "link"
    DELIVERY_CODE = "code"
    DELIVERY_CHOICES = (
        (DELIVERY_LINK, "Link"),
        (DELIVERY_CODE, "Code"),
    )

    id = models.UUIDField(default=uuid.uuid4, primary_key=True)
    email = models.EmailField(db_index=True)
    token = models.CharField(max_length=64, unique=True, db_index=True)
    delivery = models.CharField(
        max_length=8, choices=DELIVERY_CHOICES, default=DELIVERY_LINK
    )
    code_hash = models.CharField(max_length=256, blank=True, default="")
    attempts = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    is_used = models.BooleanField(default=False)
    used_at = models.DateTimeField(null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        db_table = "magic_link_token"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["email", "created_at"]),
        ]

    def __str__(self):
        return f"MagicLink({self.email}, delivery={self.delivery}, used={self.is_used})"


class PortalLoginToken(models.Model):
    """One-time sign-in codes for the customer portal.

    Mirrors `MagicLinkToken`, deliberately as a separate table rather than a
    nullable `org` column on that one. Sharing the table would mean one verify
    endpoint accepting the other realm's token, and that is a customer logging
    into the internal application. Roughly forty duplicated lines buys a realm
    boundary that a single mistake cannot cross.

    Six emailed digits, stored hashed, and never a clickable link. The portal
    briefly carried both deliveries; nothing ever asked for the link one, so it
    is gone. What remains is the flow the sign-in page actually drives, where
    the customer stays in the tab they started in. The code is the entire
    credential for ten minutes, which is why `attempts` exists: see
    `PortalLoginVerifyView._redeem_code`.

    Bound to one contact in one org when it is minted, so it cannot be replayed
    against another tenant.

    Deliberately absent from `ORG_SCOPED_TABLES`: the lookup runs before any org
    context exists, so an RLS policy here would hide the row from the only
    request that needs to read it. Same reasoning as `MagicLinkToken` and
    `PortalAccessToken`.
    """

    id = models.UUIDField(default=uuid.uuid4, primary_key=True)
    org = models.ForeignKey("Org", on_delete=models.CASCADE)
    contact = models.ForeignKey("contacts.Contact", on_delete=models.CASCADE)
    code_hash = models.CharField(max_length=256)
    attempts = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    is_used = models.BooleanField(default=False)
    used_at = models.DateTimeField(null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        db_table = "portal_login_token"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["contact", "created_at"]),
        ]

    def __str__(self):
        return f"PortalLogin({self.contact_id}, used={self.is_used})"


# Activity Tracking for Recent Activities Dashboard


class Activity(BaseModel):
    """Track user activities across all CRM entities"""

    ACTION_CHOICES = (
        ("CREATE", "Created"),
        ("UPDATE", "Updated"),
        ("DELETE", "Deleted"),
        ("VIEW", "Viewed"),
        ("COMMENT", "Commented"),
        ("ASSIGN", "Assigned"),
        # Cases roadmap verb registry: see docs/cases/COORDINATION_DECISIONS.md D1.
        ("STATUS_CHANGED", "Status Changed"),
        ("PRIORITY_CHANGED", "Priority Changed"),
        ("ROUTED", "Routed"),
        ("ESCALATED", "Escalated"),
        ("REOPENED", "Reopened"),
        ("MERGED", "Merged"),
        ("MERGE_TARGET", "Merge Target"),
        ("UNMERGED", "Unmerged"),
        ("UNMERGE_TARGET", "Unmerge Target"),
        ("LINKED_SOLUTION", "Linked Solution"),
        ("UNLINKED_SOLUTION", "Unlinked Solution"),
        ("WATCHED", "Watched"),
        ("UNWATCHED", "Unwatched"),
        ("MENTIONED", "Mentioned"),
        ("APPROVAL_REQUESTED", "Approval Requested"),
        ("APPROVED", "Approved"),
        ("REJECTED", "Rejected"),
        ("APPROVAL_CANCELLED", "Approval Cancelled"),
        ("LINKED_ASSET", "Linked Asset"),
        ("UNLINKED_ASSET", "Unlinked Asset"),
        ("LINKED_JIRA", "Linked Jira"),
        ("LINKED_PARENT", "Linked Parent"),
        ("UNLINKED_PARENT", "Unlinked Parent"),
        ("PARENT_CLOSED_CASCADE", "Parent Closed Cascade"),
        ("TIME_LOGGED", "Time Logged"),
    )

    ENTITY_TYPE_CHOICES = (
        ("Account", "Account"),
        ("Lead", "Lead"),
        ("Contact", "Contact"),
        ("Opportunity", "Opportunity"),
        ("Case", "Case"),
        ("Task", "Task"),
        ("Invoice", "Invoice"),
        ("Event", "Event"),
        ("Document", "Document"),
        ("Team", "Team"),
    )

    user = models.ForeignKey(
        Profile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="activities",
    )
    action = models.CharField(max_length=32, choices=ACTION_CHOICES)
    entity_type = models.CharField(max_length=50, choices=ENTITY_TYPE_CHOICES)
    entity_id = models.UUIDField()
    entity_name = models.CharField(max_length=255, blank=True, default="")
    description = models.TextField(blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    org = models.ForeignKey(Org, on_delete=models.CASCADE, related_name="activities")

    class Meta:
        verbose_name = "Activity"
        verbose_name_plural = "Activities"
        db_table = "activity"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["org", "-created_at"]),
            models.Index(fields=["entity_type", "entity_id"]),
            # Analytics window scans: "all Case CREATE/UPDATE rows in window".
            models.Index(
                fields=["org", "entity_type", "action", "created_at"],
                name="activity_analytics_idx",
            ),
        ]

    def __str__(self):
        return f"{self.user} {self.get_action_display()} {self.entity_type}: {self.entity_name}"

    @property
    def created_on_arrow(self):
        return timesince(self.created_at) + " ago"


class Notification(BaseModel):
    """In-app notification delivered to a single recipient.

    Per `docs/cases/COORDINATION_DECISIONS.md` D2 we inherit BaseModel and
    declare our own `org` FK rather than using BaseOrgModel. RLS still
    applies via the migration in 0018.
    """

    recipient = models.ForeignKey(
        Profile,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    verb = models.CharField(max_length=64)
    actor = models.ForeignKey(
        Profile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="dispatched_notifications",
    )
    entity_type = models.CharField(max_length=50, blank=True, default="")
    entity_id = models.UUIDField(null=True, blank=True)
    entity_name = models.CharField(max_length=255, blank=True, default="")
    data = models.JSONField(default=dict, blank=True)
    link = models.CharField(max_length=500, blank=True, default="")
    read_at = models.DateTimeField(null=True, blank=True)
    org = models.ForeignKey(Org, on_delete=models.CASCADE, related_name="notifications")

    class Meta:
        verbose_name = "Notification"
        verbose_name_plural = "Notifications"
        db_table = "notification"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["org", "recipient", "-created_at"]),
            models.Index(fields=["recipient", "read_at"]),
        ]

    def __str__(self):
        return f"{self.verb} -> {self.recipient_id}"


class Teams(BaseModel):
    name = models.CharField(max_length=100)
    description = models.TextField()
    users = models.ManyToManyField(Profile, related_name="user_teams")
    org = models.ForeignKey(Org, on_delete=models.CASCADE, related_name="teams")

    class Meta:
        verbose_name = "Team"
        verbose_name_plural = "Teams"
        db_table = "teams"
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.name}"

    def get_users(self):
        return ",".join(
            [str(_id) for _id in list(self.users.values_list("id", flat=True))]
        )


class ContactFormSubmission(BaseModel):
    """
    Contact form submission from public website.
    Stores inquiries from potential customers via contact forms.
    Not org-scoped as these are platform-level submissions.
    """

    name = models.CharField(max_length=255)
    email = models.EmailField()
    message = models.TextField()
    reason = models.CharField(
        max_length=100,
        choices=[
            ("general", "General Inquiry"),
            ("sales", "Sales Question"),
            ("support", "Technical Support"),
            ("partnership", "Partnership Opportunity"),
            ("other", "Other"),
        ],
        default="general",
    )

    # Tracking fields
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(null=True, blank=True)
    referrer = models.URLField(max_length=500, null=True, blank=True)

    # Status tracking
    STATUS_CHOICES = [
        ("new", "New"),
        ("read", "Read"),
        ("replied", "Replied"),
        ("closed", "Closed"),
    ]
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="new")
    replied_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="contact_replies",
    )
    replied_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Contact Form Submission"
        verbose_name_plural = "Contact Form Submissions"
        db_table = "contact_form_submission"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["email"]),
            models.Index(fields=["status"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return f"{self.name} - {self.email} ({self.reason})"


class CustomFieldDefinition(BaseModel):
    """Per-org schema extension for any supported entity (Case, Lead, ...).

    Each row defines a single field on a single target model. Values are stored
    on the entity itself in a `custom_fields` JSONField; this row is the
    metadata that the validator uses to coerce types, enforce required fields,
    and validate dropdown membership.

    See docs/cases/tier1/custom-fields.md.
    """

    TARGET_MODEL_CHOICES = [
        ("Account", "Account"),
        ("Case", "Case"),
        ("Contact", "Contact"),
        ("Estimate", "Estimate"),
        ("Invoice", "Invoice"),
        ("Lead", "Lead"),
        ("Opportunity", "Opportunity"),
        ("RecurringInvoice", "Recurring Invoice"),
        ("Task", "Task"),
    ]

    FIELD_TYPE_CHOICES = [
        ("text", "Text"),
        ("textarea", "Textarea"),
        ("number", "Number"),
        ("dropdown", "Dropdown"),
        ("date", "Date"),
        ("checkbox", "Checkbox"),
    ]

    org = models.ForeignKey(
        Org, on_delete=models.CASCADE, related_name="custom_field_definitions"
    )
    target_model = models.CharField(max_length=32, choices=TARGET_MODEL_CHOICES)
    key = models.SlugField(max_length=64)
    label = models.CharField(max_length=128)
    field_type = models.CharField(max_length=16, choices=FIELD_TYPE_CHOICES)
    options = models.JSONField(
        blank=True,
        null=True,
        help_text="List of {value, label} pairs for dropdown fields.",
    )
    is_required = models.BooleanField(default=False)
    is_filterable = models.BooleanField(default=False)
    display_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Custom Field Definition"
        verbose_name_plural = "Custom Field Definitions"
        db_table = "custom_field_definition"
        ordering = ("target_model", "display_order", "label")
        constraints = [
            models.UniqueConstraint(
                fields=["org", "target_model", "key"],
                name="uniq_custom_field_per_org_target",
            )
        ]
        indexes = [
            models.Index(fields=["org", "target_model", "is_active"]),
        ]

    def __str__(self):
        return f"{self.target_model}.{self.key} ({self.label})"


def generate_pat_raw():
    """Return a new raw personal access token string."""
    return f"bcrm_pat_{secrets.token_urlsafe(32)}"


class PersonalAccessToken(BaseOrgModel):
    """
    Per-user token for programmatic REST API access (scripts, agents).

    The agent authenticates AS `profile` and inherits that user's role,
    org and RLS scope. The raw token is shown ONCE at creation and only
    its SHA-256 hash is stored.
    """

    profile = models.ForeignKey(
        "common.Profile",
        on_delete=models.CASCADE,
        related_name="access_tokens",
    )
    name = models.CharField(max_length=255)
    token_hash = models.CharField(max_length=64, unique=True, db_index=True)
    token_prefix = models.CharField(max_length=20)
    # `<resource>:<action>` strings, validated at creation and enforced on every
    # request by `common.scopes` from the `GetProfileAndOrg` middleware. An empty
    # list means unrestricted, which is what every token issued before
    # enforcement carries and what this field used to mean unconditionally.
    # See common/scopes.py for the grammar and for the credential deny-list that
    # applies regardless of what is stored here.
    scopes = models.JSONField(default=list, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "personal_access_token"
        indexes = [models.Index(fields=["org", "-created_at"])]

    @staticmethod
    def hash_token(raw):
        return hashlib.sha256(raw.encode()).hexdigest()

    @classmethod
    def generate(cls, profile, name, scopes=None, expires_at=None):
        raw = generate_pat_raw()
        pat = cls.objects.create(
            org=profile.org,
            profile=profile,
            name=name,
            token_hash=cls.hash_token(raw),
            token_prefix=raw[:13],
            scopes=scopes or [],
            expires_at=expires_at,
            created_by=profile.user,
        )
        return raw, pat

    def is_valid(self):
        if self.revoked_at is not None:
            return False
        if self.expires_at is not None and self.expires_at <= timezone.now():
            return False
        return True


class PortalAccessToken(models.Model):
    """Unscoped ``token_hash → org`` lookup for anonymous portal requests.

    The public portal endpoints: the invoice and estimate links emailed to
    customers, and the CSAT survey link, arrive with no auth and no org
    context. RLS is keyed on ``app.current_org``, so with an empty context the
    isolation policy hides the very row (invoice / estimate / csat_survey) that
    would tell us which org the token belongs to. That is a chicken-and-egg:
    you cannot set the context until you know the org, and you cannot read the
    org until the context is set.

    This table breaks it. It is deliberately **not** org-scoped. It is absent
    from ``common.rls.ORG_SCOPED_TABLES`` and carries no RLS policy, so it is
    readable with an empty context. A public view hashes the URL token, looks
    up the org here, calls ``set_rls_context(org_id)``, and only then reads the
    resource under full RLS. That is the same sanctioned pattern Celery tasks
    and the CSAT view already use; it adds a surface rather than weakening one.

    The key is ``sha256(url_token)``. For invoices and estimates the URL token
    is the raw ``public_token``; for CSAT it is the signed token whose SHA-256
    is already the stored ``csat_survey.token_hash``, so a single hash resolves
    all three. The row leaks only the *existence* of a token to somebody who
    already holds it; the resource contents stay behind RLS, and a disabled or
    deleted resource still 404s because the view's own scoped query re-checks
    ``public_link_enabled`` / existence after the context is set.

    See ``docs/PORTAL_RLS.md``.
    """

    INVOICE = "invoice"
    ESTIMATE = "estimate"
    CSAT = "csat"
    RESOURCE_CHOICES = (
        (INVOICE, "Invoice"),
        (ESTIMATE, "Estimate"),
        (CSAT, "CSAT survey"),
    )

    id = models.UUIDField(default=uuid.uuid4, primary_key=True)
    token_hash = models.CharField(max_length=64, unique=True, db_index=True)
    org = models.ForeignKey(
        "common.Org", on_delete=models.CASCADE, related_name="portal_access_tokens"
    )
    resource_type = models.CharField(max_length=16, choices=RESOURCE_CHOICES)
    resource_id = models.UUIDField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "portal_access_token"
        indexes = [
            models.Index(fields=["resource_type", "resource_id"]),
        ]

    def __str__(self):
        return f"PortalAccessToken({self.resource_type}:{self.token_hash[:8]}…)"


# Import SecurityAuditLog so Django discovers it for migrations. The name is
# unused here on purpose, and the suppression has to sit on the line ruff
# attributes the diagnostic to, which is the imported name rather than the
# closing paren. Split across three lines it silently suppressed nothing, and
# `ruff check --fix` would have deleted the import and taken the model's
# migrations with it.
from common.audit_log import SecurityAuditLog  # noqa: F401,E402


class PackApplication(BaseOrgModel):
    """Audit record of one vertical-pack application to one org."""

    pack_id = models.CharField(max_length=64)
    pack_version = models.PositiveIntegerField()
    applied_by = models.ForeignKey(
        "common.Profile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pack_applications",
    )
    result = models.JSONField(
        default=dict,
        help_text="{'created': [...], 'skipped': [...], 'failed': [...]}",
    )

    class Meta:
        verbose_name = "Pack Application"
        verbose_name_plural = "Pack Applications"
        db_table = "pack_application"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["org", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.pack_id} v{self.pack_version} → {self.org.name}"
