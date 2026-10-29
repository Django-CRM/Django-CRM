"""Checking a rule's JSON against what its org offers rules.

Runs when a rule is saved and again before each run, so a rule cannot set a
field or option its edit form would refuse, or name a person, endpoint or
link outside its org. Every id is looked up with the rule's org.
"""

import json
import re
import uuid
from collections import Counter
from datetime import date
from decimal import Decimal, InvalidOperation

from automation.capture import CREATED, UPDATED
from automation.conditions import OPERATORS, UPDATE_ONLY
from automation.limits import MAX_ACTIONS, MAX_CONDITIONS
from automation.links import link_specs
from automation.models import ALL, ANY
from automation.objects import adapter_for, switched_off_module
from common.models import Profile
from common.modules import module_is_available, module_off_message
from tasks.models import Task
from webhooks.models import JSON, WebhookEndpoint

NO_VALUE = ("empty", "not_empty", "true", "false")
RECIPIENTS = ("record_assignees", "record_creator", "parent_assignees", "admins")
LINKED = "linked:"
ASSIGN_TO = ("record", "parent", "owner", "person")
PRIORITIES = tuple(value for value, _ in Task.PRIORITY_CHOICES)
MAX_TITLE = 200
MAX_MESSAGE = 500
MAX_DUE_IN_DAYS = 365
ROTATE_MIN, ROTATE_MAX = 2, 20

TRIGGER_KEYS = {"event", "field", "to"}
CONDITION_KEYS = {"field", "op", "value"}
ACTION_KEYS = {
    "set_field": {"type", "target", "field", "value"},
    "assign": {"type", "people", "mode"},
    "create_task": {"type", "title", "due_in_days", "priority", "assign_to", "person"},
    "notify": {"type", "to", "people", "message"},
    "webhook": {"type", "endpoint"},
}

UNKNOWN_FIELD = "This field is not available."
NOT_A_MEMBER = "This person is not an active member of this org."
NOT_ALL_MEMBERS = "Each person must be an active member of this org."
ENTER_A_VALUE = "Enter a value."

_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def active_people(org):
    """The org's members a rule may name: active profiles of active users."""
    return Profile.objects.filter(org=org, is_active=True, user__is_active=True)


def offered_actions(org):
    """The action types a new step may take: Create a task only while Tasks is on."""
    if module_is_available(org, "tasks"):
        return list(ACTION_KEYS)
    return [kind for kind in ACTION_KEYS if kind != "create_task"]


def _task_step_key(action):
    """A Create a task step by its content: a missing key reads as null."""
    return tuple(
        json.dumps(action.get(key), sort_keys=True)
        for key in sorted(ACTION_KEYS["create_task"])
    )


def new_task_step_errors(org, actions, stored) -> dict[str, list[str]]:
    """While Tasks is off, a Create a task step the stored rule lacks is refused.
    A stored step covers one sent step, whatever its position; the rest are new."""
    if module_is_available(org, "tasks") or not isinstance(actions, list):
        return {}
    kept = Counter(
        _task_step_key(step)
        for step in (stored if isinstance(stored, list) else [])
        if isinstance(step, dict) and step.get("type") == "create_task"
    )
    errors = {}
    for index, action in enumerate(actions):
        if not isinstance(action, dict) or action.get("type") != "create_task":
            continue
        key = _task_step_key(action)
        if kept[key] > 0:
            kept[key] -= 1
        else:
            errors[f"actions[{index}].type"] = [module_off_message(org, "tasks")]
    return errors


def validate_rule(org, data: dict) -> dict[str, list[str]]:
    """Errors keyed by path ("trigger.to", "actions[0].people"); `{}` when valid."""
    return _Check(org).rule(data)


def _uuid(value):
    """`value` as a UUID if it is one in canonical form, else None.

    Conditions compare ids as strings, so an uppercase or unhyphenated id
    would save and then never match.
    """
    if not isinstance(value, str):
        return None
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        return None
    return parsed if str(parsed) == value else None


def _is_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return False
    try:
        return Decimal(str(value)).is_finite()
    except InvalidOperation:
        return False


def _is_date(value):
    if not isinstance(value, str) or not _ISO_DATE.fullmatch(value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _is_text(value, limit):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= limit


def _blank(value):
    return value is None or value == ""


class _Check:
    def __init__(self, org):
        self.org = org
        self.errors = {}
        self._catalogues = {}
        self._active = {}
        self._links = None

    def add(self, path, message):
        self.errors.setdefault(path, []).append(message)

    def rule(self, data):
        key = data.get("object_key")
        adapter = adapter_for(self.org, key) if isinstance(key, str) else None
        if adapter is None:
            module = switched_off_module(self.org, key)
            if module is not None:
                self.add("object_key", module_off_message(self.org, module))
            else:
                self.add("object_key", "Rules cannot be built on this object.")
            return self.errors
        self.key, self.adapter = key, adapter
        self._catalogues[key] = {spec.key: spec for spec in adapter.fields(self.org)}
        self.fields = self._catalogues[key]

        if data.get("match", ALL) not in (ALL, ANY):
            self.add("match", "Choose all or any.")
        event = self.trigger(data.get("trigger"))
        self.conditions(data.get("conditions", []), event)
        self.actions(data.get("actions", []))
        return self.errors

    def catalogue(self, key):
        if key not in self._catalogues:
            adapter = adapter_for(self.org, key)
            self._catalogues[key] = (
                None
                if adapter is None
                else {spec.key: spec for spec in adapter.fields(self.org)}
            )
        return self._catalogues[key]

    def link(self, spec_id):
        """The org's link spec `spec_id` on the rule's object, if rules can reach it."""
        if self._links is None:
            self._links = link_specs(self.org, self.key)
        spec = next((s for s in self._links if s.id == spec_id), None)
        if spec is None or self.catalogue(spec.object_key) is None:
            return None
        return spec

    def unknown_keys(self, path, item, allowed):
        extra = sorted(str(key)[:40] for key in item if key not in allowed)
        if extra:
            noun = "key" if len(extra) == 1 else "keys"
            self.add(path, f"Unknown {noun}: {', '.join(extra[:5])}.")

    def members(self, ids):
        """Whether every id in `ids` is one of `active_people`."""
        missing = {pk for pk in ids if pk not in self._active}
        if missing:
            found = set(
                active_people(self.org)
                .filter(pk__in=missing)
                .values_list("pk", flat=True)
            )
            self._active.update({pk: pk in found for pk in missing})
        return all(self._active[pk] for pk in ids)

    def person_error(self, value):
        pk = _uuid(value)
        return None if pk is not None and self.members([pk]) else NOT_A_MEMBER

    def people_error(self, value, *, at_most=None):
        if not isinstance(value, list) or not value:
            return "Choose at least one person."
        if at_most is not None and len(value) > at_most:
            return f"Choose at most {at_most} people."
        ids = [_uuid(item) for item in value]
        if None in ids or not self.members(ids):
            return NOT_ALL_MEMBERS
        if len(set(ids)) != len(ids):
            return "Choose each person once."
        return None

    def value_error(self, spec, value, *, setting=False):
        """Why `value` does not fit `spec`'s type, or None."""
        kind = spec.type
        if kind == "text":
            return None if isinstance(value, str) else "Enter text."
        if kind == "number":
            return None if _is_number(value) else "Enter a number."
        if kind == "date":
            return None if _is_date(value) else "Enter a date as YYYY-MM-DD."
        if kind == "boolean":
            return None if isinstance(value, bool) else "Enter true or false."
        if kind == "choice":
            choice = next((c for c in spec.choices if c.value == value), None)
            if choice is None:
                return "Choose one of the options."
            if setting and not choice.settable:
                return "A rule cannot set this option."
            return None
        if kind == "person":
            return self.person_error(value)
        if kind == "people":
            return self.people_error(value)
        return "This field cannot be used in rules."

    def trigger(self, trigger):
        if not isinstance(trigger, dict):
            self.add("trigger", "Choose what sets the rule off.")
            return None
        self.unknown_keys("trigger", trigger, TRIGGER_KEYS)
        event, field, to = trigger.get("event"), trigger.get("field"), trigger.get("to")
        if event not in (CREATED, UPDATED):
            self.add("trigger.event", "Choose created or updated.")
            return None
        if event == CREATED:
            if field is not None:
                self.add("trigger.field", "A created trigger takes no field.")
            if to is not None:
                self.add("trigger.to", "A created trigger takes no value.")
            return event
        if field is None:
            if to is not None:
                self.add("trigger.to", "Choose a field for this value.")
            return event
        spec = self.fields.get(field) if isinstance(field, str) else None
        if spec is None:
            self.add("trigger.field", UNKNOWN_FIELD)
        elif spec.type == "people":
            self.add("trigger.field", "A change of people cannot set off a rule.")
        elif to is not None:
            error = ENTER_A_VALUE if to == "" else self.value_error(spec, to)
            if error:
                self.add("trigger.to", error)
        return event

    def conditions(self, conditions, event):
        if not isinstance(conditions, list):
            self.add("conditions", "Conditions must be a list.")
            return
        if len(conditions) > MAX_CONDITIONS:
            self.add("conditions", f"At most {MAX_CONDITIONS} conditions.")
            return
        for index, condition in enumerate(conditions):
            self.condition(f"conditions[{index}]", condition, event)

    def condition(self, path, condition, event):
        if not isinstance(condition, dict):
            self.add(path, "Each condition must be an object.")
            return
        self.unknown_keys(path, condition, CONDITION_KEYS)
        field, op = condition.get("field"), condition.get("op")
        spec = self.fields.get(field) if isinstance(field, str) else None
        if spec is None:
            self.add(f"{path}.field", UNKNOWN_FIELD)
            return
        if op in UPDATE_ONLY:
            if event != UPDATED:
                self.add(f"{path}.op", "Only an updated trigger has an old value.")
                return
            # Changes to people are not captured, so they have no old value.
            if spec.type == "people":
                self.add(f"{path}.op", "People have no old value to compare.")
                return
        elif op not in OPERATORS.get(spec.type, ()):
            self.add(f"{path}.op", "This operator does not apply to this field.")
            return
        error = self.condition_value_error(spec, op, condition.get("value"))
        if error:
            self.add(f"{path}.value", error)

    def condition_value_error(self, spec, op, value):
        if op in NO_VALUE:
            return None if value is None else "This operator takes no value."
        if _blank(value):
            return ENTER_A_VALUE
        if op in ("in", "not_in"):
            if not isinstance(value, list) or not value:
                return "Choose at least one option."
            return next(filter(None, (self.value_error(spec, v) for v in value)), None)
        if spec.type == "people" and op != "eq":
            return self.person_error(value)
        return self.value_error(spec, value)

    def actions(self, actions):
        if not isinstance(actions, list):
            self.add("actions", "Actions must be a list.")
            return
        if not actions:
            self.add("actions", "Add at least one action.")
            return
        if len(actions) > MAX_ACTIONS:
            self.add("actions", f"At most {MAX_ACTIONS} actions.")
            return
        checks = {
            "set_field": self.set_field,
            "assign": self.assign,
            "create_task": self.create_task,
            "notify": self.notify,
            "webhook": self.webhook,
        }
        for index, action in enumerate(actions):
            path = f"actions[{index}]"
            if not isinstance(action, dict):
                self.add(path, "Each action must be an object.")
                continue
            kind = action.get("type")
            if not isinstance(kind, str) or kind not in checks:
                self.add(f"{path}.type", "Unknown action.")
                continue
            self.unknown_keys(path, action, ACTION_KEYS[kind])
            checks[kind](path, action)

    def target_object(self, path, target):
        """The object key a set_field `target` names, or None after an error."""
        if target == "record":
            return self.key
        if target == "parent":
            parent = self.adapter.parent_key
            if parent is None or self.catalogue(parent) is None:
                self.add(f"{path}.target", "This object has no parent a rule can set.")
                return None
            return parent
        spec = self.link(target) if isinstance(target, str) else None
        if spec is None:
            self.add(f"{path}.target", "Choose the record, its parent or a link.")
            return None
        return spec.object_key

    def set_field(self, path, action):
        key = self.target_object(path, action.get("target"))
        if key is None:
            return
        field = action.get("field")
        spec = self.catalogue(key).get(field) if isinstance(field, str) else None
        if spec is None:
            self.add(f"{path}.field", UNKNOWN_FIELD)
            return
        if not spec.settable:
            self.add(f"{path}.field", "A rule cannot set this field.")
            return
        if "value" not in action:
            self.add(f"{path}.value", "Enter a value, or null to clear the field.")
            return
        value = action["value"]
        error = None if value is None else self.value_error(spec, value, setting=True)
        if error:
            self.add(f"{path}.value", error)

    def assign(self, path, action):
        spec = self.fields.get("assigned_to")
        if spec is None or spec.type != "people" or not spec.settable:
            self.add(f"{path}.type", "This object has no assignees a rule can set.")
            return
        mode, people = action.get("mode"), action.get("people")
        if mode not in ("replace", "rotate"):
            self.add(f"{path}.mode", "Choose replace or rotate.")
            return
        error = self.people_error(people, at_most=ROTATE_MAX)
        if error is None and mode == "replace" and len(people) != 1:
            error = "Replace takes exactly one person."
        if error is None and mode == "rotate" and len(people) < ROTATE_MIN:
            error = f"Rotate takes {ROTATE_MIN} to {ROTATE_MAX} people."
        if error:
            self.add(f"{path}.people", error)

    def create_task(self, path, action):
        if not _is_text(action.get("title"), MAX_TITLE):
            self.add(f"{path}.title", f"Enter a title of 1 to {MAX_TITLE} characters.")
        days = action.get("due_in_days")
        if days is not None and (
            not isinstance(days, int)
            or isinstance(days, bool)
            or not 0 <= days <= MAX_DUE_IN_DAYS
        ):
            self.add(
                f"{path}.due_in_days",
                f"Enter a whole number of days from 0 to {MAX_DUE_IN_DAYS}.",
            )
        if action.get("priority") not in PRIORITIES:
            self.add(f"{path}.priority", "Choose Low, Medium or High.")
        assign_to, person = action.get("assign_to"), action.get("person")
        if assign_to not in ASSIGN_TO:
            self.add(f"{path}.assign_to", "Choose who gets the task.")
        elif assign_to == "person":
            error = "Choose a person." if person is None else self.person_error(person)
            if error:
                self.add(f"{path}.person", error)
        elif person is not None:
            self.add(f"{path}.person", "Only a task for a chosen person takes one.")

    def notify(self, path, action):
        to, people = action.get("to"), action.get("people")
        to = [] if to is None else to
        people = [] if people is None else people
        if not isinstance(to, list) or not all(map(self.recipient, to)):
            self.add(f"{path}.to", "Choose recipients from the list.")
        elif to == [] and people == []:
            self.add(f"{path}.to", "Choose who to notify.")
        error = None if people == [] else self.people_error(people)
        if error:
            self.add(f"{path}.people", error)
        if not _is_text(action.get("message"), MAX_MESSAGE):
            self.add(
                f"{path}.message", f"Enter a message of 1 to {MAX_MESSAGE} characters."
            )

    def recipient(self, source):
        if not isinstance(source, str):
            return False
        if source.startswith(LINKED):
            return self.link(source[len(LINKED) :]) is not None
        return source in RECIPIENTS

    def webhook(self, path, action):
        pk = _uuid(action.get("endpoint"))
        endpoints = WebhookEndpoint.objects.filter(
            org=self.org, is_active=True, format=JSON, app__isnull=True
        )
        if pk is None or not endpoints.filter(pk=pk).exists():
            self.add(f"{path}.endpoint", "Choose an active JSON endpoint of this org.")
