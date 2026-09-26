import hashlib
import logging
import re
from datetime import timedelta
from email.utils import make_msgid, parseaddr

from celery import shared_task
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.mail import EmailMessage
from django.core.mail.utils import DNS_NAME
from django.core.signing import TimestampSigner
from django.db.models import Q
from django.template.loader import render_to_string
from django.utils import timezone

from cases.inbound.threading import _SUBJECT_FALLBACK_RE as _SUBJECT_TAG_RE
from cases.inbound.threading import short_case_id
from cases.models import Case, CsatSurvey, EscalationPolicy, InboundMailbox, TimeEntry
from cases.models import EmailMessage as EmailMessageRecord
from cases.notifications import case_link
from cases.workflow import TERMINAL_STATUSES
from common.links import frontend_url
from common.models import Activity, Comment, Org, Profile
from common.tasks import clear_rls_context, set_rls_context

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
    case = Case.objects.get(id=case_id, org_id=org_id)
    created_by = case.created_by
    for profile_id in recipients:
        recipients_list = []
        profile = Profile.objects.filter(
            id=profile_id, org_id=org_id, is_active=True
        ).first()
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

    Walks every org through the unscoped `organization` table and sets the RLS
    context before any org-scoped query; `_scan_org` returns early for an org
    with no active policy. The previous version found its orgs by querying
    `escalation_policy` first, with no context set. That table is org-scoped
    and a worker runs no middleware, so under an RLS-bound role the lookup
    matched nothing and no case was ever escalated. The context is cleared in
    a ``finally`` so the last org's id is not left on a pooled connection.
    """
    total = 0
    try:
        for org in Org.objects.all():
            set_rls_context(org.id)
            try:
                total += _scan_org(org)
            except Exception:  # pragma: no cover
                logger.exception("Escalation scan failed for org=%s", org.id)
    finally:
        clear_rls_context()
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
    if _unanswered_web_form_ticket(case):
        logger.info(
            "send_csat_survey: case=%s is an unanswered web form ticket", case_id
        )
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


# Threading for the customer-facing case emails below. A reply the customer
# sends back carries our Message-ID in In-Reply-To, which the inbound pipeline
# matches against `EmailMessage.message_id`; the subject tag catches the mail
# clients and relays that strip those headers.
#
# References keeps the thread's root plus this many of its latest ids, so a
# long ticket does not grow the header without limit.
THREAD_REFERENCES_TAIL = 10
# Printable ASCII with no whitespace and no angle brackets. Stored ids come
# from inbound mail, which the sender controls, so an id that does not fit is
# left out of our headers rather than trusted to be well formed.
_SAFE_MESSAGE_ID_RE = re.compile(r"^[!-;=?-~]{1,512}$")


def _safe_ids(ids):
    """Drop unusable ids and repeats, keeping the order."""
    seen = set()
    out = []
    for value in ids:
        if value and _SAFE_MESSAGE_ID_RE.match(value) and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def _thread_headers(case):
    """Return (in_reply_to, references) for the next email on `case`.

    Ordered by our own clock (`created_at`) rather than `received_at`, which
    for inbound mail is the sender's Date header.
    """
    rows = EmailMessageRecord.objects.filter(org_id=case.org_id, case=case)
    latest = list(
        rows.order_by("-created_at").values_list("message_id", flat=True)[
            :THREAD_REFERENCES_TAIL
        ]
    )
    root = case.external_thread_id or (
        rows.order_by("created_at").values_list("message_id", flat=True).first()
    )
    references = _safe_ids([root, *reversed(latest)])
    in_reply_to = _safe_ids([*latest[:1], case.external_thread_id])
    return (in_reply_to[0] if in_reply_to else ""), references


def _thread_subject(case):
    """`Re: <name> [Case #xxxxxxxx]`, with CR/LF and any older tag removed.

    Any `[Case #...]` already in the name (a case born from a reply subject
    carries one) is stripped, so the tag appears once and it is this case's.
    Collapsing whitespace is what keeps a user-typed newline in the name out
    of the header block: Django would refuse the whole message otherwise.
    """
    name = _SUBJECT_TAG_RE.sub(" ", case.name or "")
    return " ".join(f"Re: {name} [Case #{short_case_id(case)}]".split())


def _reply_to_mailbox(case):
    """The inbound mailbox a customer's email reply should go to, or None.

    The one the case's latest inbound email came through; else the org's only
    mailbox. Only a mailbox that can take mail counts: active, and an SES one
    with its topic pinned, since the webhook refuses everything else and a
    reply sent there would be lost. Always this case's org.
    """
    receiving = InboundMailbox.objects.filter(
        org_id=case.org_id, is_active=True, provider="ses"
    ).exclude(topic_arn="")
    last_mailbox_id = (
        EmailMessageRecord.objects.filter(
            org_id=case.org_id,
            case=case,
            direction="inbound",
            mailbox__isnull=False,
        )
        .order_by("-created_at")
        .values_list("mailbox_id", flat=True)
        .first()
    )
    if last_mailbox_id:
        mailbox = receiving.filter(id=last_mailbox_id).first()
        if mailbox is not None:
            return mailbox
    only = list(receiving[:2])
    return only[0] if len(only) == 1 else None


def _unanswered_web_form_ticket(case):
    """True for a ticket a public web form created that no agent has answered.

    Anybody can post a ticket form with any address, and the service attaches
    a contact for it, so until somebody on our side engages, the address is a
    stranger's claim. Mailing it status changes and a CSAT survey would let
    that stranger make the org's sender write to anyone, under a subject the
    stranger chose. An agent's first public reply is the deliberate decision
    to engage, and it is what stamps `first_response_at`
    (`cases.signals._maybe_stamp_first_response`), so an answered ticket costs
    no query here. The reply itself is still emailed, since the agent chose to
    send it.
    """
    if case.first_response_at is not None:
        return False
    return case.webform_submissions.filter(org_id=case.org_id).exists()


def _ses_message_id(ses_id):
    """The Message-ID header SES puts on a message it accepted as `ses_id`.

    SES replaces any Message-ID we supply (AWS "Amazon SES header fields"), and
    django-ses reports only the bare id, in `extra_headers["message_id"]`. The
    header SES writes is `<id@email.amazonses.com>` in us-east-1 and
    `<id@<region>.amazonses.com>` in every other region, and that full form is
    what the customer's reply quotes in In-Reply-To. django-ses sends through
    `AWS_SES_REGION_NAME`, defaulting to us-east-1.
    """
    region = getattr(settings, "AWS_SES_REGION_NAME", "") or "us-east-1"
    host = "email" if region == "us-east-1" else region
    return f"{ses_id}@{host}.amazonses.com"


def _claim_thread_root(case, msgid_domain):
    """The case's thread root id, minted from our own domain if it has none.

    A case opened in the portal, by an agent or from a web form has no root,
    so its first email's References was empty. When SES then rewrote the
    Message-ID, a reply matched nothing by header. The root is our own id, set
    once and carried in References of every email on the case, and
    `find_existing_case` matches it on `external_thread_id`, so a reply threads
    by header whatever the provider did to the Message-ID. A concurrent task
    that set it first wins, and its value is used.
    """
    root = make_msgid(domain=msgid_domain).strip("<>")
    Case.objects.filter(
        Q(external_thread_id__isnull=True) | Q(external_thread_id=""),
        pk=case.pk,
        org_id=case.org_id,
    ).update(external_thread_id=root)
    return (
        Case.objects.filter(pk=case.pk, org_id=case.org_id)
        .values_list("external_thread_id", flat=True)
        .first()
    )


def _author_name(comment, org_name):
    if comment.commented_by_id:
        return comment.commented_by.user.name or f"{org_name} support"
    contact = comment.commented_by_contact
    if contact is not None:
        return f"{contact.first_name} {contact.last_name}".strip() or "A contact"
    return f"{org_name} support"


@shared_task
def notify_portal_contacts(
    case_id, org_id, kind, actor_contact_id=None, comment_id=None
):
    """Email the customer that something happened on their case.

    `kind` is "reply" or "status". Unlike `_select_primary_contact`, which
    deliberately picks a single recipient for CSAT, this mails every contact on
    the case that has an address, because any of them may be the one waiting.
    `actor_contact_id` is excluded, so nobody is emailed about their own reply.

    A reply email carries the reply itself (`comment_id`), escaped by the
    template. The comment is re-read here and must still be public, so a reply
    turned into an internal note before this ran is never sent.

    A status email is not sent on a web form ticket no agent has answered yet
    (`_unanswered_web_form_ticket`); a reply email always is.

    Every email is threaded: a fresh Message-ID, In-Reply-To and References
    from the case's known messages plus its thread root (`_claim_thread_root`),
    and a `[Case #xxxxxxxx]` subject tag. Each one sent is recorded as an
    outbound `EmailMessage` under the Message-ID the customer actually
    receives (`_ses_message_id` when SES replaced ours), which is what lets
    the customer's email reply land back on this case.

    The portal link requires signing in and carries no token, so forwarding the
    email does not forward access. That is the difference between this and the
    invoice and estimate mails, where the token in the URL is the credential.
    """
    set_rls_context(org_id)
    case = Case.objects.filter(id=case_id, org_id=org_id).select_related("org").first()
    if case is None:
        logger.info("notify_portal_contacts: case=%s not found, skipping", case_id)
        return None

    comment = None
    if kind == "reply":
        comment = (
            Comment.objects.filter(
                id=comment_id,
                org_id=org_id,
                content_type=ContentType.objects.get_for_model(Case),
                object_id=case.id,
                is_internal=False,
            )
            .select_related("commented_by__user", "commented_by_contact")
            .first()
        )
        if comment is None:
            logger.info(
                "notify_portal_contacts: no public comment=%s on case=%s, skipping",
                comment_id,
                case_id,
            )
            return 0
    elif _unanswered_web_form_ticket(case):
        logger.info(
            "notify_portal_contacts: case=%s is an unanswered web form ticket, "
            "skipping status email",
            case_id,
        )
        return 0

    recipients = (
        case.contacts.filter(is_active=True)
        .exclude(email__isnull=True)
        .exclude(email="")
    )
    if actor_contact_id:
        recipients = recipients.exclude(id=actor_contact_id)
    recipients = list(recipients)
    if not recipients:
        return 0

    # The org rides in the query string because the recipient may have no
    # portal cookie on the device they read this on: a phone, a colleague's
    # machine, a browser they cleared. Without it the sign-in fallback has no
    # idea which tenant's portal to send them to and drops them on the internal
    # staff login, which a customer cannot use. It is an id that already
    # appears in the URL of every portal page, not a credential, and it grants
    # nothing on its own.
    link = frontend_url(f"/portal/cases/{case.id}?org={case.org_id}")
    org_name = case.org.name or ""
    subject = _thread_subject(case)
    from_address = parseaddr(settings.DEFAULT_FROM_EMAIL)[1]
    msgid_domain = from_address.rpartition("@")[2] or str(DNS_NAME)
    in_reply_to, references = _thread_headers(case)
    if not case.external_thread_id:
        references = _safe_ids([_claim_thread_root(case, msgid_domain), *references])
    mailbox = _reply_to_mailbox(case)
    if comment is not None:
        author_name = _author_name(comment, org_name)
        body_text = comment.comment
    else:
        author_name = ""
        body_text = f"Your request is now {case.status}."

    headers = {}
    if in_reply_to:
        headers["In-Reply-To"] = f"<{in_reply_to}>"
    if references:
        headers["References"] = " ".join(f"<{ref}>" for ref in references)

    sent = 0
    for contact in recipients:
        html = render_to_string(
            "portal/case_update_email.html",
            {
                "contact_name": contact.first_name or "",
                "case_name": case.name,
                "case_status": case.status,
                "kind": kind,
                "author_name": author_name,
                "reply_text": body_text,
                "reply_by_email": mailbox is not None,
                "link": link,
                "org_name": org_name,
            },
        )
        message_id = make_msgid(domain=msgid_domain)
        msg = EmailMessage(
            subject,
            html,
            to=[contact.email],
            reply_to=[mailbox.address] if mailbox is not None else None,
            headers={**headers, "Message-ID": message_id},
        )
        msg.content_subtype = "html"
        try:
            msg.send(fail_silently=False)
        except Exception as exc:
            # One bad address must not stop the rest of the thread being told.
            # Only the exception type is logged: an SMTP or SES error message
            # usually quotes the recipient's address.
            logger.error(
                "notify_portal_contacts: send failed for case=%s contact=%s (%s)",
                case_id,
                contact.id,
                type(exc).__name__,
            )
            continue
        sent += 1
        ses_id = msg.extra_headers.get("message_id")
        EmailMessageRecord.objects.create(
            org_id=case.org_id,
            case=case,
            mailbox=mailbox,
            direction="outbound",
            message_id=(_ses_message_id(ses_id) if ses_id else message_id.strip("<>")),
            in_reply_to=in_reply_to,
            references=" ".join(references),
            from_address=from_address,
            to_addresses=contact.email,
            subject=subject[:512],
            body_text=body_text,
            body_html=html,
            received_at=timezone.now(),
        )
    return sent


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

    Returns the number of timers stopped. Walks every org through the
    unscoped `organization` table and sets the RLS context before touching
    `time_entry`. The previous version collected its org ids from `time_entry`
    itself before any context was set, which under an RLS-bound role matched
    nothing, so no timer was ever stopped.
    """
    now = timezone.now()
    cutoff = now - timedelta(hours=threshold_hours)

    stopped = 0
    try:
        for org_id in Org.objects.values_list("id", flat=True):
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
    finally:
        clear_rls_context()
    return stopped
