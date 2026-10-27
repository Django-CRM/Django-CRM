"""The record types the app knows as objects, and what each one supports.

Callers ask the registry which object a target name or a record belongs to,
and read its capability flags, instead of keeping their own per-model lists.
Read rules are not restated here: an entry points at its module's own
`visible_*_qs` by dotted path. Models and rules resolve at call time, never
cached, because those modules import from `common` and a module-level import
would be circular.

An installed app adds per-org entries with `register_hook`. `model_label`,
`read_rule`, `edit_rule`, `automation`, `list_spec`, `search` and `webhook`
are for the registry's own use. Callers use only `key`, `label`,
`label_plural`, `custom_field_target`, `attachments`, `activity_feed`, `links`,
`tags_key`, `model`, `records(org)`, `readable(profile)`, `editable(profile)`,
`automation_adapter()`, `saved_list()`, `search_hits(profile, q, limit)` and
`webhook_spec()`.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from django.apps import apps
from django.utils.module_loading import import_string

from common.modules import model_module, module_is_available


@dataclass(frozen=True)
class ObjectEntry:
    key: str
    model_label: str
    custom_field_target: str
    read_rule: str
    attachments: bool
    activity_feed: bool
    label: str
    label_plural: str
    links: bool
    edit_rule: str | None
    automation: str | None = None
    list_spec: str | None = None
    search: str | None = None
    tags_key: str | None = None
    webhook: str | None = None

    @property
    def model(self):
        return apps.get_model(self.model_label)

    @property
    def module(self):
        return model_module(self.model_label)

    def records(self, org):
        return self.model.objects.filter(org=org)

    def readable(self, profile):
        return import_string(self.read_rule)(profile)

    def editable(self, profile):
        if self.edit_rule is None:
            return self.model.objects.none()
        return import_string(self.edit_rule)(profile)

    def automation_adapter(self):
        """What rules use to reach this object's records, or None if they cannot."""
        if self.automation is None:
            return None
        return import_string(self.automation)(self)

    def saved_list(self):
        """The `common.saved_views.SavedList` views on this object's list are
        saved against, or None if the list takes none."""
        if self.list_spec is None:
            return None
        return import_string(self.list_spec)

    def search_hits(self, profile, q, limit):
        """At most `limit` hits `{type, id, title, subtitle}` for `q` among the
        records `profile` may open, or None if the object is not searched."""
        if self.search is None:
            return None
        return import_string(self.search)(profile, q, limit)

    def webhook_spec(self):
        """The `webhooks.events.WebhookSpec` this object's events are sent
        under, or None if it sends none."""
        if self.webhook is None:
            return None
        return import_string(self.webhook)


class RegistryConflict(Exception):
    pass


_HOOKS = []


BUILT_IN = (
    ObjectEntry(
        "lead",
        "leads.Lead",
        "Lead",
        "leads.access.visible_leads_qs",
        attachments=True,
        activity_feed=True,
        label="Lead",
        label_plural="Leads",
        links=True,
        edit_rule="leads.access.visible_leads_qs",
        automation="automation.adapters.leads.LeadAdapter",
        list_spec="common.saved_views.LEAD_LIST",
        search="common.search_views.search_leads",
        tags_key="leads",
        webhook="webhooks.events.LEAD_WEBHOOK",
    ),
    ObjectEntry(
        "contact",
        "contacts.Contact",
        "Contact",
        "contacts.access.visible_contacts_qs",
        attachments=True,
        activity_feed=True,
        label="Contact",
        label_plural="Contacts",
        links=True,
        edit_rule="contacts.access.visible_contacts_qs",
        automation="automation.adapters.contacts.ContactAdapter",
        list_spec="common.saved_views.CONTACT_LIST",
        search="common.search_views.search_contacts",
        tags_key="contacts",
        webhook="webhooks.events.CONTACT_WEBHOOK",
    ),
    ObjectEntry(
        "account",
        "accounts.Account",
        "Account",
        "accounts.access.visible_accounts_qs",
        attachments=True,
        activity_feed=True,
        label="Account",
        label_plural="Accounts",
        links=True,
        edit_rule="accounts.access.visible_accounts_qs",
        automation="automation.adapters.accounts.AccountAdapter",
        list_spec="common.saved_views.ACCOUNT_LIST",
        search="common.search_views.search_accounts",
        tags_key="accounts",
        webhook="webhooks.events.ACCOUNT_WEBHOOK",
    ),
    ObjectEntry(
        "deal",
        "opportunity.Opportunity",
        "Opportunity",
        "opportunity.access.visible_deals_qs",
        attachments=True,
        activity_feed=True,
        label="Deal",
        label_plural="Deals",
        links=True,
        edit_rule="opportunity.access.visible_deals_qs",
        automation="automation.adapters.deals.DealAdapter",
        list_spec="common.saved_views.DEAL_LIST",
        search="common.search_views.search_deals",
        tags_key="opportunities",
        webhook="webhooks.events.DEAL_WEBHOOK",
    ),
    ObjectEntry(
        "ticket",
        "cases.Case",
        "Case",
        "cases.access.visible_cases_qs",
        attachments=True,
        activity_feed=True,
        label="Ticket",
        label_plural="Tickets",
        links=True,
        edit_rule="cases.access.writable_cases_qs",
        automation="automation.adapters.tickets.TicketAdapter",
        list_spec="common.saved_views.TICKET_LIST",
        search="common.search_views.search_tickets",
        tags_key="cases",
        webhook="webhooks.events.TICKET_WEBHOOK",
    ),
    ObjectEntry(
        "task",
        "tasks.Task",
        "Task",
        "tasks.access.visible_tasks_qs",
        attachments=True,
        activity_feed=True,
        label="Task",
        label_plural="Tasks",
        links=True,
        edit_rule="tasks.access.visible_tasks_qs",
        automation="automation.adapters.tasks.TaskAdapter",
        tags_key="tasks",
        webhook="webhooks.events.TASK_WEBHOOK",
    ),
    ObjectEntry(
        "project",
        "projects.Project",
        "Project",
        "projects.access.visible_projects_qs",
        attachments=True,
        activity_feed=False,
        label="Project",
        label_plural="Projects",
        links=False,
        edit_rule=None,
        tags_key="projects",
    ),
    ObjectEntry(
        "invoice",
        "invoices.Invoice",
        "Invoice",
        "invoices.permissions.visible_invoices_qs",
        attachments=True,
        activity_feed=True,
        label="Invoice",
        label_plural="Invoices",
        links=False,
        edit_rule=None,
        list_spec="common.saved_views.INVOICE_LIST",
        search="common.search_views.search_invoices",
        webhook="webhooks.events.INVOICE_WEBHOOK",
    ),
    ObjectEntry(
        "estimate",
        "invoices.Estimate",
        "Estimate",
        "invoices.permissions.visible_estimates_qs",
        attachments=False,
        activity_feed=False,
        label="Estimate",
        label_plural="Estimates",
        links=False,
        edit_rule=None,
    ),
    ObjectEntry(
        "recurring_invoice",
        "invoices.RecurringInvoice",
        "RecurringInvoice",
        "invoices.permissions.visible_recurring_qs",
        attachments=False,
        activity_feed=False,
        label="Recurring invoice",
        label_plural="Recurring invoices",
        links=False,
        edit_rule=None,
    ),
)


def built_in_objects(org):
    """BUILT_IN less the entries whose module is not available to `org`."""
    return tuple(
        e for e in BUILT_IN if e.module is None or module_is_available(org, e.module)
    )


def all_objects(org):
    """BUILT_IN, then each hook's entries for `org`, less off-module entries.

    No hook runs when org is None, and nothing is dropped. Conflicts are checked
    over every entry. A hook entry with no `module` (a custom object) stays.
    """
    entries = BUILT_IN
    if org is not None:
        extra = [e for hook in _HOOKS for e in hook(org)]
        entries = (*BUILT_IN, *extra)
    seen_keys, seen_targets = set(), set()
    for entry in entries:
        for seen, value in (
            (seen_keys, entry.key),
            (seen_targets, entry.custom_field_target),
        ):
            if value in seen:
                raise RegistryConflict(f"{value!r} is registered twice")
            seen.add(value)
    if org is None:
        return entries
    return tuple(
        e
        for e in entries
        if getattr(e, "module", None) is None or module_is_available(org, e.module)
    )


def object_module(key=None, *, target=None):
    """The module of the built-in entry with this key, or with this custom-field
    target, read from the unfiltered list."""
    field, value = ("key", key) if target is None else ("custom_field_target", target)
    return next((e.module for e in BUILT_IN if getattr(e, field) == value), None)


def register_hook(hook: Callable[[object], Iterable]) -> None:
    if hook not in _HOOKS:
        _HOOKS.append(hook)


def object_for_key(org, key):
    return next((e for e in all_objects(org) if e.key == key), None)


def object_for_target(org, target):
    return next((e for e in all_objects(org) if e.custom_field_target == target), None)


def object_for_record(org, content_type, object_id):
    """The entry the record at `content_type` and `object_id` belongs to.

    One entry on the content type is decided with no query. When several share
    it (one model holding many objects), the one whose records hold the id wins.
    Never calls `content_type.model_class()`, which is None for a model that is
    gone.
    """
    matches = [
        e
        for e in all_objects(org)
        if (e.model._meta.app_label, e.model._meta.model_name)
        == (content_type.app_label, content_type.model)
    ]
    if len(matches) <= 1:
        return matches[0] if matches else None
    return next(
        (e for e in matches if e.records(org).filter(pk=object_id).exists()), None
    )
