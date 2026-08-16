"""
Audit-log signal handlers for the Cases app.

Replaces the generic Case CREATE/UPDATE/DELETE handlers in `common/signals.py`
with finer-grained Activity rows. See `docs/cases/COORDINATION_DECISIONS.md` D1.

Verbs emitted from here: CREATE, UPDATE, DELETE, ASSIGN, STATUS_CHANGED,
PRIORITY_CHANGED, COMMENT, LINKED_SOLUTION, UNLINKED_SOLUTION.

Other Tier 1+ verbs (ROUTED, ESCALATED, REOPENED, MERGED, ...) are emitted
explicitly by their owning views/tasks, not from signals.
"""

from __future__ import annotations

import json
import logging

from crum import get_current_user
from django.contrib.contenttypes.models import ContentType
from django.db.models.signals import (
    m2m_changed,
    post_delete,
    post_save,
    pre_save,
)
from django.dispatch import receiver
from django.utils import timezone

from cases.models import Case, ReopenPolicy, Solution, TimeEntry
from cases.workflow import resolve_sla_targets
from common.models import Activity, Comment

REOPEN_TRIGGER_STATUS = "Closed"
REOPEN_DEFAULTS = {
    "is_enabled": True,
    "reopen_window_days": 7,
    "reopen_to_status": "Pending",
    "notify_assigned": True,
}

logger = logging.getLogger(__name__)

METADATA_BYTE_CAP = 4096

# Field-level UPDATE rows: only emit for these. Status/priority have their own
# verbs; everything else is bundled into a single UPDATE row per save with
# metadata.changes describing the field diff.
_TRACKED_SCALAR_FIELDS = (
    "name",
    "case_type",
    "description",
    "closed_on",
    "account_id",
    "stage_id",
    "sla_first_response_hours",
    "sla_resolution_hours",
    "first_response_at",
    "resolved_at",
)


def _resolve_actor():
    """Return a Profile to record as Activity.user, or None for system actions."""
    user = get_current_user()
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    profile = getattr(user, "profile", None)
    return profile


def _jsonify(value):
    """Coerce common non-JSON types (UUID, datetime, date) to strings."""
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value) if value is not None else None


def _normalize_metadata(metadata):
    """Walk dicts/lists and coerce leaves so JSONField can store them."""
    if isinstance(metadata, dict):
        return {k: _normalize_metadata(v) for k, v in metadata.items()}
    if isinstance(metadata, (list, tuple)):
        return [_normalize_metadata(v) for v in metadata]
    return _jsonify(metadata)


def _truncate_metadata(metadata: dict) -> dict:
    """Enforce the 4 KB serialized cap; mark truncation if we had to clip."""
    metadata = _normalize_metadata(metadata)
    try:
        encoded = json.dumps(metadata)
    except (TypeError, ValueError):
        return {"_truncated": True}
    if len(encoded.encode("utf-8")) <= METADATA_BYTE_CAP:
        return metadata
    logger.warning(
        "Activity metadata truncated: %d bytes exceeds %d cap",
        len(encoded),
        METADATA_BYTE_CAP,
    )
    return {"_truncated": True}


def _create_activity(case, action, metadata=None, actor=None):
    """Single insertion point for all Case-scoped Activity rows."""
    metadata = _truncate_metadata(metadata or {})
    if actor is None:
        actor = _resolve_actor()
    Activity.objects.create(
        user=actor,
        action=action,
        entity_type="Case",
        entity_id=case.pk,
        entity_name=str(case)[:255],
        metadata=metadata,
        org_id=case.org_id,
    )


@receiver(pre_save, sender=Case)
def case_pre_save_capture_old(sender, instance, **kwargs):
    """Stash the pre-save row so post_save can diff it."""
    if not instance.pk:
        instance._audit_old = None
        return
    try:
        instance._audit_old = sender.objects.get(pk=instance.pk)
    except sender.DoesNotExist:
        instance._audit_old = None


@receiver(pre_save, sender=Case)
def case_pre_save_sla_targets(sender, instance, **kwargs):
    """Resolve SLA targets from the org's policy, and keep them honest when
    priority moves.

    On insert, a target left NULL is resolved from the org's active
    ``EscalationPolicy`` for that priority, falling back to the
    ``cases.workflow`` defaults. A value the caller supplied explicitly is left
    alone, so seeds and imports can pin a bespoke promise.

    On a priority change both targets are re-derived. Raising priority is how a
    lead says "this needs attention sooner", and a target frozen at insert made
    that a no-op for the deadline: a Low case bumped to Urgent kept its 72-hour
    resolution target. Re-deriving can put an old case immediately past its new
    deadline, which is correct rather than surprising, and the escalation
    scan's cooldown and count cap keep that from becoming a mail storm.

    Nothing a client set is lost here, because no client can set these:
    ``CaseCreateSerializer`` does not expose either field. That is also why
    there is no "was overridden" column to consult.

    Lives here rather than in ``Case.save()`` (where the old, broken version
    was) so the invariant holds regardless of which view writes the case.
    """
    old = getattr(instance, "_audit_old", None)

    if old is None:
        if (
            instance.sla_first_response_hours is not None
            and instance.sla_resolution_hours is not None
        ):
            return
        first, resolution = resolve_sla_targets(instance.org_id, instance.priority)
        if instance.sla_first_response_hours is None:
            instance.sla_first_response_hours = first
        if instance.sla_resolution_hours is None:
            instance.sla_resolution_hours = resolution
        return

    if old.priority == instance.priority:
        return

    (
        instance.sla_first_response_hours,
        instance.sla_resolution_hours,
    ) = resolve_sla_targets(instance.org_id, instance.priority)


PENDING_STATUS = "Pending"


@receiver(pre_save, sender=Case)
def case_pre_save_sla_pause(sender, instance, **kwargs):
    """Maintain SLA pause counters across status transitions.

    Entering ``Pending``: stamp ``sla_paused_at`` if not already set so the
    helper subtracts wait time from the deadline.

    Leaving ``Pending``: accumulate the elapsed wait into
    ``sla_paused_seconds`` and clear ``sla_paused_at``. Preserves the
    accumulator across N pause/resume cycles.
    """
    old = getattr(instance, "_audit_old", None)
    new_status = instance.status
    if old is None:
        # New row. If it starts as Pending, stamp so first response/resolution
        # deadlines pause from minute zero.
        if new_status == PENDING_STATUS and instance.sla_paused_at is None:
            instance.sla_paused_at = timezone.now()
        return

    old_status = old.status
    if old_status == new_status:
        return

    now = timezone.now()
    if new_status == PENDING_STATUS:
        # Resume → pause edge.
        if instance.sla_paused_at is None:
            instance.sla_paused_at = now
        return

    if old_status == PENDING_STATUS:
        # Pause → resume edge.
        paused_at = instance.sla_paused_at or old.sla_paused_at
        if paused_at is not None:
            elapsed = int((now - paused_at).total_seconds())
            if elapsed > 0:
                instance.sla_paused_seconds = (
                    instance.sla_paused_seconds or 0
                ) + elapsed
        instance.sla_paused_at = None


RESOLVED_STATUSES = ("Closed",)


@receiver(pre_save, sender=Case)
def case_pre_save_stamp_resolved_at(sender, instance, **kwargs):
    """Record *when* a case was resolved, which nothing did.

    `resolved_at` drives `is_sla_resolution_breached`, the MTTR dashboard and
    the resolution half of the escalation scan. The only code in the repo that
    ever wrote it was `parent_views.close_with_children`; a case closed the
    ordinary way (the API, the v1 UI, a bulk update, a kanban drag) kept
    `resolved_at = NULL` forever. Both seeded Closed cases show it.

    The consequences ran one way: a resolved case stayed permanently
    "resolution breached" because the property reads NULL as *not resolved
    yet*, and MTTR silently excluded every ticket the team actually closed.

    Stamped on the transition into Closed and cleared on the way back out, so
    a reopened case is not still carrying the timestamp of its last life.

    `closed_on` is cleared on the way out for the same reason, and for a second
    one: the serializer's close gate requires a closing date, and it accepts a
    date already on the record. A ticket reopened without clearing it would sit
    there as status=New holding last month's closing date, and the next close
    would satisfy the gate with it instead of a real one. The customer-reply
    reopen path in `_evaluate_reopen` has always cleared it; a status change
    made by hand did not.
    """
    old = getattr(instance, "_audit_old", None)
    was_resolved = old is not None and old.status in RESOLVED_STATUSES
    is_resolved = instance.status in RESOLVED_STATUSES

    if is_resolved and not was_resolved and instance.resolved_at is None:
        instance.resolved_at = timezone.now()
    elif was_resolved and not is_resolved:
        instance.resolved_at = None
        instance.closed_on = None


def _maybe_stamp_first_response(case, comment):
    """Stamp `first_response_at` on the first agent reply to a ticket.

    Nothing wrote this field either, not the API, not the v1 UI, not the
    inbound-email path, while four things read it: the breach property, the
    "first reply" column, the escalation scan and the FRT dashboard. So every
    case in every org was permanently first-response-breached, the escalation
    task re-fired on tickets that had been answered hours ago, and no ticket
    could ever be shown as having met its target. Confirmed live: replying to
    a ticket left the field NULL and `is_sla_first_response_breached` True.

    "First response" is the first *public* comment from somebody on our side.
    An internal note is not a reply to the customer, and a comment with no
    `commented_by` is the customer themselves. The same test
    `_evaluate_reopen` already uses to tell the two apart.
    """
    if case.first_response_at is not None:
        return
    if getattr(comment, "is_internal", False):
        return
    if comment.commented_by_id is None:
        return
    stamped = comment.created_at or timezone.now()
    case.first_response_at = stamped
    Case.objects.filter(pk=case.pk, first_response_at__isnull=True).update(
        first_response_at=stamped
    )


@receiver(post_save, sender=Case)
def case_post_save_emit_activity(sender, instance, created, **kwargs):
    if created:
        _create_activity(instance, "CREATE")
        _maybe_route(instance)
        return

    old = getattr(instance, "_audit_old", None)
    if old is None:
        return

    if old.is_active and not instance.is_active:
        _create_activity(instance, "DELETE")
        return

    if old.status != instance.status:
        _create_activity(
            instance,
            "STATUS_CHANGED",
            {"before": old.status, "after": instance.status},
        )
        _maybe_schedule_csat(instance, old.status)
        _notify_portal_of_status(instance)

    if old.priority != instance.priority:
        _create_activity(
            instance,
            "PRIORITY_CHANGED",
            {"before": old.priority, "after": instance.priority},
        )

    # Tier 3 parent/child: emit on any parent transition, regardless of the
    # entry point. The dedicated link/unlink endpoints set _parent_audit_skip
    # to avoid double-emitting (they record richer activity with the actor).
    if old.parent_id != instance.parent_id and not getattr(
        instance, "_parent_audit_skip", False
    ):
        if instance.parent_id is None:
            _create_activity(
                instance,
                "UNLINKED_PARENT",
                {"former_parent_id": str(old.parent_id)},
            )
        else:
            _create_activity(
                instance,
                "LINKED_PARENT",
                {"parent_id": str(instance.parent_id)},
            )

    changes = {}
    for field in _TRACKED_SCALAR_FIELDS:
        before = getattr(old, field)
        after = getattr(instance, field)
        if before != after:
            changes[field] = {"before": before, "after": after}
    if changes:
        _create_activity(instance, "UPDATE", {"changes": changes})


def _maybe_route(case):
    """Run the auto-routing engine. Failures must not break case creation."""
    if getattr(case, "_routing_skip", False):
        return
    try:
        from cases.routing import evaluate

        evaluate(case)
    except Exception:
        logger.exception("Auto-routing failed for case=%s", case.pk)


def _notify_portal_of_status(case):
    """Email the case's contacts that the status moved.

    Fires on every status change rather than only on Closed, because "your
    request is now Pending" is the update a waiting customer most wants and the
    one an agent is least likely to send by hand.
    """
    if not case.org_id:
        return
    try:
        from cases.tasks import notify_portal_contacts

        notify_portal_contacts.delay(str(case.id), str(case.org_id), "status")
    except Exception:  # pragma: no cover - defensive, matches _maybe_schedule_csat
        logger.exception("portal status notification failed for case %s", case.pk)


def _maybe_schedule_csat(case, old_status):
    """Enqueue the CSAT survey email when a case transitions to Closed.

    Uses a delayed Celery task so a near-immediate reopen short-circuits
    inside the task body (status re-check). The task itself is also
    idempotent, a second STATUS_CHANGED → Closed on the same case won't
    create a second survey because of the `OneToOneField` on Case.
    """
    if case.status != "Closed" or old_status == "Closed":
        return
    if not case.org_id or not case.org.csat_enabled:
        return
    try:
        from cases.tasks import CSAT_SEND_DELAY_MINUTES, send_csat_survey

        send_csat_survey.apply_async(
            args=[str(case.id), str(case.org_id)],
            countdown=CSAT_SEND_DELAY_MINUTES * 60,
        )
    except Exception:  # pragma: no cover. Broker outage shouldn't block close
        logger.exception("Failed to enqueue CSAT survey for case=%s", case.pk)


@receiver(post_delete, sender=Case)
def case_post_delete_emit_activity(sender, instance, **kwargs):
    """Hard-delete path. Soft-delete (is_active=False) is covered by post_save."""
    _create_activity(instance, "DELETE", {"hard_deleted": True})


@receiver(m2m_changed, sender=Case.assigned_to.through)
def case_assigned_to_changed(sender, instance, action, pk_set, **kwargs):
    if action not in ("post_add", "post_remove"):
        return
    if not pk_set:
        return
    ids = [str(pk) for pk in pk_set]
    metadata = {"added": ids} if action == "post_add" else {"removed": ids}
    _create_activity(instance, "ASSIGN", metadata)


@receiver(m2m_changed, sender=Solution.cases.through)
def solution_cases_changed(sender, instance, action, pk_set, reverse, **kwargs):
    """
    Solution↔Case M2M. Solution.cases.through has columns (solution_id, case_id).

    `reverse=False` → instance is a Solution, pk_set are Case ids.
    `reverse=True`  → instance is a Case,     pk_set are Solution ids.
    """
    if action not in ("post_add", "post_remove"):
        return
    if not pk_set:
        return

    verb = "LINKED_SOLUTION" if action == "post_add" else "UNLINKED_SOLUTION"

    if reverse:
        # instance is a Case, pk_set is solution ids
        for sol_id in pk_set:
            _create_activity(instance, verb, {"solution_id": str(sol_id)})
    else:
        # instance is a Solution, pk_set is case ids
        case_ids = list(pk_set)
        cases = Case.objects.filter(pk__in=case_ids)
        for case in cases:
            _create_activity(case, verb, {"solution_id": str(instance.pk)})


@receiver(pre_save, sender=Comment)
def comment_pre_save_capture_old(sender, instance, **kwargs):
    """Stash the old is_internal value so post_save can detect a flip."""
    if not instance.pk:
        instance._audit_old_is_internal = None
        return
    try:
        old = Comment.objects.only("is_internal").get(pk=instance.pk)
    except Comment.DoesNotExist:
        instance._audit_old_is_internal = None
        return
    instance._audit_old_is_internal = bool(old.is_internal)


def _resolve_reopen_policy(org_id):
    """Return a snapshot of the org's reopen policy, falling back to defaults."""
    policy = ReopenPolicy.objects.filter(org_id=org_id).first()
    if policy is None:
        return dict(REOPEN_DEFAULTS)
    return {
        "is_enabled": policy.is_enabled,
        "reopen_window_days": policy.reopen_window_days,
        "reopen_to_status": policy.reopen_to_status,
        "notify_assigned": policy.notify_assigned,
    }


def _evaluate_reopen(case, comment):
    """Check whether a new external comment should reopen the case.

    Returns:
        "reopened" if the case was reopened (and a REOPENED Activity emitted),
        "out_of_window" if it would have reopened but closed_on is too old,
        None otherwise (agent comment, internal note, non-Closed status, policy off).
    """
    if getattr(comment, "is_internal", False):
        return None
    if comment.commented_by_id is not None:
        return None
    if case.status != REOPEN_TRIGGER_STATUS:
        return None
    if not case.closed_on:
        return None

    policy = _resolve_reopen_policy(case.org_id)
    if not policy["is_enabled"]:
        return None

    today = timezone.localdate()
    days_since_close = (today - case.closed_on).days
    if days_since_close > policy["reopen_window_days"]:
        return "out_of_window"

    case.status = policy["reopen_to_status"]
    case.closed_on = None
    # `.update()` skips signals, so the resolved_at reset that
    # `case_pre_save_stamp_resolved_at` does on a normal save has to be
    # written out by hand here. A reopened ticket is not a resolved one.
    case.resolved_at = None
    Case.objects.filter(pk=case.pk).update(
        status=case.status, closed_on=None, resolved_at=None
    )
    _create_activity(
        case,
        "REOPENED",
        {
            "comment_id": str(comment.pk),
            "to_status": policy["reopen_to_status"],
            "days_since_close": days_since_close,
        },
    )
    if policy["notify_assigned"]:
        _notify_reopen_assignees(case)
    return "reopened"


def _notify_reopen_assignees(case):
    """Best-effort notification fan-out on reopen."""
    try:
        from cases.tasks import send_email_to_assigned_user
    except ImportError:
        return
    assignee_ids = list(case.assigned_to.values_list("id", flat=True))
    if not assignee_ids:
        return
    try:
        send_email_to_assigned_user.delay(assignee_ids, str(case.pk), str(case.org_id))
    except Exception:  # pragma: no cover - notification failures are non-blocking
        logger.warning(
            "send_email_to_assigned_user failed for reopen of case=%s", case.pk
        )


def maybe_reopen_for_inbound_email(case, email_message):
    """Reopen a Closed case when a threaded inbound email lands.

    Mirrors _evaluate_reopen but records the trigger as email_message_id.
    Returns "reopened", "out_of_window", or None.
    """
    if case.status != REOPEN_TRIGGER_STATUS:
        return None
    if not case.closed_on:
        return None

    policy = _resolve_reopen_policy(case.org_id)
    if not policy["is_enabled"]:
        return None

    today = timezone.localdate()
    days_since_close = (today - case.closed_on).days
    if days_since_close > policy["reopen_window_days"]:
        return "out_of_window"

    case.status = policy["reopen_to_status"]
    case.closed_on = None
    # `.update()` skips signals, so the resolved_at reset that
    # `case_pre_save_stamp_resolved_at` does on a normal save has to be
    # written out by hand here. A reopened ticket is not a resolved one.
    case.resolved_at = None
    Case.objects.filter(pk=case.pk).update(
        status=case.status, closed_on=None, resolved_at=None
    )
    _create_activity(
        case,
        "REOPENED",
        {
            "email_message_id": str(email_message.pk),
            "to_status": policy["reopen_to_status"],
            "days_since_close": days_since_close,
        },
    )
    if policy["notify_assigned"]:
        _notify_reopen_assignees(case)
    return "reopened"


def emit_email_received_activity(case, email_message):
    """Audit row for one inbound email landing on a Case (new or threaded)."""
    if case is None:
        return
    _create_activity(
        case,
        "EMAIL_RECEIVED",
        {
            "email_message_id": str(email_message.pk),
            "from_address": (email_message.from_address or "")[:128],
            "subject": (email_message.subject or "")[:128],
            "message_id": (email_message.message_id or "")[:255],
        },
    )


# ---------------------------------------------------------------------------
# Tier 3 time-tracking: emit a TIME_LOGGED Activity row when an entry's
# duration is finalized. We only fire when ended_at transitions from null →
# set (timer stopped), or on a freshly created entry that already has both
# timestamps (manual entry). Started-but-not-yet-stopped rows write nothing.


@receiver(pre_save, sender=TimeEntry)
def time_entry_pre_save_capture_ended_at(sender, instance, **kwargs):
    if not instance.pk:
        instance._audit_old_ended_at = None
        return
    try:
        old = TimeEntry.objects.only("ended_at").get(pk=instance.pk)
    except TimeEntry.DoesNotExist:
        instance._audit_old_ended_at = None
        return
    instance._audit_old_ended_at = old.ended_at


@receiver(post_save, sender=TimeEntry)
def time_entry_post_save_emit_activity(sender, instance, created, **kwargs):
    case = instance.case
    if case is None:
        return
    if created:
        if instance.ended_at is None:
            return  # Timer just started; nothing to log yet.
        _create_activity(
            case,
            "TIME_LOGGED",
            {
                "time_entry_id": str(instance.pk),
                "duration_minutes": instance.duration_minutes,
                "billable": bool(instance.billable),
            },
        )
        return

    old_ended_at = getattr(instance, "_audit_old_ended_at", None)
    if old_ended_at is None and instance.ended_at is not None:
        _create_activity(
            case,
            "TIME_LOGGED",
            {
                "time_entry_id": str(instance.pk),
                "duration_minutes": instance.duration_minutes,
                "billable": bool(instance.billable),
            },
        )


@receiver(post_save, sender=Comment)
def comment_post_save_emit_activity(sender, instance, created, **kwargs):
    """Emit COMMENT for new comments, plus auto-reopen on customer reply."""
    case_ct = ContentType.objects.get_for_model(Case)
    if instance.content_type_id != case_ct.id:
        return
    case = Case.objects.filter(pk=instance.object_id).first()
    if case is None:
        return
    actor = instance.commented_by if instance.commented_by_id else None

    if created:
        metadata = {
            "comment_id": str(instance.pk),
            "is_internal": bool(getattr(instance, "is_internal", False)),
        }
        reopen_outcome = _evaluate_reopen(case, instance)
        if reopen_outcome == "out_of_window":
            metadata["out_of_reopen_window"] = True
        _maybe_stamp_first_response(case, instance)
        _create_activity(case, "COMMENT", metadata, actor=actor)
        # Tier 2: fan out to watchers + mentions. Local import avoids a
        # circular import (cases.notifications -> cases.models -> signals).
        from cases.notifications import dispatch_for_comment

        try:
            dispatch_for_comment(instance, case, actor=actor)
        except Exception:  # pragma: no cover - defensive
            logger.exception("notification dispatch failed for comment %s", instance.pk)

        # Customer portal: a public reply is the thing the customer is waiting
        # for. An internal note must never trigger this, which is the whole
        # reason is_internal exists, and a customer's own reply must not email
        # the customer back.
        if not instance.is_internal:
            from cases.tasks import notify_portal_contacts

            try:
                notify_portal_contacts.delay(
                    str(case.id),
                    str(case.org_id),
                    "reply",
                    actor_contact_id=(
                        str(instance.commented_by_contact_id)
                        if instance.commented_by_contact_id
                        else None
                    ),
                )
            except Exception:  # pragma: no cover - defensive
                logger.exception(
                    "portal reply notification failed for comment %s", instance.pk
                )
        return

    old_is_internal = getattr(instance, "_audit_old_is_internal", None)
    new_is_internal = bool(getattr(instance, "is_internal", False))
    if old_is_internal is not None and old_is_internal != new_is_internal:
        metadata = {
            "comment_id": str(instance.pk),
            "visibility_changed": True,
            "before": old_is_internal,
            "after": new_is_internal,
        }
        _create_activity(case, "COMMENT", metadata, actor=actor)
