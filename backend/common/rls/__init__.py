"""
Row-Level Security (RLS) Configuration for PostgreSQL.

This module provides centralized configuration and SQL generators for
database-level multi-tenancy using PostgreSQL Row-Level Security.

RLS ensures that:
1. Queries automatically filter by current org
2. Even if application code misses a filter, data is protected
3. Direct SQL access respects org boundaries

Usage:
    from common.rls import RLS_CONFIG, get_enable_policy_sql

    # Enable RLS on a table
    cursor.execute(get_enable_policy_sql('lead'))

    # Check config
    tables = RLS_CONFIG['tables']
"""

# PostgreSQL session variable for org context
# Set via: SELECT set_config('app.current_org', '<org_id>', true)
CONTEXT_VARIABLE = "app.current_org"

# Policy names
ISOLATION_POLICY = "org_isolation"
INSERT_POLICY = "org_insert_check"

# All org-scoped tables that need RLS policies
# Add new org-scoped tables here
# NOTE: Only include tables that have an org_id column directly
# Table names must match actual PostgreSQL table names (check via \dt)
ORG_SCOPED_TABLES = [
    # Core business entities
    "lead",
    "accounts",  # Note: plural
    "contacts",  # Note: plural
    "opportunity",
    "sales_goal",  # SalesGoal quota/target: org-scoped, see opportunity/0012
    # Both org-scoped opportunity children were live but unregistered, the
    # policy tooling (manage_rls --status, audits) could not see them. RLS is
    # stamped from source by common/0011 (line items) and opportunity/0013
    # (stage aging); these entries put them back under central governance.
    "opportunity_line_item",
    "stage_aging_config",
    "case",  # Note: singular
    "task",
    "invoice",
    # Supporting entities
    "comment",
    "commentFiles",  # Security fix: Added for RLS protection
    "attachments",
    "document",
    "teams",
    "activity",
    "tags",
    "address",
    "solution",
    "reopen_policy",
    "custom_field_definition",
    "escalation_policy",
    "inbound_mailbox",
    "email_message",
    "routing_rule",
    "routing_rule_state",
    # Kanban pipelines and their stages. All six carry org_id directly.
    # These were stamped out-of-band on existing databases but were absent from
    # this list and from every migration, so a database built purely from
    # migrations got no policies at all. common/0034 stamps them from source.
    "lead_pipeline",
    "lead_stage",
    "marketing_campaign",
    "lead_attribution_touch",
    "data_subject_request",
    "data_subject_request_event",
    "lead_conversion",
    "case_pipeline",
    "case_stage",
    "task_pipeline",
    "task_stage",
    # Boards (Kanban)
    "board",
    "board_column",
    "board_task",
    "board_member",
    # Settings
    "apiSettings",  # Note: camelCase
    "pack_application",
    # Email & Invoicing
    "account_email",
    "emailLogs",
    "invoice_history",
    "invoice_line_item",
    "invoice_template",
    "payment",
    "estimate",
    "estimate_line_item",
    "recurring_invoice",
    "recurring_invoice_line_item",
    # Products
    "product",
    # Orders
    "orders",
    "order_line_item",
    #
    # `security_audit_log` is deliberately NOT here. It is a platform-level
    # ledger, not tenant data: its `org` is `null=True` because the events it
    # exists to record happen outside any org (LOGIN_FAILURE, API_KEY_INVALID)
    # or deliberately straddle two (CROSS_ORG_ATTEMPT). The insert-check policy
    # compares `org_id::text` to `app.current_org`, and `NULL = anything` is not
    # true, so under the non-superuser role RLS_SETUP.md requires, every one of
    # those rows was refused. `AuditLogger._log` catches the failure and writes
    # a `logger.error`, so the audit trail was silently absent in exactly the
    # deployments configured correctly, for exactly the hostile events. It was
    # invisible because the dev database user is a superuser and CI ran SQLite.
    # `common/0036` drops the policies. Nothing outside a test reads the model,
    # and `common/tests/test_audit_log_not_exposed.py` keeps it that way: if an
    # endpoint ever needs to serve these rows, it must do the org filtering in
    # the ORM, because there is no policy underneath it.
    #
    # Notifications
    "notification",
    # Case watchers (Tier 2 watchers-mentions)
    "case_watcher",
    # Business hours (Tier 2 business-hours-sla)
    "business_calendar",
    "business_holiday",
    # Macros / canned responses (Tier 2 macros). RLS is enabled by the
    # migration; this entry registers the table with the central status
    # tooling (manage_rls --status, audit verifications).
    "macro",
    # CSAT surveys (Tier 2 csat).
    "csat_survey",
    # Time tracking (Tier 3 time-tracking).
    "time_entry",
    # Approval workflows (Tier 3 approvals).
    "approval_rule",
    "approval",
    # Web forms / web-to-lead (issue #634). All four are BaseOrgModel.
    # The public submit and embed endpoints run with the RLS context set from
    # the org id in their URL rather than from a JWT, because an embedded form
    # on a customer's website has neither. See webforms/public_views.py for why
    # the org has to be resolved from the path before anything queries.
    "web_form",
    "web_form_field",
    "web_form_submission",
    "web_form_daily_stat",
    # Programmatic API access
    # NOTE: personal_access_token is intentionally NOT RLS-protected. It is an
    # auth-bootstrap table (looked up by token_hash before any tenant context
    # exists), mirroring the Org table. Isolation for token management is enforced
    # by explicit org+profile filters in common/views/pat_views.py.
]

# Centralized RLS configuration
RLS_CONFIG = {
    "context_variable": CONTEXT_VARIABLE,
    "tables": ORG_SCOPED_TABLES,
    "policies": {
        "isolation": ISOLATION_POLICY,
        "insert": INSERT_POLICY,
    },
}


def get_check_table_exists_sql():
    """
    Returns SQL to check if a table exists.

    Usage:
        cursor.execute(get_check_table_exists_sql(), [table_name])
        exists = cursor.fetchone()[0]
    """
    return """
        SELECT EXISTS (
            SELECT FROM information_schema.tables
            WHERE table_schema = 'public'
            AND table_name = %s
        )
    """


def get_check_rls_status_sql():
    """
    Returns SQL to check RLS status for a table.

    Usage:
        cursor.execute(get_check_rls_status_sql(), [table_name])
        rls_enabled, rls_forced = cursor.fetchone()
    """
    return """
        SELECT relrowsecurity, relforcerowsecurity
        FROM pg_class
        WHERE relname = %s AND relnamespace = 'public'::regnamespace
    """


def get_set_context_sql():
    """
    Returns SQL to set the org context.

    Uses set_config with is_local=false to persist for the SESSION,
    not just the current transaction. This is required because Django
    uses autocommit mode by default.

    The middleware must reset this after each request to prevent leakage.

    Usage:
        cursor.execute(get_set_context_sql(), [org_id])
    """
    return f"SELECT set_config('{CONTEXT_VARIABLE}', %s, false)"


def get_enable_policy_sql(table):
    """
    Returns SQL to enable RLS on a table with proper policies.

    This creates:
    1. Enables RLS on the table
    2. Forces RLS for table owner
    3. Creates isolation policy for SELECT/UPDATE/DELETE
    4. Creates insert check policy

    Args:
        table: Table name

    Returns:
        SQL string to execute
    """
    return f"""
        -- Enable RLS on table
        ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY;
        ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY;

        -- Drop existing policies if any
        DROP POLICY IF EXISTS {ISOLATION_POLICY} ON "{table}";
        DROP POLICY IF EXISTS {INSERT_POLICY} ON "{table}";

        -- Create isolation policy (SELECT, UPDATE, DELETE)
        -- Uses NULLIF to return no rows when context is not set (fail-safe)
        CREATE POLICY {ISOLATION_POLICY} ON "{table}"
            FOR ALL
            USING (
                org_id::text = (select NULLIF(current_setting('{CONTEXT_VARIABLE}', true), ''))
            );

        -- Create insert check policy
        CREATE POLICY {INSERT_POLICY} ON "{table}"
            FOR INSERT
            WITH CHECK (
                org_id::text = (select NULLIF(current_setting('{CONTEXT_VARIABLE}', true), ''))
            );
    """


def get_disable_policy_sql(table):
    """
    Returns SQL to disable RLS on a table.

    The exact inverse of get_enable_policy_sql, including NO FORCE. Dropping the
    policies and disabling RLS without also clearing the force flag leaves the
    table reporting `relrowsecurity=false, relforcerowsecurity=true`, which is
    inert (forcing does nothing while RLS is off) but reads like a control that
    was half applied. That is not hypothetical: it is exactly how the state of
    `security_audit_log` was misread as missing tenant isolation, when
    `common/0036` had removed it deliberately.

    Most callers are the reverse half of an enable migration, so being a true
    inverse is also what makes those migrations round-trip cleanly.

    Args:
        table: Table name

    Returns:
        SQL string to execute
    """
    return f"""
        -- Drop policies
        DROP POLICY IF EXISTS {ISOLATION_POLICY} ON "{table}";
        DROP POLICY IF EXISTS {INSERT_POLICY} ON "{table}";

        -- Disable RLS, and clear the force flag that enabling set, so the
        -- table is left in the state it had before RLS was ever applied.
        ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY;
        ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY;
    """
