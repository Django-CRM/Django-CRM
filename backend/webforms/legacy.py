"""Provisioning the backing web form for a legacy web-to-lead API key.

`CreateLeadFromSite` writes leads through `webforms.service.submit_form`, which
needs a `WebForm` describing what the key collects. Two callers need to build
one and they cannot share a model import:

* the backfill migration, which must use the historical models
  `apps.get_model` hands it;
* `ensure_web_form` below, on the request path, which uses the real ones.

So `build_web_form` takes its model classes as arguments. That is the whole
reason for the signature: it keeps one copy of "what a legacy key collects"
without the migration reaching for models that may have moved on by the time
someone runs it on a fresh database.

WHY THE REQUEST PATH PROVISIONS AT ALL, GIVEN THE MIGRATION
The migration is a one-shot over the rows that existed when it ran. Nothing
else ever set `legacy_api_setting`, so every key minted afterwards through
`common/views/settings_views.py` had no form and the endpoint answered 409 for
good. Provisioning on first use covers every path that can create a key,
including a shell session and a future importer, instead of one of them.
"""

# (lead_field, label, is_required). Exactly what the legacy view read out of
# the request body, in the order a person would fill them in. The two renames
# it needs are in `LEGACY_PARAM_MAP` on the view, because they are properties
# of the request parameters rather than of the form.
LEGACY_FIELDS = [
    ("salutation", "Title", False),
    ("first_name", "First name", False),
    ("last_name", "Last name", False),
    ("email", "Email", True),
    ("phone", "Phone", False),
    ("description", "Message", False),
]


def build_web_form(api_setting, *, web_form_model, field_model, profile_model):
    """Create and return the form backing `api_setting`.

    Assumes the caller has already established there is none. The recipients
    come from the key's own `lead_assigned_to`, falling back to a profile for
    whoever created it: the old view ignored that M2M entirely and notified
    `created_by`, which is why a key's configured recipients heard nothing.

    `lead_source` is "other" rather than the key's `website`. The old view
    assigned the URL to `Lead.source`, which declares `choices=LEAD_SOURCE`, so
    those rows were invisible to every source-based filter.
    """
    recipients = list(api_setting.lead_assigned_to.all())
    if not recipients:
        recipients = list(
            profile_model.objects.filter(
                user_id=api_setting.created_by_id, org=api_setting.org
            )
        )

    form = web_form_model.objects.create(
        name=api_setting.title or "Web to lead",
        org=api_setting.org,
        is_published=True,
        assign_to=recipients[0] if recipients else None,
        lead_source="other",
        legacy_api_setting=api_setting,
        created_by_id=api_setting.created_by_id,
    )
    if recipients:
        form.notify_profiles.set(recipients)
    tags = list(api_setting.tags.all())
    if tags:
        form.tags.set(tags)

    for order, (lead_field, label, is_required) in enumerate(LEGACY_FIELDS):
        field_model.objects.create(
            form=form,
            org=api_setting.org,
            order=order,
            source="lead",
            lead_field=lead_field,
            label=label,
            is_required=is_required,
            created_by_id=api_setting.created_by_id,
        )
    return form


def ensure_web_form(api_setting):
    """The form backing this key, creating it on first use.

    Returns the existing form untouched when there is one. It belongs to
    whoever has been editing it since, so re-deriving its fields or flipping
    `is_published` back on here would make unpublishing a legacy form
    impossible.
    """
    from webforms.models import WebForm, WebFormField

    existing = WebForm.objects.filter(legacy_api_setting=api_setting).first()
    if existing is not None:
        return existing

    from common.models import Profile

    return build_web_form(
        api_setting,
        web_form_model=WebForm,
        field_model=WebFormField,
        profile_model=Profile,
    )
