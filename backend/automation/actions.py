"""What a rule's actions do, each split into a plan step and an apply step.

`plan_action` resolves the records and people an action reaches and checks its
values, writing nothing, so "try it on a record" runs plans alone. The engine
calls each `Planned.apply` inside the rule's transaction, acting as its owner.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from automation.limits import MAX_RECORDS_PER_ACTION
from automation.links import link_specs, linked_records
from automation.models import AutomationRotation, AutomationRule
from automation.objects import Adapter, adapter_for
from automation.tasks import mark_email_failed, send_member_email
from automation.validation import LINKED, UNKNOWN_FIELD, active_people
from common import notifications
from common.links import frontend_url
from common.models import Org, Profile
from common.modules import module_is_available, module_off_message
from common.org_time import org_timezone
from common.permissions import is_org_admin
from tasks.models import Task
from tasks.updates import create_task
from webhooks import emit
from webhooks.models import JSON, WebhookEndpoint

logger = logging.getLogger(__name__)

EVENT = "rule.triggered"
MESSAGE_VERB = "automation.message"
NOBODY_ACTIVE = "None of the chosen people is an active member"
ENDPOINT_GONE = "This webhook endpoint is switched off or gone"
NO_PAYLOAD = "Webhooks for this kind of record come in a later version"
REFUSED = "The change was refused"
NOBODY_FOR_TASK = "Nobody to give the task to"
NOBODY_TO_NOTIFY = "Nobody to notify"
BY_ROTATION = " by rotation"
SUBJECT_LENGTH = 100
TITLE_LENGTH = Task._meta.get_field("title").max_length
# An earlier action of these types can change who a later one reaches.
CHANGES_PEOPLE = ("assign", "set_field")


class StepSkipped(Exception):
    """The action has nothing to do on this record; the rule carries on."""

    def __init__(self, text):
        super().__init__(text)
        self.text = text


class StepFailed(Exception):
    """The action cannot be done; the rule's writes roll back."""

    def __init__(self, text):
        super().__init__(text)
        self.text = text


@dataclass(frozen=True)
class RunContext:
    org: Org
    rule: AutomationRule
    record: Any
    adapter: Adapter
    owner: Profile
    run_id: UUID | None = None


@dataclass(frozen=True)
class Planned:
    """`text` says what `apply` would do; `apply` does it and says what it did."""

    text: str
    apply: Callable[[], str]


def render(text: str, record_name: str) -> str:
    """`text` with each `{name}` replaced by `record_name`, and nothing else."""
    return text.replace("{name}", record_name)


def plan_action(ctx: RunContext, index: int, action: dict) -> Planned:
    """Plan `action`, the rule's action at `index`, on `ctx.record`.

    Raises `StepSkipped` when there is nothing to do and `StepFailed` when it
    cannot be done. Writes nothing; the returned `apply` does the writing, and
    resolves the record, its targets and its people again first, because the
    rule's earlier steps run between planning and applying.
    """
    planner = _PLANNERS.get(action.get("type"))
    if planner is None:
        raise StepFailed("This action is not available")
    return planner(ctx, index, action)


def _label(adapter):
    return adapter.entry.label.lower()


def _person(profile):
    return profile.user.name or "Unnamed member"


def _listed(names, limit=None):
    """`names` as "A, B and C", counting those past `limit` as "N more"."""
    names = list(names)
    if limit is not None and len(names) > limit + 1:
        names = [*names[:limit], f"{len(names) - limit} more"]
    if len(names) <= 1:
        return "".join(names)
    return f"{', '.join(names[:-1])} and {names[-1]}"


def _joined(*sentences):
    return ". ".join(sentence for sentence in sentences if sentence)


def _first_message(errors):
    if isinstance(errors, dict):
        errors = list(errors.values())
    if isinstance(errors, (list, tuple)):
        return next(filter(None, map(_first_message, errors)), "")
    return str(errors)


def _members(org, ids):
    """The active members of `org` among `ids`, once each, in the order given."""
    wanted = list(dict.fromkeys(str(pk) for pk in ids))
    try:
        found = {
            str(profile.pk): profile
            for profile in active_people(org)
            .filter(pk__in=wanted)
            .select_related("user")
        }
    except (DjangoValidationError, ValueError):
        return []
    return [found[pk] for pk in wanted if pk in found]


def _clipped(text, limit):
    return text if len(text) <= limit else f"{text[: limit - 3].rstrip()}..."


def _fresh(ctx):
    """The record as the rule's earlier steps left it."""
    return ctx.adapter.load(ctx.org, ctx.record.pk) or ctx.record


def _after_a_change(ctx, index):
    """Whether an earlier action of the rule may change who this one reaches.

    If so, finding nobody while planning is not final: apply decides.
    """
    earlier = ctx.rule.actions[:index]
    return any(isinstance(a, dict) and a.get("type") in CHANGES_PEOPLE for a in earlier)


def _parent(ctx, record):
    """The record's parent and its adapter; skipped when it has none."""
    parent = ctx.adapter.parent(record)
    adapter = adapter_for(ctx.org, parent[0]) if parent is not None else None
    if adapter is None:
        missing = ctx.adapter.parent_label(record)
        raise StepSkipped(f"This {_label(ctx.adapter)} has no {missing}")
    return adapter, parent[1]


def _linked(ctx, record, spec_id):
    """The link spec `spec_id`, its object's adapter and the records it links."""
    key = ctx.rule.object_key
    spec = next((s for s in link_specs(ctx.org, key) if s.id == spec_id), None)
    adapter = adapter_for(ctx.org, spec.object_key) if spec is not None else None
    if adapter is None:
        raise StepFailed("This link is not available")
    records = linked_records(ctx.org, key, record, spec_id)
    if len(records) > MAX_RECORDS_PER_ACTION:
        raise StepFailed(
            f"{len(records)} linked records: an action may reach at most "
            f"{MAX_RECORDS_PER_ACTION}"
        )
    return spec, adapter, records


def _who_can_open(ctx, record, people):
    """Those of `people` who can open `record`, and a sentence naming the rest."""
    kept, skipped = [], []
    for person in people:
        visible = ctx.adapter.entry.readable(person).filter(pk=record.pk)
        (kept if visible.exists() else skipped).append(person)
    if not skipped:
        return kept, ""
    names = _listed(_person(person) for person in skipped)
    return kept, f"Skipped {names}: cannot open this {_label(ctx.adapter)}"


def _reached(people):
    return _listed(map(_person, people), limit=3) or "nobody yet"


# Set a field


def _targets(ctx, record, target):
    if target == "record":
        return ctx.adapter, [record]
    if target == "parent":
        adapter, parent = _parent(ctx, record)
        return adapter, [parent]
    spec, adapter, records = _linked(ctx, record, target)
    if not records:
        raise StepSkipped(f"This {_label(ctx.adapter)} has no {spec.label.lower()}")
    return adapter, records


def _shown(org, spec, value):
    """`value` as a sentence shows it: labels and names, never keys or ids."""
    if spec.type == "choice":
        choice = next((c for c in spec.choices if c.value == value), None)
        if choice is None:
            raise StepFailed("This option is not available")
        return choice.label
    if spec.type in ("person", "people"):
        ids = value if isinstance(value, list) else [value]
        return _listed(_person(p) for p in _members(org, ids)) or "nobody"
    if spec.type == "boolean":
        return "Yes" if value else "No"
    if spec.type == "text":
        return f"'{value}'"
    return str(value)


def _plan_set_field(ctx, index, action):
    key, value = action.get("field"), action.get("value")

    def resolve(record):
        adapter, targets = _targets(ctx, record, action.get("target"))
        spec = next((s for s in adapter.fields(ctx.org) if s.key == key), None)
        if spec is None or not spec.settable:
            raise StepFailed(UNKNOWN_FIELD)
        if value is None:
            change = (f"clear {spec.label}", f"Cleared {spec.label}")
        else:
            shown = _shown(ctx.org, spec, value)
            change = (f"set {spec.label} to {shown}", f"Set {spec.label} to {shown}")
        where = _listed((adapter.name(target) for target in targets), limit=3)
        return adapter, targets, change, where

    _, _, (would, _), where = resolve(ctx.record)

    def apply():
        adapter, targets, (_, done), where = resolve(_fresh(ctx))
        for target in targets:
            errors = adapter.write(target, {key: value}, ctx.owner)
            if errors:
                failure = _first_message(errors) or REFUSED
                if len(targets) > 1:
                    failure = f"{adapter.name(target)}: {failure}"
                raise StepFailed(failure)
        return f"{done} on {where}"

    return Planned(f"Would {would} on {where}", apply)


# Assign


def _plan_assign(ctx, index, action):
    def pool():
        people = _members(ctx.org, action.get("people") or [])
        if not people:
            raise StepFailed(NOBODY_ACTIVE)
        return people

    def assign(person, how=""):
        record = _fresh(ctx)
        change = {"assigned_to": [str(person.pk)]}
        errors = ctx.adapter.write(record, change, ctx.owner)
        if errors:
            raise StepFailed(_first_message(errors) or REFUSED)
        return f"Assigned {ctx.adapter.name(record)} to {_person(person)}{how}"

    people = pool()
    name = ctx.adapter.name(ctx.record)
    if action.get("mode") != "rotate":
        return Planned(
            f"Would assign {name} to {_person(people[0])}", lambda: assign(pool()[0])
        )

    # Planning reads the cursor without locking or creating its row; apply locks it.
    turn = (
        AutomationRotation.objects.filter(
            org=ctx.org, rule=ctx.rule, action_index=index
        )
        .values_list("next_index", flat=True)
        .first()
        or 0
    )

    def rotate():
        with transaction.atomic():
            rotation, _ = AutomationRotation.objects.select_for_update().get_or_create(
                org=ctx.org, rule=ctx.rule, action_index=index
            )
            current = pool()
            text = assign(current[rotation.next_index % len(current)], BY_ROTATION)
            rotation.next_index += 1
            rotation.save(update_fields=["next_index", "updated_at"])
        return text

    upcoming = _person(people[turn % len(people)])
    return Planned(f"Would assign {name} to {upcoming}{BY_ROTATION}", rotate)


# Create a task


def _task_people(ctx, record, action):
    who = action.get("assign_to")
    if who == "person":
        return _members(ctx.org, [action.get("person")])
    if who == "record":
        people = ctx.adapter.people(record, "assignees")
    elif who == "parent":
        adapter, parent = _parent(ctx, record)
        people = adapter.people(parent, "assignees")
    elif who == "owner":
        people = [ctx.owner]
    else:
        raise StepFailed("Choose who gets the task")
    return _members(ctx.org, [person.pk for person in people])


def _plan_create_task(ctx, index, action):
    if not module_is_available(ctx.org, "tasks"):
        raise StepSkipped(module_off_message(ctx.org, "tasks"))

    def resolve(record):
        name = ctx.adapter.name(record)
        title = _clipped(render(action.get("title") or "", name), TITLE_LENGTH)
        people, note = _who_can_open(ctx, record, _task_people(ctx, record, action))
        return title, people, note

    def said(verb, title, people, note):
        return _joined(f"{verb} the task '{title}' for {_reached(people)}", note)

    title, people, note = resolve(ctx.record)
    if not people and not _after_a_change(ctx, index):
        raise StepSkipped(note or NOBODY_FOR_TASK)

    def apply():
        record = _fresh(ctx)
        title, people, note = resolve(record)
        if not people:
            raise StepSkipped(note or NOBODY_FOR_TASK)
        parent = ctx.adapter.task_parent(record)
        data = {
            "title": title,
            "status": "New",
            "priority": action.get("priority"),
            "assigned_to": [str(person.pk) for person in people],
            **parent,
        }
        if not parent:
            data["description"] = frontend_url(ctx.adapter.path(record))
        days = action.get("due_in_days")
        if days is not None:
            with org_timezone(ctx.org):
                due = timezone.localdate() + timedelta(days=days)
            data["due_date"] = due.isoformat()
        try:
            _, errors = create_task(SimpleNamespace(profile=ctx.owner), data)
        except ValidationError as exc:
            errors = exc.detail
        if errors:
            raise StepFailed(_first_message(errors) or REFUSED)
        return said("Created", title, people, note)

    return Planned(said("Would create", title, people, note), apply)


# Notify


def _recipients(ctx, record, source):
    if source == "record_assignees":
        return ctx.adapter.people(record, "assignees")
    if source == "record_creator":
        return ctx.adapter.people(record, "creator")
    if source == "parent_assignees":
        adapter, parent = _parent(ctx, record)
        return adapter.people(parent, "assignees")
    if source == "admins":
        people = active_people(ctx.org).select_related("user")
        return [person for person in people if is_org_admin(person)]
    if isinstance(source, str) and source.startswith(LINKED):
        _, adapter, records = _linked(ctx, record, source[len(LINKED) :])
        return [p for linked in records for p in adapter.people(linked, "assignees")]
    raise StepFailed("Choose recipients from the list")


def _one_line(text, limit=SUBJECT_LENGTH):
    """`text` fit for an email subject, which may not hold a line break."""
    return _clipped(" ".join(text.split()), limit)


def _queue_email(recipients, subject, message, path, org_id, run_id, step):
    try:
        send_member_email.delay(
            recipients, subject, message, path, org_id, run_id, step
        )
    except Exception:
        # Not the exception: a broker error can carry the broker's URL.
        logger.warning("Could not queue the automation email for run %s", run_id)
        mark_email_failed(org_id, run_id, step)


def _plan_notify(ctx, index, action):
    path = ctx.adapter.path(ctx.record)
    run_id = str(ctx.run_id) if ctx.run_id is not None else None
    org_id = str(ctx.org.pk)

    def resolve(record):
        ids, notes = [], []
        for source in action.get("to") or []:
            try:
                ids += [person.pk for person in _recipients(ctx, record, source)]
            except StepSkipped as exc:
                notes.append(exc.text)
        ids += action.get("people") or []
        people, note = _who_can_open(ctx, record, _members(ctx.org, ids))
        return people, [*notes, note]

    people, notes = resolve(ctx.record)
    if not people and not _after_a_change(ctx, index):
        raise StepSkipped(_joined(*notes) or NOBODY_TO_NOTIFY)

    def apply():
        record = _fresh(ctx)
        people, notes = resolve(record)
        if not people:
            raise StepSkipped(_joined(*notes) or NOBODY_TO_NOTIFY)
        name = ctx.adapter.name(record)
        message = render(action.get("message") or "", name)
        data = {"message": message, "rule": ctx.rule.name}
        for person in people:
            notifications.create(
                person,
                MESSAGE_VERB,
                actor=ctx.owner,
                entity=record,
                entity_name=name[:255],
                link=path,
                data=data,
            )
        recipients = [str(person.pk) for person in people]
        subject = _one_line(message)
        transaction.on_commit(
            lambda: _queue_email(
                recipients, subject, message, path, org_id, run_id, index
            )
        )
        return _joined(f"Notified {_reached(people)}", *notes)

    return Planned(_joined(f"Would notify {_reached(people)}", *notes), apply)


# Send a webhook


def _plan_webhook(ctx, index, action):
    try:
        endpoint = WebhookEndpoint.objects.filter(
            org=ctx.org,
            pk=action.get("endpoint"),
            is_active=True,
            format=JSON,
            app__isnull=True,
        ).first()
    except (DjangoValidationError, ValueError):
        endpoint = None
    if endpoint is None:
        raise StepFailed(ENDPOINT_GONE)
    if ctx.adapter.webhook_data(ctx.record) is None:
        raise StepFailed(NO_PAYLOAD)
    where = urlsplit(endpoint.url).hostname or "the endpoint"

    def apply():
        record = _fresh(ctx)
        data = {
            "rule": {"id": str(ctx.rule.pk), "name": ctx.rule.name},
            "object": ctx.rule.object_key,
            "record": ctx.adapter.webhook_data(record),
        }
        emit.queue_delivery(endpoint, EVENT, emit.envelope(ctx.org.pk, EVENT, data))
        return f"Sent {ctx.adapter.name(record)} to {where}"

    return Planned(f"Would send {ctx.adapter.name(ctx.record)} to {where}", apply)


_PLANNERS = {
    "set_field": _plan_set_field,
    "assign": _plan_assign,
    "create_task": _plan_create_task,
    "notify": _plan_notify,
    "webhook": _plan_webhook,
}
