"""
Database middleware for Row-Level Security (RLS) context.

This middleware sets the PostgreSQL session variable `app.current_org`
which is used by RLS policies to filter data at the database level.

Enable RLS policies after this middleware is in place:

    ALTER TABLE leads ENABLE ROW LEVEL SECURITY;
    CREATE POLICY org_isolation ON leads
      USING (org_id = current_setting('app.current_org', true)::uuid);

Usage in settings.py:
    MIDDLEWARE = [
        ...
        'common.middleware.get_company.GetProfileAndOrg',
        'common.middleware.rls_context.SetOrgContext',  # After GetProfileAndOrg
        ...
    ]
"""

import logging

from django.db import connection
from django.http import JsonResponse

logger = logging.getLogger(__name__)


class SetOrgContext:
    """
    Middleware to set PostgreSQL session variable for Row-Level Security.

    This sets `app.current_org` to the user's organization ID, which is
    used by RLS policies to automatically filter data at the database level.

    Security: This provides defense-in-depth. Even if application code
    forgets to filter by org, the database will enforce isolation.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Set org context before processing request
        self._set_org_context(request)

        # The reset MUST run even when the view raises. `app.current_org` is
        # set at SESSION scope, so it outlives the statement and the
        # transaction; it is cleared only because we clear it. Without the
        # finally, an exception anywhere downstream hands the connection back
        # still carrying this tenant's org id, and the next request to reuse
        # that connection inherits it. That is a cross-tenant read.
        try:
            return self.get_response(request)
        finally:
            self._reset_org_context()

    def _set_org_context(self, request):
        """
        Set the PostgreSQL session variable for RLS.

        Args:
            request: Django request object with profile attached
        """
        if not hasattr(request, "org") or request.org is None:
            return

        org_id = str(request.org.id)

        try:
            with connection.cursor() as cursor:
                # Set the session variable (is_local=false for session scope)
                # Required because Django uses autocommit mode by default
                cursor.execute(
                    "SELECT set_config('app.current_org', %s, false)", [org_id]
                )
                logger.debug("Set RLS context: app.current_org = %s", org_id)

        except Exception as e:
            # RLS might not be configured - log but don't fail
            logger.debug("Could not set RLS context: %s", e)

    def _reset_org_context(self):
        """
        Reset the PostgreSQL session variable after request.
        Critical to prevent context leakage between requests on pooled connections.
        """
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT set_config('app.current_org', '', false)")
        except Exception:
            pass


class RequireOrgContext:
    """
    Stricter middleware that fails if org context is not set.

    Use this instead of SetOrgContext when you want to ensure
    all requests have proper org context (after RLS is fully enabled).

    Usage in settings.py:
        MIDDLEWARE = [
            ...
            'common.middleware.get_company.GetProfileAndOrg',
            'common.middleware.rls_context.RequireOrgContext',
            ...
        ]
    """

    # Paths that don't require org context
    EXEMPT_PATHS = [
        "/api/auth/refresh-token/",
        "/api/auth/me/",
        "/api/auth/switch-org/",
        "/api/auth/google/",
        "/api/auth/magic-link/request/",
        "/api/auth/magic-link/verify/",
        "/api/auth/magic-link/verify-code/",
        "/api/org/",
        "/admin/",
        "/swagger-ui/",
        "/api/schema/",
        # Public CSAT survey link (Tier 2 csat). Anonymous, sets RLS
        # context manually inside the view from the survey's own org_id.
        "/api/public/csat/",
        # Client portal: the invoice and estimate links emailed to customers.
        # Anonymous by design: the view authorises on `public_token` alone
        # (see invoices/public_views.py) and never reads request.user or
        # request.org, so requiring org context here rejected every customer
        # who clicked a link. Listed as two specific prefixes rather than a
        # blanket "/api/public/" so that anything mounted there in future has
        # to opt in deliberately.
        #
        # NOTE: this restores reachability, not readability. `_set_org_context`
        # returns early when request.org is None, so these requests run with
        # `app.current_org` empty, and the isolation policy from
        # `get_enable_policy_sql` is `org_id::text = NULLIF(current_setting(
        # 'app.current_org', true), '')`, which matches nothing when empty.
        # Verified as `crm_user` (non-superuser, no BYPASSRLS): invoice,
        # estimate and csat_survey all return 0 rows with empty context. So on
        # a correctly-configured production database these endpoints now
        # answer 404 rather than 403 until the view can resolve the org from
        # the token before querying. See docs/PORTAL_RLS.md.
        "/api/public/invoice/",
        "/api/public/estimate/",
        # Customer portal sign-in. Anonymous by design: the caller has no org
        # claim yet, so the view takes the org from the URL and sets the RLS
        # context itself before it reads `contacts`.
        #
        # This is the login prefix ONLY, and it must stay that way. Adding the
        # bare "/api/portal/" here would strip tenant-context enforcement from
        # every authenticated portal endpoint in one line, because this list is
        # prefix-matched. That is the same trap already recorded for
        # /api/packs/ in EXEMPT_EXACT_PATHS below. The authenticated portal
        # endpoints resolve their org in GetProfileAndOrg and are meant to be
        # checked here like everything else.
        "/api/portal/login/",
    ]

    # Paths exempt on an EXACT match only, never prefix-matched like
    # EXEMPT_PATHS above. Mirrors the exact-match pattern already used in
    # common.middleware.get_company.GetProfileAndOrg.process_request
    # (`if request.path in auth_skip_paths`).
    #
    # GET /api/packs/ is static content read from JSON files in the repo,
    # byte-identical for every tenant, no tenant data whatsoever. It backs
    # the pack chooser on the org-creation page, where a user creating their
    # *first* org has no org_id claim yet, so RequireOrgContext would 403 the
    # exact audience the page serves.
    #
    # This MUST stay an exact match, not a prefix: POST
    # /api/packs/<pack_id>/apply/ (writes org-wide config, ADMIN only) and
    # DELETE /api/packs/sample-data/ (deletes records, ADMIN only) both start
    # with "/api/packs/" too. Adding "/api/packs/" to the prefix-matched
    # EXEMPT_PATHS list instead of here would silently strip tenant-context
    # enforcement from those write/destroy endpoints. Do not move it there.
    # POST /api/auth/logout/ revokes the refresh token it is handed. It is
    # reached most often when the access token has already expired, so there is
    # no org claim to require, and demanding one would fail sign-out in exactly
    # the case sign-out matters. Exact match: the view reads nothing but the
    # token in the body, and no sub-path should inherit this.
    #
    # GET /healthz/ is an unauthenticated liveness probe (load balancers, uptime
    # monitors, container orchestrators). It renders a static template and reads
    # no tenant data, so there is no org context to require. Gating it makes every
    # health check return 403 and the service read as down. Exact match: nothing
    # should hang off /healthz/.
    EXEMPT_EXACT_PATHS = [
        "/healthz/",
        "/api/packs/",
        "/api/auth/logout/",
    ]

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Check if path requires org context
        if not self._is_exempt(request.path):
            # Skip check for URLs that don't resolve (let Django return 404)
            from django.urls import resolve
            from django.urls.exceptions import Resolver404

            try:
                resolve(request.path)
            except Resolver404:
                return self.get_response(request)

            if not hasattr(request, "org") or request.org is None:
                return JsonResponse(
                    {"detail": "Organization context is required. Please login again."},
                    status=403,
                )

        # Set org context
        self._set_org_context(request)

        # See SetOrgContext.__call__: the reset is in a finally because
        # `app.current_org` is SESSION-scoped and survives an exception. On a
        # pooled or otherwise reused connection, skipping it leaks this
        # tenant's context to whoever borrows the connection next.
        try:
            return self.get_response(request)
        finally:
            self._reset_org_context()

    def _is_exempt(self, path):
        """Check if path is exempt from org context requirement."""
        if path in self.EXEMPT_EXACT_PATHS:
            return True
        return any(path.startswith(exempt) for exempt in self.EXEMPT_PATHS)

    def _set_org_context(self, request):
        """Set PostgreSQL session variable (session scope for autocommit mode)."""
        if not hasattr(request, "org") or request.org is None:
            return

        org_id = str(request.org.id)

        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT set_config('app.current_org', %s, false)", [org_id]
                )
        except Exception as e:
            logger.warning("Failed to set RLS context: %s", e)

    def _reset_org_context(self):
        """Reset PostgreSQL session variable after request."""
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT set_config('app.current_org', '', false)")
        except Exception:
            pass


# SQL to enable RLS on all org-scoped tables
RLS_SETUP_SQL = """
-- Enable RLS on main tables
-- Run this after all org-scoped tables are identified

-- Example for leads table:
-- ALTER TABLE lead ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE lead FORCE ROW LEVEL SECURITY;  -- Apply to table owner too
-- CREATE POLICY org_isolation ON lead
--   USING (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid);

-- Tables that need RLS policies:
-- lead, accounts, contacts, opportunity, cases, tasks, invoices,
-- comment, attachments, document, teams, activity, tags, address,
-- api_settings, board, board_column, board_task, board_member

-- Note: Use NULLIF to handle empty string when context is not set
-- This makes the policy return no rows when context is not set (fail-safe)
"""
