"""
Security Audit Logging for Multi-Tenancy

Tracks security-relevant events for compliance and incident investigation:
- Login attempts (success/failure)
- Org switches
- Permission denials
- API key usage
- Suspicious activities

Usage:
    from common.audit_log import audit_log

    audit_log.login_success(user, org, request)
    audit_log.org_switch(user, from_org, to_org, request)
    audit_log.permission_denied(user, org, action, resource, request)
"""

import logging

from django.core.cache import cache
from django.db import models

from common.base import BaseModel
from common.request_meta import client_ip, user_agent

logger = logging.getLogger("security.audit")

# Failed sign-ins are written by anonymous callers into a table with no org and
# no RLS policy, so each client IP gets at most this many rows per hour. The
# attempts past it are refused exactly as before; they just leave no row.
LOGIN_FAILURE_ROWS_PER_IP_PER_HOUR = 20
_LOGIN_FAILURE_WINDOW_SECONDS = 60 * 60
# The width of an email address (RFC 5321), and of `User.email`.
_LOGIN_FAILURE_EMAIL_MAX_LENGTH = 254


def _login_failure_row_allowed(request):
    """Count one failed sign-in against the caller's IP and say whether it
    still fits under `LOGIN_FAILURE_ROWS_PER_IP_PER_HOUR`.

    The window starts at the first failure and is not extended by later ones.
    Callers with no address we can vouch for share one bucket.

    With the cache down the cap cannot be counted, so the row is skipped
    rather than written uncapped: a flood during an outage must not reach
    the unscoped audit table, and the refused sign-in must still answer its
    own 400 rather than a 500. The warning names only the exception type.
    """
    ip = client_ip(request) if request is not None else None
    key = f"audit:login_failure:{ip or 'unknown'}"
    try:
        cache.add(key, 0, timeout=_LOGIN_FAILURE_WINDOW_SECONDS)
        try:
            count = cache.incr(key)
        except ValueError:
            # Evicted between add and incr: count this one as the first.
            cache.add(key, 1, timeout=_LOGIN_FAILURE_WINDOW_SECONDS)
            count = 1
    except Exception as exc:  # the backend's own error types vary (redis, memcached)
        logger.warning(
            "Login failure audit row skipped: cache unavailable (%s)",
            type(exc).__name__,
        )
        return False
    return count <= LOGIN_FAILURE_ROWS_PER_IP_PER_HOUR


_CALENDAR_FEED_DESCRIPTIONS = {
    "CALENDAR_FEED_ENABLED": "Calendar feed enabled",
    "CALENDAR_FEED_REGENERATED": "Calendar feed URL regenerated",
    "CALENDAR_FEED_DISABLED": "Calendar feed disabled",
}


class SecurityAuditLog(BaseModel):
    """
    Model to store security audit events.

    This is separate from Activity model which tracks business operations.
    SecurityAuditLog tracks authentication and authorization events.
    """

    EVENT_TYPES = (
        ("LOGIN_SUCCESS", "Login Success"),
        ("LOGIN_FAILURE", "Login Failure"),
        ("LOGOUT", "Logout"),
        ("ORG_SWITCH", "Organization Switch"),
        ("TOKEN_REFRESH", "Token Refresh"),
        ("TOKEN_REVOKED", "Token Revoked"),
        ("PERMISSION_DENIED", "Permission Denied"),
        ("CROSS_ORG_ATTEMPT", "Cross-Org Access Attempt"),
        ("API_KEY_USED", "API Key Used"),
        ("API_KEY_INVALID", "Invalid API Key"),
        ("MEMBERSHIP_REVOKED", "Membership Revoked"),
        ("SUSPICIOUS_ACTIVITY", "Suspicious Activity"),
        ("SAMPLE_DATA_CLEARED", "Vertical Pack Sample Data Cleared"),
        ("WEBHOOK_PAUSED", "Webhook Paused"),
        ("WEBHOOK_REENABLED", "Webhook Re-enabled"),
        ("WEBHOOK_CHANGED", "Webhook Destination Changed"),
        ("RECORD_MERGED", "Record Merged"),
        ("CALENDAR_FEED_ENABLED", "Calendar Feed Enabled"),
        ("CALENDAR_FEED_REGENERATED", "Calendar Feed Regenerated"),
        ("CALENDAR_FEED_DISABLED", "Calendar Feed Disabled"),
        ("API_TOKEN_CREATED", "API Token Created"),
        ("API_TOKEN_REVOKED", "API Token Revoked"),
    )

    event_type = models.CharField(max_length=50, choices=EVENT_TYPES, db_index=True)
    user = models.ForeignKey(
        "common.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="security_audit_logs",
    )
    org = models.ForeignKey(
        "common.Org",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="security_audit_logs",
    )

    # Event details
    description = models.TextField(blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)

    # Request information
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True, default="")
    request_path = models.CharField(max_length=500, blank=True, default="")
    request_method = models.CharField(max_length=10, blank=True, default="")

    # Outcome
    success = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Security Audit Log"
        verbose_name_plural = "Security Audit Logs"
        db_table = "security_audit_log"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["event_type", "-created_at"]),
            models.Index(fields=["user", "-created_at"]),
            models.Index(fields=["org", "-created_at"]),
            models.Index(fields=["ip_address"]),
            models.Index(fields=["success", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.event_type} - {self.user} - {self.created_at}"


class AuditLogger:
    """
    Helper class for logging security events.

    Logs to both database (SecurityAuditLog) and Python logger.
    """

    def _get_request_info(self, request):
        """Extract request information for logging."""
        if not request:
            return {}

        # Never the first X-Forwarded-For entry: the caller writes that, and
        # admins read this column in the audit log viewer as fact.
        return {
            "ip_address": client_ip(request),
            "user_agent": user_agent(request)[:500],
            "request_path": request.path[:500],
            "request_method": request.method,
        }

    def _log(
        self,
        event_type,
        user=None,
        org=None,
        description="",
        metadata=None,
        success=True,
        request=None,
    ):
        """
        Internal method to create audit log entry.
        """
        request_info = self._get_request_info(request)

        # Log to database
        try:
            SecurityAuditLog.objects.create(
                event_type=event_type,
                user=user,
                org=org,
                description=description,
                metadata=metadata or {},
                success=success,
                **request_info,
            )
        except Exception as e:
            logger.error("Failed to create audit log: %s", e)

        # Log to Python logger
        log_level = logging.INFO if success else logging.WARNING
        user_id = str(user.id) if user else "anonymous"
        org_id = str(org.id) if org else "none"

        logger.log(
            log_level,
            "%s | user_id=%s | org_id=%s | ip=%s | success=%s",
            event_type,
            user_id,
            org_id,
            request_info.get("ip_address"),
            success,
        )

    def login_success(self, user, org, request=None):
        """Log successful login."""
        self._log(
            "LOGIN_SUCCESS",
            user=user,
            org=org,
            description=f"User logged in to {org.name if org else 'platform'}",
            request=request,
        )

    def login_failure(self, email, reason, request=None):
        """Log a refused staff sign-in (see `common.views.auth_views`).

        ``reason`` is a short stable code. ``email`` is whatever the attempt
        claimed, or empty when it named none; it is stored truncated and never
        alongside the token or code that was tried. No org: the caller has
        none yet, so an org's audit viewer never lists these rows. Rate-capped
        per client IP by `_login_failure_row_allowed`.
        """
        if not _login_failure_row_allowed(request):
            return

        from common.models import User

        email = (email or "")[:_LOGIN_FAILURE_EMAIL_MAX_LENGTH]
        user = User.objects.filter(email=email).first() if email else None

        self._log(
            "LOGIN_FAILURE",
            user=user,
            description=f"Login failed for {email}: {reason}",
            metadata={"email": email, "reason": reason},
            success=False,
            request=request,
        )

    def logout(self, user, org, request=None):
        """Log user logout."""
        self._log(
            "LOGOUT",
            user=user,
            org=org,
            description="User logged out",
            request=request,
        )

    def org_switch(self, user, from_org, to_org, request=None):
        """Log organization switch."""
        self._log(
            "ORG_SWITCH",
            user=user,
            org=to_org,
            description=f"Switched from {from_org.name if from_org else 'none'} to {to_org.name}",
            metadata={
                "from_org_id": str(from_org.id) if from_org else None,
                "to_org_id": str(to_org.id),
            },
            request=request,
        )

    def token_refresh(self, user, org, request=None):
        """Log token refresh."""
        self._log(
            "TOKEN_REFRESH",
            user=user,
            org=org,
            description="Token refreshed",
            request=request,
        )

    def token_revoked(self, user, org, reason, request=None):
        """Log token revocation."""
        self._log(
            "TOKEN_REVOKED",
            user=user,
            org=org,
            description=f"Token revoked: {reason}",
            metadata={"reason": reason},
            request=request,
        )

    def permission_denied(self, user, org, action, resource, request=None):
        """Log permission denial."""
        self._log(
            "PERMISSION_DENIED",
            user=user,
            org=org,
            description=f"Permission denied for {action} on {resource}",
            metadata={"action": action, "resource": resource},
            success=False,
            request=request,
        )

    def cross_org_attempt(self, user, user_org, target_org, resource, request=None):
        """Log attempted cross-org data access."""
        self._log(
            "CROSS_ORG_ATTEMPT",
            user=user,
            org=user_org,
            description=f"Attempted to access {resource} in org {target_org.name if target_org else 'unknown'}",
            metadata={
                "user_org_id": str(user_org.id) if user_org else None,
                "target_org_id": str(target_org.id) if target_org else None,
                "resource": resource,
            },
            success=False,
            request=request,
        )

    def api_key_used(self, org, endpoint, request=None):
        """Log API key usage."""
        self._log(
            "API_KEY_USED",
            org=org,
            description=f"API key used for {endpoint}",
            metadata={"endpoint": endpoint},
            request=request,
        )

    def api_key_invalid(self, api_key_prefix, request=None):
        """Log invalid API key attempt."""
        self._log(
            "API_KEY_INVALID",
            description=f"Invalid API key attempted: {api_key_prefix}...",
            metadata={"api_key_prefix": api_key_prefix},
            success=False,
            request=request,
        )

    def membership_revoked(self, user, org, revoked_by=None, request=None):
        """Log when user's org membership is revoked."""
        self._log(
            "MEMBERSHIP_REVOKED",
            user=user,
            org=org,
            description=f"Membership revoked by {revoked_by.email if revoked_by else 'system'}",
            metadata={"revoked_by": str(revoked_by.id) if revoked_by else None},
            request=request,
        )

    def sample_data_cleared(self, user, org, deleted_count, request=None):
        """Log a vertical-pack sample-data clear.

        This is the only destructive operation in the vertical-packs
        feature; apply_pack only ever creates and records itself via
        PackApplication, but clear_sample_data deletes rows and, before
        this, recorded nothing. Any org admin can trigger it, so an audit
        trail of who cleared what, and how many rows, matters for incident
        review the same way the other destructive-adjacent events here do.
        """
        self._log(
            "SAMPLE_DATA_CLEARED",
            user=user,
            org=org,
            description=f"Cleared {deleted_count} vertical-pack sample lead(s)",
            metadata={"deleted_count": deleted_count},
            request=request,
        )

    def webhook_paused(self, endpoint, reason, creator=None, request=None):
        """Log a webhook paused because its creator lost the standing to own
        it (see `webhooks.ownership`). `user` is that creator, or None when
        their user row is being deleted; `creator_id` survives either way.

        The URL is left out on purpose: hook URLs often carry a secret.
        """
        self._log(
            "WEBHOOK_PAUSED",
            user=creator,
            org=endpoint.org,
            description=f"Webhook paused: {reason}",
            metadata={
                "endpoint_id": str(endpoint.id),
                "creator_id": (
                    str(endpoint.created_by_id) if endpoint.created_by_id else None
                ),
                "pause_reason": reason,
            },
            request=request,
        )

    def webhook_taken_over(
        self, user, endpoint, previous_creator_id, changed, request=None
    ):
        """Log an admin becoming the creator of a webhook, which happens when
        they turn it back on or change what it sends or where (`changed`
        names the fields, `is_active` for a re-enable). `previous_creator_id`
        is who answered for it before. See `webhooks.views`.
        """
        self._log(
            "WEBHOOK_REENABLED" if changed == ["is_active"] else "WEBHOOK_CHANGED",
            user=user,
            org=endpoint.org,
            description=f"Webhook taken over: {', '.join(changed)}",
            metadata={
                "endpoint_id": str(endpoint.id),
                "previous_creator_id": (
                    str(previous_creator_id) if previous_creator_id else None
                ),
                "changed": list(changed),
            },
            request=request,
        )

    def record_merged(self, user, org, entity, kept, merged, request=None):
        """Log one lead, contact or account merged into another (G19).

        ``merged`` is deleted by the time anyone reads this row, so both ids
        and both display names are kept here: this row is the only place the
        merged record's name survives.
        """
        self._log(
            "RECORD_MERGED",
            user=user,
            org=org,
            description=f"Merged {entity} {merged['name']} into {kept['name']}",
            metadata={
                "entity": entity,
                "kept_id": kept["id"],
                "kept_name": kept["name"],
                "merged_id": merged["id"],
                "merged_name": merged["name"],
            },
            request=request,
        )

    def calendar_feed(self, event_type, user, org, request=None):
        """Log a member enabling, regenerating or disabling their own task
        calendar feed (G14). ``event_type`` is one of the three
        ``CALENDAR_FEED_*`` events.

        The feed URL is a standing credential, so its lifecycle is recorded
        here. Nothing about the token is: not the URL, not the raw token, not
        its hash.
        """
        self._log(
            event_type,
            user=user,
            org=org,
            description=_CALENDAR_FEED_DESCRIPTIONS[event_type],
            request=request,
        )

    def api_token_created(self, user, pat, request=None):
        """Log a personal access token created by its owner."""
        self._api_token("API_TOKEN_CREATED", "created", user, pat, request)

    def api_token_revoked(self, user, pat, request=None):
        """Log a personal access token revoked, by its owner or by an admin
        from ``/api/org/tokens/``."""
        self._api_token("API_TOKEN_REVOKED", "revoked", user, pat, request)

    def _api_token(self, event_type, verb, user, pat, request):
        """``user`` is who acted; ``owner_id`` and ``owner_name`` say whose
        token it was. Only the display prefix the token list already shows is
        kept, never the raw token or its hash.
        """
        owner = pat.profile.user
        self._log(
            event_type,
            user=user,
            org=pat.org,
            description=f"API token {pat.token_prefix} {verb}",
            metadata={
                "token_id": str(pat.id),
                "token_prefix": pat.token_prefix,
                "token_name": pat.name,
                "scopes": list(pat.scopes or []),
                "owner_id": str(owner.id),
                "owner_name": owner.name or owner.email,
            },
            request=request,
        )

    def suspicious_activity(self, user, org, activity_type, details, request=None):
        """Log suspicious activity for security review."""
        self._log(
            "SUSPICIOUS_ACTIVITY",
            user=user,
            org=org,
            description=f"Suspicious activity: {activity_type}",
            metadata={"activity_type": activity_type, "details": details},
            success=False,
            request=request,
        )


# Global audit logger instance
audit_log = AuditLogger()
