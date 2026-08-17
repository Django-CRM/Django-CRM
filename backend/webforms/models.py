"""Embeddable web forms that create leads (issue #634).

Four models. `WebForm` is the configuration an admin edits, `WebFormField` is
one ordered question on it, `WebFormSubmission` is one POST from a visitor
(stored whether it was accepted or rejected, which is what makes spam review
and an honest conversion rate possible), and `WebFormDailyStat` counts embed
renders so conversion has a denominator.

All four inherit BaseOrgModel and are registered in
`common/rls/__init__.py:ORG_SCOPED_TABLES`. Note that a subclass declaring its
own `class Meta` REPLACES the parent's rather than merging it, so each one
restates `indexes = [Index(fields=["org", "-created_at"])]`.
`common/tests/test_org_index_coverage.py` fails if one forgets, which is how
`orders` silently lost both of its per-org indexes.
"""

from django.db import models

from common.base import BaseOrgModel
from common.models import APISettings, CustomFieldDefinition, Profile, Tags
from common.utils import LEAD_SOURCE
from webforms.constants import LEAD_FIELD_CHOICES


def _org_index():
    """A fresh Index instance per model.

    Django mutates an Index when it is attached to a model (it stamps a
    generated name onto it), so sharing one instance across four Meta classes
    makes the later ones collide on that name.
    """
    return models.Index(fields=["org", "-created_at"])


class WebForm(BaseOrgModel):
    """One embeddable form.

    The UUID primary key is the public identifier, and that is deliberate: the
    embed hands it to every visitor by design, so obscurity is not a control
    here. `allowed_origins`, the honeypot, the throttles and the optional
    captcha are the controls.
    """

    SUCCESS_MESSAGE = "message"
    SUCCESS_REDIRECT = "redirect"
    SUCCESS_MODE_CHOICES = [
        (SUCCESS_MESSAGE, "Show a message"),
        (SUCCESS_REDIRECT, "Redirect to a URL"),
    ]

    CAPTCHA_NONE = ""
    CAPTCHA_TURNSTILE = "turnstile"
    CAPTCHA_CHOICES = [
        (CAPTCHA_NONE, "None"),
        (CAPTCHA_TURNSTILE, "Cloudflare Turnstile"),
    ]

    name = models.CharField(max_length=255)
    is_published = models.BooleanField(
        default=False,
        help_text=(
            "Only published forms accept submissions. Flipped through the "
            "publish and unpublish endpoints, never through a plain update."
        ),
    )
    allowed_origins = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            "Origins permitted to frame this form or POST to it. "
            "An empty list means unrestricted."
        ),
    )
    submit_button_label = models.CharField(max_length=64, default="Submit")

    success_mode = models.CharField(
        max_length=16, choices=SUCCESS_MODE_CHOICES, default=SUCCESS_MESSAGE
    )
    success_message = models.TextField(
        blank=True, default="Thanks. We will be in touch shortly."
    )
    redirect_url = models.URLField(max_length=500, blank=True, default="")

    assign_to = models.ForeignKey(
        Profile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="webforms_assigned",
    )
    notify_profiles = models.ManyToManyField(
        Profile, blank=True, related_name="webforms_notified"
    )
    # Must be a LEAD_SOURCE member, because Lead.source declares those as its
    # choices. Which form a lead came from is recorded by
    # WebFormSubmission.form, not by writing a URL into this column the way
    # the legacy view does.
    lead_source = models.CharField(max_length=32, choices=LEAD_SOURCE, default="other")
    tags = models.ManyToManyField(Tags, blank=True, related_name="webforms")

    captcha_provider = models.CharField(
        max_length=16, choices=CAPTCHA_CHOICES, default=CAPTCHA_NONE, blank=True
    )
    captcha_site_key = models.CharField(max_length=255, blank=True, default="")
    captcha_secret = models.CharField(max_length=255, blank=True, default="")
    reject_disposable_email = models.BooleanField(default=True)

    legacy_api_setting = models.OneToOneField(
        APISettings,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="web_form",
        help_text=(
            "Set by the backfill migration. Links a legacy web-to-lead API "
            "key to the form that now backs it."
        ),
    )

    class Meta:
        verbose_name = "Web form"
        verbose_name_plural = "Web forms"
        db_table = "web_form"
        ordering = ("-created_at",)
        indexes = [_org_index()]

    def __str__(self):
        return self.name


class WebFormField(BaseOrgModel):
    """One ordered question on a form.

    The row IS the field mapping: `lead_field` names the Lead column a value
    lands in, or `custom_field` names a CustomFieldDefinition whose value lands
    in `Lead.custom_fields`. Exactly one of the two is set.
    """

    SOURCE_LEAD = "lead"
    SOURCE_CUSTOM = "custom"
    SOURCE_CHOICES = [
        (SOURCE_LEAD, "Lead field"),
        (SOURCE_CUSTOM, "Custom field"),
    ]

    form = models.ForeignKey(WebForm, on_delete=models.CASCADE, related_name="fields")
    order = models.PositiveIntegerField(default=0)
    source = models.CharField(max_length=16, choices=SOURCE_CHOICES)
    lead_field = models.CharField(
        max_length=32, choices=LEAD_FIELD_CHOICES, blank=True, default=""
    )
    custom_field = models.ForeignKey(
        CustomFieldDefinition,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="webform_fields",
    )
    label = models.CharField(max_length=128)
    placeholder = models.CharField(max_length=128, blank=True, default="")
    is_required = models.BooleanField(default=False)

    class Meta:
        verbose_name = "Web form field"
        verbose_name_plural = "Web form fields"
        db_table = "web_form_field"
        ordering = ("order", "created_at")
        indexes = [_org_index(), models.Index(fields=["form", "order"])]
        constraints = [
            models.UniqueConstraint(
                fields=["form", "lead_field"],
                condition=~models.Q(lead_field=""),
                name="web_form_field_unique_lead_field",
            ),
            models.UniqueConstraint(
                fields=["form", "custom_field"],
                condition=models.Q(custom_field__isnull=False),
                name="web_form_field_unique_custom_field",
            ),
            # Exactly one of the two. Enforced here and not only in the
            # serializer, so a data migration or a shell session cannot create
            # a row the dynamic serializer would then not know how to read.
            models.CheckConstraint(
                condition=(
                    models.Q(lead_field="", custom_field__isnull=False)
                    | (~models.Q(lead_field="") & models.Q(custom_field__isnull=True))
                ),
                name="web_form_field_exactly_one_target",
            ),
        ]

    def __str__(self):
        return f"{self.form.name}: {self.label}"

    @property
    def input_name(self):
        """The name this field carries in the submitted payload.

        A Lead field is named after its column; a custom field after its
        definition key. Both templates and the dynamic serializer read this,
        so the two cannot disagree about what an input is called.
        """
        if self.source == self.SOURCE_CUSTOM and self.custom_field_id:
            return self.custom_field.key
        return self.lead_field


class WebFormSubmission(BaseOrgModel):
    """One POST from a visitor, accepted or not.

    Rejected rows are kept. Without them there is no spam review, and the
    conversion rate silently counts only the submissions that worked.
    """

    ACCEPTED = "accepted"
    ACCEPTED_DUPLICATE = "accepted_duplicate"
    REJECTED_SPAM = "rejected_spam"
    REJECTED_INVALID = "rejected_invalid"
    STATUS_CHOICES = [
        (ACCEPTED, "Accepted"),
        (ACCEPTED_DUPLICATE, "Accepted, merged into an existing lead"),
        (REJECTED_SPAM, "Rejected as spam"),
        (REJECTED_INVALID, "Rejected as invalid"),
    ]

    ACCEPTED_STATUSES = (ACCEPTED, ACCEPTED_DUPLICATE)

    form = models.ForeignKey(
        WebForm, on_delete=models.CASCADE, related_name="submissions"
    )
    lead = models.ForeignKey(
        "leads.Lead",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="webform_submissions",
    )
    # The VALIDATED dict, never raw request.data. Persisting the raw body would
    # let a submitter store an arbitrary blob of keys that later renders in an
    # admin's browser.
    payload = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES)
    # Internal triage detail. Never echoed to the submitter: telling a bot
    # which control caught it is how it learns to get past that control.
    reject_reason = models.CharField(max_length=255, blank=True, default="")
    # Informational and throttle bucketing only. X-Forwarded-For is
    # submitter-controlled, so this is never an input to an authorization
    # decision.
    submitted_ip = models.GenericIPAddressField(null=True, blank=True)
    referer = models.CharField(max_length=512, blank=True, default="")

    class Meta:
        verbose_name = "Web form submission"
        verbose_name_plural = "Web form submissions"
        db_table = "web_form_submission"
        ordering = ("-created_at",)
        indexes = [
            _org_index(),
            models.Index(fields=["form", "-created_at"]),
            models.Index(fields=["form", "status"]),
        ]

    def __str__(self):
        return f"{self.form.name} {self.created_at:%Y-%m-%d} {self.status}"


class WebFormDailyStat(BaseOrgModel):
    """Embed renders per form per day.

    Views need their own row because nothing else counts them, and a row per
    pageview is too many rows for a marketing form. Submissions are NOT
    denormalised here: the analytics endpoint counts them from
    WebFormSubmission, so each number has exactly one source of truth.
    """

    form = models.ForeignKey(
        WebForm, on_delete=models.CASCADE, related_name="daily_stats"
    )
    date = models.DateField()
    views = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "Web form daily stat"
        verbose_name_plural = "Web form daily stats"
        db_table = "web_form_daily_stat"
        ordering = ("-date",)
        indexes = [_org_index(), models.Index(fields=["form", "-date"])]
        constraints = [
            models.UniqueConstraint(
                fields=["form", "date"], name="web_form_daily_stat_unique_day"
            ),
        ]

    def __str__(self):
        return f"{self.form.name} {self.date}: {self.views} views"
