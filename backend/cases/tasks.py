import hashlib
import logging
from datetime import timedelta

from celery import shared_task
from django.core.mail import EmailMessage
from django.core.signing import TimestampSigner
from django.db.models import Q
from django.template.loader import render_to_string
from django.utils import timezone

from cases.models import Case, CsatSurvey, EscalationPolicy, TimeEntry
from cases.notifications import case_link
from cases.workflow import TERMINAL_STATUSES
from common.links import frontend_url
from common.models import Activity, Org, Profile
from common.tasks import set_rls_context

logger = logging.getLogger(__name__)

# Surveys live for 30 days from send before the link 410s.
CSAT_TOKEN_TTL_DAYS = 30
# Wait this long after a case closes before sending the survey, to avoid
# spamming customers when an agent flips status to Closed and then
# immediately reopens (Tier 1 reopen).
CSAT_SEND_DELAY_MINUTES = 30
# Salt scoping the TimestampSigner so a leak doesn't help forge tokens
# elsewhere in the codebase.
CSAT_SIGNER_SALT = "cases.csat_survey"
# The rating scale, in one place because two things read it: the survey email
# renders a star per value, and csat_views.CsatPublicView validates the POST
# against the same bounds. A scale that disagreed with the validator would put
# a star in the email that the API rejects on arrival.
CSAT_RATING_MIN = 1
CSAT_RATING_MAX = 5
# The ends of the scale, labelled. Mirrors SCALE_ENDS in the survey page so the
# email and the page it links to describe the same 1 and the same 5.
CSAT_SCALE_LOW_LABEL = "Not good"
CSAT_SCALE_HIGH_LABEL = "Great"

# Cap a single case at 3 escalations total. Past that, a human needs to step in.
ESCALATION_COUNT_CAP = 3
# Minimum gap between escalation attempts on the same case (prevents storming).
ESCALATION_COOLDOWN_MINUTES = 60


@shared_task
def send_email_to_assigned_user(recipients, case_id, org_id):
    """Send Mail To Users When they are assigned to a case"""
    set_rls_context(org_id)
    case = Case.objects.get(id=case_id)
    created_by = case.created_by
    for profile_id in recipients:
        recipients_list = []
        profile = Profile.objects.filter(id=profile_id, is_active=True).first()
        if profile:
            recipients_list.append(profile.user.email)
            context = {}
            context["url"] = frontend_url(case_link(case.id))
            context["user"] = profile.user
            context["case"] = case
            context["created_by"] = created_by
            subject = "Assigned to case."
            html_content = render_to_string(
                "assigned_to/cases_assigned.html", context=context
            )

            msg = EmailMessage(subject, html_content, to=recipients_list)
            msg.content_subtype = "html"
            msg.send()


def _dispatch_breach(case, action, target_profile, team, org_id):
    """Apply one breach action (notify / reassign / notify_and_reassign).

    Returns the list of profile_ids that received an email so callers can record
    them in the Activity metadata. Reassignment replaces the assignee set.
    """
    notified_ids = []
    if action in ("reassign", "notify_and_reassign") and target_profile is not None:
        case.assigned_to.set([target_profile])
    if action in ("notify", "notify_and_reassign"):
        recipients = []
        if target_profile is not None:
            recipients.append(str(target_profile.id))
        if team is not None:
            recipients.extend(
                str(pid)
                for pid in team.users.filter(is_active=True).values_list(
                    "id", flat=True
                )
            )
        # de-dupe while preserving order
        seen = set()
        recipients = [r for r in recipients if not (r in seen or seen.add(r))]
        if recipients:
            try:
                send_email_to_assigned_user.delay(recipients, str(case.id), str(org_id))
            except (
                Exception
            ):  # pragma: no cover. Broker glitches shouldn't lose the escalation
                logger.exception(
                    "Failed to enqueue escalation email for case=%s", case.pk
                )
            notified_ids = recipients
    return notified_ids


def _scan_org(org):
    """Run the breach scan for one org. Returns count of cases escalated."""
    now = timezone.now()
    cooldown_cutoff = now - timezone.timedelta(minutes=ESCALATION_COOLDOWN_MINUTES)

    candidate_qs = (
        Case.objects.filter(org=org, is_active=True)
        .exclude(status__in=TERMINAL_STATUSES)
        .filter(escalation_count__lt=ESCALATION_COUNT_CAP)
        .filter(
            Q(last_escalation_fired_at__isnull=True)
            | Q(last_escalation_fired_at__lt=cooldown_cutoff)
        )
    )

    fired = 0
    policies = {
        p.priority: p for p in EscalationPolicy.objects.filter(org=org, is_active=True)
    }
    if not policies:
        return 0

    for case in candidate_qs.iterator():
        first_breach = case.is_sla_first_response_breached
        resolution_breach = case.is_sla_resolution_breached
        if not (first_breach or resolution_breach):
            continue

        policy = policies.get(case.priority)
        if policy is None:
            continue

        breaches_metadata = []
        if first_breach and policy.first_response_target_id is not None:
            notified = _dispatch_breach(
                case,
                policy.first_response_action,
                policy.first_response_target,
                policy.notify_team,
                org.id,
            )
            breaches_metadata.append(
                {
                    "breach_type": "first_response",
                    "action": policy.first_response_action,
                    "target_profile_id": str(policy.first_response_target_id),
                    "notified_profile_ids": notified,
                }
            )
        if resolution_breach and policy.resolution_target_id is not None:
            notified = _dispatch_breach(
                case,
                policy.resolution_action,
                policy.resolution_target,
                policy.notify_team,
                org.id,
            )
            breaches_metadata.append(
                {
                    "breach_type": "resolution",
                    "action": policy.resolution_action,
                    "target_profile_id": str(policy.resolution_target_id),
                    "notified_profile_ids": notified,
                }
            )

        if not breaches_metadata:
            continue

        case.escalation_count = (case.escalation_count or 0) + 1
        case.last_escalation_fired_at = now
        case.save(
            update_fields=["escalation_count", "last_escalation_fired_at", "updated_at"]
        )
        Activity.objects.create(
            user=None,
            action="ESCALATED",
            entity_type="Case",
            entity_id=case.pk,
            entity_name=str(case)[:255],
            metadata={
                "breaches": breaches_metadata,
                "policy_id": str(policy.id),
                "escalation_count": case.escalation_count,
            },
            org_id=org.id,
        )
        fired += 1

    return fired


@shared_task
def scan_for_breached_cases():
    """Periodic scanner that fires escalation actions on SLA-breached cases.

    Runs once every 5 minutes via Celery beat. For each org with at least one
    active EscalationPolicy, checks non-terminal cases past either SLA deadline
    that haven't escalated within the last hour and whose escalation_count is
    below the cap, then dispatches the configured action(s) and records a single
    Activity(action='ESCALATED').
    """
    total = 0
    org_ids = (
        EscalationPolicy.objects.filter(is_active=True)
        .values_list("org_id", flat=True)
        .distinct()
    )
    for org in Org.objects.filter(id__in=list(org_ids)):
        set_rls_context(org.id)
        try:
            total += _scan_org(org)
        except Exception:  # pragma: no cover
            logger.exception("Escalation scan failed for org=%s", org.id)
    # Reset RLS context so the worker doesn't leak the last org's context to
    # whatever task runs next on the same connection. set_rls_context() no-ops
    # on falsy values, so clear the session variable directly.
    from django.db import connection

    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute("SELECT set_config('app.current_org', '', false)")
    return total


# ---------------------------------------------------------------------------
# CSAT (Tier 2 csat)


def csat_signer() -> TimestampSigner:
    """Salted TimestampSigner shared by send + verify paths."""
    return TimestampSigner(salt=CSAT_SIGNER_SALT)


def hash_csat_token(token: str) -> str:
    """SHA-256 hex digest. We never store raw tokens, only their hash."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _select_primary_contact(case: Case):
    """Pick the contact we'll mail. Prefer one with a non-blank email.

    Cases with multiple contacts get a single survey to the first
    email-bearing one (FK iteration order). Spec: each closed case is one
    survey; do not bundle, do not split.
    """
    return (
        case.contacts.exclude(email__isnull=True)
        .exclude(email="")
        .order_by("created_at")
        .first()
    )


@shared_task
def send_csat_survey(case_id, org_id):
    """Send a CSAT survey for a freshly-closed case.

    Skips when:
      - The org has flipped `csat_enabled` off.
      - The case has no contact with an email (logged, not raised).
      - The case has been reopened in the meantime (status no longer
        Closed): the spec's reopen-protection clause.
      - A survey row already exists for this case (don't double-send).
    """
    set_rls_context(org_id)
    case = Case.objects.filter(id=case_id, org_id=org_id).first()
    if case is None:
        logger.info("send_csat_survey: case=%s not found, skipping", case_id)
        return None
    if case.status != "Closed":
        logger.info(
            "send_csat_survey: case=%s status=%s, likely reopened, skipping",
            case_id,
            case.status,
        )
        return None
    if not case.org.csat_enabled:
        logger.info("send_csat_survey: org=%s has csat disabled, skipping", org_id)
        return None
    if hasattr(case, "csat_survey"):
        logger.info("send_csat_survey: case=%s already has a survey row", case_id)
        return None

    contact = _select_primary_contact(case)
    if contact is None or not contact.email:
        logger.info("send_csat_survey: case=%s has no contact email, skipping", case_id)
        return None

    now = timezone.now()
    raw_token = csat_signer().sign(str(case.id))
    survey = CsatSurvey.objects.create(
        org_id=org_id,
        case=case,
        contact=contact,
        token_hash=hash_csat_token(raw_token),
        sent_at=now,
        expires_at=now + timedelta(days=CSAT_TOKEN_TTL_DAYS),
    )

    # Register the unscoped token→org lookup so the anonymous survey view can
    # resolve the org under RLS. hash_csat_token is sha256, matching the key
    # portal_token_hash computes from the same URL token.
    from common.portal_tokens import register_portal_token_hash

    register_portal_token_hash(survey.token_hash, org_id, "csat", survey.id)

    link = frontend_url(f"/csat/{raw_token}")
    context = {
        "case": case,
        "contact": contact,
        "org": case.org,
        "link": link,
        # Each value becomes a star linking to `{link}?rating=<value>`, which
        # only pre-selects on the page. Nothing is recorded until that page
        # POSTs, because a link is a GET and mail scanners follow GETs.
        "rating_scale": range(CSAT_RATING_MIN, CSAT_RATING_MAX + 1),
        "scale_low_label": CSAT_SCALE_LOW_LABEL,
        "scale_high_label": CSAT_SCALE_HIGH_LABEL,
    }
    # Deliberately unguarded. This render used to sit under a bare
    # `except Exception` that fell back to a plain link, with a comment saying
    # the template existed in production. It never existed anywhere, so every
    # survey ever sent took the fallback and nobody found out. A template that
    # fails to render is now a task failure that Celery logs.
    html = render_to_string("csat/survey_email.html", context=context)

    msg = EmailMessage(
        subject=f"How did we do?, {case.name}",
        body=html,
        to=[contact.email],
    )
    msg.content_subtype = "html"
    try:
        msg.send(fail_silently=False)
    except Exception:
        # The survey row is already written; a retry strategy can pick it
        # up by token_hash. We don't tear down the row because the agent
        # CAN re-send manually if needed.
        logger.exception(
            "send_csat_survey: email send failed for case=%s contact=%s",
            case_id,
            contact.id,
        )
    return str(survey.id)


# ---------------------------------------------------------------------------
# Tier 3 time-tracking: auto-stop forgotten timers.

# A running timer this old gets killed by the Celery beat. Hand-tuned: 12h
# covers an overnight forgotten timer without clobbering a mid-day session
# someone left running through lunch.
TIME_ENTRY_AUTO_STOP_HOURS = 12


@shared_task
def auto_stop_stale_timers(threshold_hours=TIME_ENTRY_AUTO_STOP_HOURS):
    """Stop running TimeEntry rows whose started_at is older than
    ``threshold_hours``. Sets ``auto_stopped=True`` and recomputes
    ``duration_minutes`` via the model save() path.

    Returns the number of timers stopped. Iterates per-org so RLS context
    is set correctly when running in production with row-level security.
    """
    now = timezone.now()
    cutoff = now - timedelta(hours=threshold_hours)

    org_ids = list(
        TimeEntry.objects.filter(ended_at__isnull=True, started_at__lt=cutoff)
        .values_list("org_id", flat=True)
        .distinct()
    )
    stopped = 0
    for org_id in org_ids:
        set_rls_context(org_id)
        try:
            stale = list(
                TimeEntry.objects.filter(
                    org_id=org_id, ended_at__isnull=True, started_at__lt=cutoff
                )
            )
            for entry in stale:
                entry.ended_at = now
                entry.auto_stopped = True
                entry.save()
                stopped += 1
        except Exception:  # pragma: no cover
            logger.exception("auto_stop_stale_timers failed for org=%s", org_id)
    # Reset RLS context (same idiom as scan_for_breached_cases).
    from django.db import connection

    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute("SELECT set_config('app.current_org', '', false)")
    return stopped
