# Security audit log

`GET /api/org/audit-log/` lets an org's admins read its security audit log: sign-ins, org switches,
refused requests, revoked memberships, paused webhooks and the like. The view is
`SecurityAuditLogListView` (`backend/common/views/audit_log_views.py`), routed in
`common/urls.py`; the events are written by `AuditLogger` in `common/audit_log.py`. See
[Conventions](conventions.md#pagination) for `limit` and `offset`, and [Errors](errors.md) for the
refusal shapes.

## Who may read it

Org admins only (`IsOrgAdmin`: the `ADMIN` role, or a Django superuser's profile in the org). A
member is refused with `403`:

```json
{"detail": "You must be an organization administrator to perform this action."}
```

It is read from a signed-in session and nothing else. `/api/org/audit-log/` is on the credential
deny-list in `common/scopes.py`, beside `/api/webhooks/` and the token routes, so a personal access
token or the organization API key is refused before the view runs, whatever scopes it carries (see
[Tokens and API keys](tokens-and-api-keys.md#what-no-token-may-do-whatever-its-scopes)). A leaked
token cannot read what the org has noticed about it.

Only the caller's own org's rows are returned. The table has no row-level security policy on
purpose (its `org` is empty for events such as a failed login with an unknown email), so the
view's `org=request.profile.org` filter is the barrier, and a row with no org never matches it.

## Query parameters

| Parameter | Meaning |
| --- | --- |
| `event_type` | One event type, by value (`LOGIN_SUCCESS`, `WEBHOOK_PAUSED`, ...). An unknown value is a `400`. |
| `include_token_refresh` | `true` to list `TOKEN_REFRESH` rows alongside everything else. Any other value, or none, leaves them out. |
| `actor` | The user id of whoever the event is about. Not a UUID is a `400`. |
| `from`, `to` | Days, `YYYY-MM-DD`, inclusive, in the org's timezone. `from` after `to` is a `400`. |
| `limit`, `offset` | Paging: 25 rows by default, at most 100. |

`TOKEN_REFRESH` rows (a signed-in client quietly renewing its session) are most of what the table
holds, so they are left out of the list, and out of `count`, unless the caller asks for them: with
`include_token_refresh=true`, or with `event_type=TOKEN_REFRESH`, which lists only those. They are
still recorded either way.

Rows come newest first. For example, `GET /api/org/audit-log/?event_type=LOGIN_FAILURE&from=2026-09-01`
lists this month's failed sign-ins that were recorded against the org.

```json
{"event_type": ["Unknown event type."]}
```

## The response

The usual `limit`/`offset` envelope, plus the event types the log can hold, so a client can offer
them as a filter without a list of its own:

```json
{
  "count": 213,
  "next": "https://api.example.com/api/org/audit-log/?limit=25&offset=25",
  "previous": null,
  "results": [
    {
      "id": "7d0e...",
      "event_type": "WEBHOOK_PAUSED",
      "event_label": "Webhook Paused",
      "created_at": "2026-09-26T09:12:44.018+00:00",
      "success": true,
      "actor": {"id": "3b1f...", "name": "Dana Reyes", "email": "dana@example.com"},
      "ip_address": null,
      "user_agent": "",
      "request_method": "",
      "request_path": "",
      "details": {"endpoint_id": "91ac...", "creator_id": "3b1f...", "pause_reason": "..."}
    }
  ],
  "event_types": [{"value": "LOGIN_SUCCESS", "label": "Login Success"}]
}
```

`actor` is `null` for an event with no user.

What each row returns is chosen field by field rather than copied from the table:

- **No `description`.** An `ORG_SWITCH` row's text names the org the user switched from, which is
  another tenant's name.
- **`details` holds only known-safe keys** (`SAFE_DETAILS`: `action`, `resource`, `deleted_count`,
  `endpoint_id`, `creator_id`, `pause_reason`, `previous_creator_id`, `changed`). Those are ids, counts and
  sentences the server wrote. Everything else in the stored metadata can hold what a caller
  supplied (the email a failed login tried, an API key prefix, free-text details of suspicious
  activity) and is left out.
- **A merge row also carries both records** (`EVENT_DETAILS`): a `RECORD_MERGED` row adds `entity`
  (`lead`, `contact` or `account`), `kept_id`, `kept_name`, `merged_id` and `merged_name`. The
  merged record is deleted, so this is the only place its name survives. Those keys are allowed on
  that event only; the same key on any other row is left out.
- **Paths under `/api/public/` are cut to that prefix.** Some public links carry a token in the URL
  (a satisfaction-survey link, for one), and the log should not repeat it.
