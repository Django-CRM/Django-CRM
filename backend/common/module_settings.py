"""Reading and changing an org's modules (``GET`` and ``PUT /api/org/modules/``).

``change_modules`` is the only writer of ``Org.modules_off``, ``module_order``
and ``modules_on_at``.
"""

from django.db import transaction
from django.utils import timezone
from django.utils.module_loading import import_string

from common.audit_log import audit_log
from common.modules import (
    KEYS,
    available_modules,
    enabled_modules,
    module,
    module_label,
    ordered_modules,
)
from common.permissions import may_configure

# Module key to the dotted path of a ``fn(org) -> None`` run when that module
# becomes available again, so it starts fresh.
START_FRESH: dict[str, str] = {
    "invoices": "invoices.schedules.start_fresh",
    "goals": "opportunity.tasks.settle_goal_milestones",
}

_NAMES_SHOWN = 5


def modules_payload(org, profile) -> dict:
    available = available_modules(org)
    enabled = enabled_modules(org)
    rows = []
    for m in ordered_modules(org):
        label = module_label(org, m.key)
        rows.append(
            {
                "key": m.key,
                "label": label,
                "group": m.group,
                "covers": m.covers,
                "stops": m.stops.format(label=label),
                "enabled": m.key in enabled,
                "available": m.key in available,
                "needs": m.needs,
            }
        )
    return {"modules": rows, "can_edit": may_configure(profile)}


def _key_list(data, field, errors):
    if field not in data:
        errors[field] = ["This field is required."]
        return None
    value = data[field]
    if not isinstance(value, list) or not all(isinstance(k, str) for k in value):
        errors[field] = ["Send a list of module keys."]
        return None
    unknown = [f'Unknown module: "{k}".' for k in value if k not in KEYS]
    if unknown:
        errors[field] = unknown
        return None
    return value


def _named(names):
    shown = ", ".join(f'"{n}"' for n in names[:_NAMES_SHOWN])
    extra = len(names) - _NAMES_SHOWN
    return f"{shown} and {extra} more" if extra > 0 else shown


def _blockers(row, key):
    """Why ``key`` cannot become unavailable now: what still receives input."""
    from cases.models import InboundMailbox
    from webforms.models import WebForm

    label = module_label(row, key)
    messages = []
    target = {v: k for k, v in WebForm.TARGET_MODULES.items()}.get(key)
    if target:
        forms = list(
            WebForm.objects.filter(org=row, is_published=True, target=target)
            .order_by("name")
            .values_list("name", flat=True)
        )
        if len(forms) == 1:
            messages.append(
                f"{label} still receives submissions from the published web form "
                f'"{forms[0]}". Unpublish it first, so no submission is lost.'
            )
        elif forms:
            messages.append(
                f"{label} still receives submissions from {len(forms)} published "
                f"web forms: {_named(forms)}. Unpublish them first, so no "
                "submission is lost."
            )
    # A mailbox counts against the module its mail becomes; the `creates`
    # values are the two module keys.
    if key in (InboundMailbox.CREATES_TICKETS, InboundMailbox.CREATES_LEADS):
        addresses = list(
            InboundMailbox.objects.filter(org=row, is_active=True, creates=key)
            .order_by("address")
            .values_list("address", flat=True)
        )
        if len(addresses) == 1:
            messages.append(
                f"{label} still receives email at {addresses[0]}. Switch that "
                "mailbox off first, so no email is lost."
            )
        elif addresses:
            shown = ", ".join(addresses[:_NAMES_SHOWN])
            extra = len(addresses) - _NAMES_SHOWN
            if extra > 0:
                shown += f" and {extra} more"
            messages.append(
                f"{label} still receives email at {len(addresses)} mailboxes: "
                f"{shown}. Switch them off first, so no email is lost."
            )
    return messages


def change_modules(org, actor, data, request=None) -> dict[str, list[str]]:
    """Validate and apply ``data``; ``{}`` once saved, else the errors, with
    nothing written."""
    from common.models import Org

    errors: dict[str, list[str]] = {}
    if not isinstance(data, dict):
        data = {}
    off = _key_list(data, "off", errors)
    order = _key_list(data, "order", errors)
    if order is not None and len(set(order)) != len(order):
        errors["order"] = ["Name each module once."]
    if errors:
        return errors

    with transaction.atomic():
        row = Org.objects.select_for_update().get(pk=org.pk)
        before = available_modules(row)
        was_off = set(KEYS) - enabled_modules(row)
        order_before = [m.key for m in ordered_modules(row)]
        row.modules_off = [k for k in KEYS if k in set(off)]
        row.module_order = list(order)
        after = available_modules(row)
        now_off = set(row.modules_off)

        problems = []
        for key in KEYS:
            needs = module(key).needs
            if key in was_off - now_off and needs in now_off:
                problems.append(
                    f"{module_label(row, key)} needs {module_label(row, needs)}. "
                    f"Switch {module_label(row, needs)} on first."
                )
        for key in KEYS:
            if key in before - after:
                problems += _blockers(row, key)
        if problems:
            errors["off"] = problems
            return errors

        now = timezone.now().isoformat()
        stamps = dict(row.modules_on_at) if isinstance(row.modules_on_at, dict) else {}
        for key in after - before:
            stamps[key] = now
        row.modules_on_at = stamps
        row.save(update_fields=["modules_off", "module_order", "modules_on_at"])

        for key in KEYS:
            if key in after - before and key in START_FRESH:
                import_string(START_FRESH[key])(row)

        switched_off = now_off - was_off
        switched_on = was_off - now_off
        reordered = [m.key for m in ordered_modules(row)] != order_before
        if switched_off or switched_on or reordered:
            audit_log.modules_changed(
                actor.user, row, switched_off, switched_on, reordered, request=request
            )
    return {}
