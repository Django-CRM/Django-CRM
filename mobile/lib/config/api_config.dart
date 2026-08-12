import 'package:flutter/foundation.dart';

/// API Configuration for BottleCRM
///
/// Provides centralized configuration for API endpoints.
/// Update [_developmentUrl] with your ngrok URL for development.
class ApiConfig {
  // ==========================================================================
  // BASE URLS - Update these for your environment
  // ==========================================================================

  /// Development API URL
  static const String _developmentUrl = 'https://pc-8004.rcdev.in';

  /// Production API URL
  static const String _productionUrl = 'https://api.bottlecrm.io';

  /// Public marketing site. Hosts the documents the app has to link to
  /// (`/terms-of-service`, `/privacy-policy`) rather than ship its own copies,
  /// which would drift the moment either is edited.
  static const String marketingSite = 'https://bottlecrm.io';

  /// Build-time override, so somebody self-hosting points a build at their own
  /// server without editing this file:
  ///
  ///     flutter build apk --release --dart-define=API_BASE_URL=https://crm.example.com
  ///
  /// Compile-time on purpose. A runtime setting would mean anything able to
  /// write the app's storage could redirect every request, and every JWT with
  /// it, to a server of its own choosing.
  static const String _baseUrlOverride = String.fromEnvironment('API_BASE_URL');

  /// Google's Web OAuth client, the audience the backend's `GOOGLE_CLIENT_ID`
  /// verifies against. Overridable the same way, because a self-hoster signs in
  /// against their own Google project:
  ///
  ///     --dart-define=GOOGLE_SERVER_CLIENT_ID=<id>.apps.googleusercontent.com
  ///
  /// Not a secret. A client id is public by design; the backend is what decides
  /// whether an ID token is acceptable.
  static const String googleServerClientId = String.fromEnvironment(
    'GOOGLE_SERVER_CLIENT_ID',
    defaultValue:
        '1072513761792-p59rct7b1c3go7l58e51r3geuqff2tfl.apps.googleusercontent.com',
  );

  /// The API host this build talks to.
  static final String baseUrl = resolveBaseUrl(
    override: _baseUrlOverride,
    isDebug: kDebugMode,
  );

  /// The override rules, separated from the build constants so both branches
  /// can be tested. `flutter test` runs one build, so a `--dart-define` the
  /// suite does not pass is a branch no test could otherwise reach.
  @visibleForTesting
  static String resolveBaseUrl({
    required String override,
    required bool isDebug,
  }) {
    if (override.isEmpty) {
      return isDebug ? _developmentUrl : _productionUrl;
    }
    // A trailing slash would produce `https://host//api`, and the resulting 404
    // on every request says nothing about the cause.
    final trimmed = override.endsWith('/')
        ? override.substring(0, override.length - 1)
        : override;
    // A release build talking plain HTTP puts every JWT on the wire in the
    // clear. Refusing loudly beats falling back to the default, which would
    // leave a self-hoster looking at somebody else's server and no explanation.
    // `http://` stays allowed in debug: that is how anyone runs against a local
    // Django.
    if (!isDebug && !trimmed.startsWith('https://')) {
      throw StateError(
        'API_BASE_URL must start with https:// in a release build. '
        'Got "$trimmed", which would send every token unencrypted.',
      );
    }
    return trimmed;
  }

  /// API base path
  static String get apiBaseUrl => '$baseUrl/api';

  // ==========================================================================
  // AUTHENTICATION ENDPOINTS
  // ==========================================================================

  /// Google Sign-In with ID token (same as old app)
  static String get googleLogin => '$apiBaseUrl/auth/google/';

  /// Request a passwordless sign-in code (OTP) by email
  static String get magicLinkRequest => '$apiBaseUrl/auth/magic-link/request/';

  /// Verify a 6-digit OTP code and exchange it for JWT tokens
  static String get magicLinkVerifyCode =>
      '$apiBaseUrl/auth/magic-link/verify-code/';

  /// Refresh JWT token
  static String get refreshToken => '$apiBaseUrl/auth/refresh-token/';

  /// Sign out: blacklists the refresh token server-side. Needs no access
  /// token, which is what lets it work when the session has already expired.
  static String get logout => '$apiBaseUrl/auth/logout/';

  /// Get current user info
  static String get me => '$apiBaseUrl/auth/me/';

  /// Get user profile with org details (read; ProfileDetailView)
  static String get profile => '$apiBaseUrl/auth/profile/';

  /// Update user profile, only `phone` is editable per backend contract.
  /// Distinct endpoint from `profile` above (ProfileView vs ProfileDetailView).
  static String get profileUpdate => '$apiBaseUrl/profile/';

  /// Switch organization context
  static String get switchOrg => '$apiBaseUrl/auth/switch-org/';

  /// Create an organization and become its admin (OrgProfileCreateView).
  /// Needs a signed-in user but no org context, which is the point: the caller
  /// is someone who has none yet.
  static String get orgCreate => '$apiBaseUrl/org/';

  /// IANA zone names with their current UTC offsets, for the org form's
  /// timezone picker. Served rather than read from the device so both clients
  /// use one vocabulary (see `common/org_time.py`).
  static String get timezones => '$apiBaseUrl/org/timezones/';

  /// The org's own settings. GET is open to any member (the company profile is
  /// not a secret), PATCH is admin-only. Always acts on the caller's own org:
  /// there is no id in the path, so there is nothing to scope.
  ///
  /// The org API key is not on this payload in any form. It authenticates as
  /// the org's first admin and is rotated through `OrgApiKeyView` alone.
  static String get orgSettings => '$apiBaseUrl/org/settings/';

  /// Vertical packs available to apply. Needs authentication but no org
  /// context, because its first caller is someone creating their first org.
  static String get packs => '$apiBaseUrl/packs/';

  /// Apply one pack. Admin-only, additive-only, and safe to repeat: a pack
  /// already applied reports everything skipped rather than refusing.
  static String packApply(String packId) => '$apiBaseUrl/packs/$packId/apply/';

  /// Delete the demo records a pack created for this org, and nothing else.
  /// Admin-only. A sample record the org has since attached real work to is
  /// deliberately kept and reported in `retained_by_type`.
  static String get packSampleData => '$apiBaseUrl/packs/sample-data/';

  // ==========================================================================
  // API TOKENS
  // ==========================================================================

  /// **The admin's org-wide oversight list.** Every token in the org, so a
  /// deactivated owner's row and a forgotten one can be found. ADMIN-only: a
  /// member gets 403, which is why the screen gates on role.
  ///
  /// Only `token_prefix` is ever returned. The server keeps a SHA-256 hash and
  /// no raw value exists to fetch.
  static String get orgTokens => '$apiBaseUrl/org/tokens/';

  /// Revoke any token in the org. Admin-only, org-scoped, idempotent. Separate
  /// from the self-scoped path below so the "a user manages only their own"
  /// guard there is never widened.
  static String orgToken(String id) => '$apiBaseUrl/org/tokens/$id/';

  /// Create a token **for yourself**. The server sets the owner from the
  /// caller's own profile, so there is no way to mint one for someone else.
  /// The raw value comes back once, in this response, and never again.
  static String get profileTokens => '$apiBaseUrl/profile/tokens/';

  /// Revoke one of **your own**. Self-scoped server-side, so a colleague's id
  /// 404s rather than being revoked. Distinct from `orgToken(id)`, which is
  /// the admin's org-wide revoke and refuses a member outright.
  static String profileToken(String id) => '$apiBaseUrl/profile/tokens/$id/';

  // ==========================================================================
  // DASHBOARD
  // ==========================================================================

  /// Dashboard summary data
  static String get dashboard => '$apiBaseUrl/dashboard/';

  // ==========================================================================
  // CRM ENDPOINTS
  // ==========================================================================

  /// Leads management
  static String get leads => '$apiBaseUrl/leads/';

  /// Lead comment (for update/delete)
  static String leadComment(String commentId) =>
      '$apiBaseUrl/leads/comment/$commentId/';

  /// Contacts management
  static String get contacts => '$apiBaseUrl/contacts/';

  /// Accounts (companies) management
  static String get accounts => '$apiBaseUrl/accounts/';

  /// Opportunities (deals) management
  static String get opportunities => '$apiBaseUrl/opportunities/';

  /// Opportunity comment (for update/delete)
  static String opportunityComment(String commentId) =>
      '$apiBaseUrl/opportunities/comment/$commentId/';

  /// The deals board: stage columns with their cards, capped at 100 a column.
  /// Status-based, so a column's `id` is the stage value itself.
  static String get opportunitiesKanban => '$apiBaseUrl/opportunities/kanban/';

  /// Move a deal to another column and, optionally, to a position in it. Takes
  /// `column_id` plus `above_id`/`below_id`, the same contract the web board
  /// uses. Distinct from `PATCH /opportunities/<id>/`, which edits the deal:
  /// this one also stamps the close when the destination is a closed stage.
  static String opportunityMove(String id) =>
      '$apiBaseUrl/opportunities/$id/move/';

  /// Sales goals. Reading is open to any member and narrowed server-side to
  /// their own goals and their teams'; creating, editing and deleting are
  /// admin-only and answer 403 to everyone else.
  static String get goals => '$apiBaseUrl/opportunities/goals/';

  static String goal(String id) => '$apiBaseUrl/opportunities/goals/$id/';

  /// Current individual goals for one period, ranked on attainment. Takes
  /// `?period_type=`, defaulting to MONTHLY server-side.
  ///
  /// Narrowed by the same rule as the list. It used to scope nothing, so a
  /// member whose own list came back empty could still read every colleague's
  /// target, attainment and email address off this endpoint.
  static String get goalsLeaderboard =>
      '$apiBaseUrl/opportunities/goals/leaderboard/';

  /// Shared files. The list answers two separately paginated envelopes,
  /// `documents_active` and `documents_inactive`. Uploading is open to any
  /// member; editing and deleting are the uploader or an admin.
  static String get documents => '$apiBaseUrl/documents/';

  static String document(String id) => '$apiBaseUrl/documents/$id/';

  /// The bytes of a document, gated by the same read predicate as the record.
  ///
  /// Fetch this with the Authorization header, never `launchUrl`. The external
  /// browser carries no token, and the file's own `/media/` path is not an
  /// alternative: it is served with no per-file authorization at all, which is
  /// why this endpoint exists.
  static String documentDownload(String id) =>
      '$apiBaseUrl/documents/$id/download/';

  /// The bytes of a file attached to a lead, deal, ticket or task. Same rule
  /// as `documentDownload`: authenticated fetch, never a storage URL.
  static String attachmentDownload(String id) =>
      '$apiBaseUrl/attachments/$id/download/';

  /// Tasks management
  static String get tasks => '$apiBaseUrl/tasks/';

  /// Tickets (support tickets) management
  static String get tickets => '$apiBaseUrl/cases/';

  /// Bulk-update selected tickets: POST `{ids, fields}`. Backend answers a
  /// per-record results envelope (`{error, updated, results}`), not a single
  /// ticket, so callers must not treat this like `updateTicket`.
  static String get casesBulkUpdate => '$apiBaseUrl/cases/bulk/update/';

  /// Bulk-delete selected tickets: POST `{ids}`. Same per-record envelope
  /// shape as [casesBulkUpdate], with `deleted` in place of `updated`.
  static String get casesBulkDelete => '$apiBaseUrl/cases/bulk/delete/';

  /// BottleCRM product support tickets opened by the signed-in user.
  static String get supportTickets => '$apiBaseUrl/support/';

  static String supportTicket(String id) => '$apiBaseUrl/support/$id/';

  static String supportTicketReplies(String id) =>
      '$apiBaseUrl/support/$id/replies/';

  static String supportMessageAttachment(String messageId) =>
      '$apiBaseUrl/support/messages/$messageId/attachment/';

  /// Ticket detail (retrieve / update / delete / add comment)
  static String ticketDetail(String id) => '$apiBaseUrl/cases/$id/';

  /// Ticket comment (single, for delete or status checks)
  static String ticketComment(String commentId) =>
      '$apiBaseUrl/cases/comment/$commentId/';

  /// Watch / unwatch a ticket (POST / DELETE).
  static String ticketWatch(String id) => '$apiBaseUrl/cases/$id/watch/';

  /// List watchers on a ticket.
  static String ticketWatchers(String id) => '$apiBaseUrl/cases/$id/watchers/';

  /// Tickets the current user is watching.
  static String get ticketsWatching => '$apiBaseUrl/cases/watching/';

  /// Merge source ticket as duplicate INTO another ticket.
  static String ticketMerge(String sourceId, String intoId) =>
      '$apiBaseUrl/cases/$sourceId/merge/$intoId/';

  /// Reverse a prior merge. Called on the SOURCE id.
  static String ticketUnmerge(String sourceId) =>
      '$apiBaseUrl/cases/$sourceId/unmerge/';

  /// Parent/child tree rooted at the closest visible ancestor.
  static String ticketTree(String id) => '$apiBaseUrl/cases/$id/tree/';

  /// Link this ticket to a parent (or detach by sending null parent_id).
  static String ticketLinkParent(String id) => '$apiBaseUrl/cases/$id/link/';

  /// Close this ticket and optionally its descendants.
  static String ticketCloseWithChildren(String id) =>
      '$apiBaseUrl/cases/$id/close-with-children/';

  /// Time entries for a ticket (GET list / POST manual entry).
  static String ticketTimeEntries(String id) =>
      '$apiBaseUrl/cases/$id/time-entries/';

  /// Start a running timer on a ticket.
  static String ticketTimerStart(String id) =>
      '$apiBaseUrl/cases/$id/time-entries/start/';

  /// Time summary for a ticket, totals + per-profile breakdown.
  static String ticketTimeSummary(String id) =>
      '$apiBaseUrl/cases/$id/time-summary/';

  /// Stop a running timer by entry id (entry-scoped, not case-scoped).
  static String timeEntryStop(String entryId) =>
      '$apiBaseUrl/time-entries/$entryId/stop/';

  /// A week of the caller's own time, grouped into day buckets.
  ///
  /// `start` and `end` are inclusive YYYY-MM-DD. The endpoint also takes a
  /// `profile` parameter, which this app never sends: passing anyone else's is
  /// admin-only and answers 403, and the screen is "your week" by design.
  static String timesheet({required String start, required String end}) =>
      '$apiBaseUrl/time-entries/timesheet/?start=$start&end=$end';

  /// Solutions (Knowledge Base). List/create.
  static String get solutions => '$apiBaseUrl/cases/solutions/';

  /// Solution detail (GET / PUT / DELETE).
  static String solutionDetail(String id) => '$apiBaseUrl/cases/solutions/$id/';

  /// Publish a solution (must be status=approved).
  static String solutionPublish(String id) =>
      '$apiBaseUrl/cases/solutions/$id/publish/';

  /// Unpublish a solution.
  static String solutionUnpublish(String id) =>
      '$apiBaseUrl/cases/solutions/$id/unpublish/';

  /// Link a solution to a ticket (POST {solution_id}).
  static String ticketSolutionLink(String ticketId) =>
      '$apiBaseUrl/cases/$ticketId/solutions/';

  /// Unlink a solution from a ticket.
  static String ticketSolutionUnlink(String ticketId, String solutionId) =>
      '$apiBaseUrl/cases/$ticketId/solutions/$solutionId/';

  /// Solution suggestions for a ticket (typeahead by ticket context).
  static String ticketSolutionSuggestions(String ticketId) =>
      '$apiBaseUrl/cases/$ticketId/solution-suggestions/';

  /// Approvals inbox / per-case list. Query params: ?case=&state=&mine=
  static String get approvals => '$apiBaseUrl/cases/approvals/';

  /// Approve / reject / cancel an approval.
  static String approvalApprove(String id) =>
      '$apiBaseUrl/cases/approvals/$id/approve/';
  static String approvalReject(String id) =>
      '$apiBaseUrl/cases/approvals/$id/reject/';
  static String approvalCancel(String id) =>
      '$apiBaseUrl/cases/approvals/$id/cancel/';

  /// Request approval for a ticket.
  static String ticketRequestApproval(String ticketId) =>
      '$apiBaseUrl/cases/$ticketId/request-approval/';

  /// Analytics endpoints. All accept ?from=YYYY-MM-DD&to=YYYY-MM-DD plus
  /// optional ?priority=, ?agent=, ?team= filters.
  static String get analyticsFrt => '$apiBaseUrl/cases/analytics/frt/';
  static String get analyticsMttr => '$apiBaseUrl/cases/analytics/mttr/';
  static String get analyticsBacklog => '$apiBaseUrl/cases/analytics/backlog/';
  static String get analyticsAgents => '$apiBaseUrl/cases/analytics/agents/';
  static String get analyticsSla => '$apiBaseUrl/cases/analytics/sla/';

  /// Invoices management.
  ///
  /// The list takes `search`, `status`, `account`, `contact`, `opportunity`,
  /// `assigned_to`, `created_by`, `issue_date_gte`/`_lte`, `due_date_gte`/`_lte`
  /// and `sort` (one of created_at, due_date, issue_date, total_amount, status,
  /// optionally `-` prefixed). It answers `{count, next, previous, results,
  /// totals}`, where `totals` is deliberately computed over everything the
  /// caller can see rather than over the current filter or page.
  static String get invoices => '$apiBaseUrl/invoices/';

  static String invoice(String id) => '$apiBaseUrl/invoices/$id/';

  /// Emails the invoice to its client and stamps `sent_at`. A Draft becomes
  /// Sent; the server refuses a Paid or Cancelled invoice with a 400.
  static String invoiceSend(String id) => '$apiBaseUrl/invoices/$id/send/';

  /// Records a payment. Despite the name it takes a partial `amount`, and
  /// defaults to the whole outstanding balance when none is given. The server
  /// rejects an amount that is zero, negative, or above `amount_due`.
  static String invoiceMarkPaid(String id) =>
      '$apiBaseUrl/invoices/$id/mark-paid/';

  /// Cancels the invoice. Refused with a 400 when it is already Cancelled, or
  /// when it is Paid.
  static String invoiceCancel(String id) => '$apiBaseUrl/invoices/$id/cancel/';

  /// Payments already recorded against one invoice.
  static String invoicePayments(String id) =>
      '$apiBaseUrl/invoices/$id/payments/';

  /// Estimates (quotes). Takes `search`, `status` and `account`.
  static String get estimates => '$apiBaseUrl/invoices/estimates/';

  /// Raises an invoice from the estimate. Refused with a 400 once one exists.
  static String estimateConvert(String id) =>
      '$apiBaseUrl/invoices/estimates/$id/convert/';

  static String estimateSend(String id) =>
      '$apiBaseUrl/invoices/estimates/$id/send/';

  /// Recurring billing schedules. Takes `is_active=true|false`.
  static String get recurringInvoices => '$apiBaseUrl/invoices/recurring/';

  /// Flips a schedule between paused and running. The server toggles from the
  /// stored value rather than taking a target state, so this must never be
  /// sent twice for one intent.
  static String recurringToggle(String id) =>
      '$apiBaseUrl/invoices/recurring/$id/toggle/';

  /// The product catalogue. Reading is open to any member; creating, editing
  /// and deleting are admin-only and answer 403 otherwise.
  static String get products => '$apiBaseUrl/invoices/products/';
  static String product(String id) => '$apiBaseUrl/invoices/products/$id/';

  /// PDF templates. Neither of these carries the raw HTML or CSS: only the
  /// admin-only editor endpoint does, and this app does not call it. That is
  /// what lets the phone edit a template at all. The fields it writes are the
  /// ordinary ones, the detail PUT is partial, so a save from here leaves the
  /// markup exactly as the web editor left it.
  static String get invoiceTemplates => '$apiBaseUrl/invoices/templates/';
  static String invoiceTemplate(String id) =>
      '$apiBaseUrl/invoices/templates/$id/';

  /// Reports. Both are admin-only and answer 403 to everyone else.
  static String get invoiceReportDashboard =>
      '$apiBaseUrl/invoices/reports/dashboard/';
  static String get invoiceReportAging => '$apiBaseUrl/invoices/reports/aging/';

  /// Kanban boards. The list returns only boards you own or belong to, so an
  /// empty list is an answer and not a permission failure: creating one makes
  /// you its owner.
  static String get boards => '$apiBaseUrl/boards/';

  /// A board's lanes, each with its cards nested under `tasks`. This is the
  /// one call that renders a whole board.
  static String boardLanes(String boardId) =>
      '$apiBaseUrl/boards/$boardId/columns/';

  /// POST a card into a lane. The server takes the lane from this URL, so a
  /// card cannot be posted into another board's lane.
  static String boardLaneCards(String laneId) =>
      '$apiBaseUrl/boards/columns/$laneId/tasks/';

  /// One card: PUT to move or edit it, DELETE to remove it.
  static String boardCard(String cardId) => '$apiBaseUrl/boards/tasks/$cardId/';

  // ==========================================================================
  // NOTIFICATIONS
  // ==========================================================================

  /// The signed-in user's own feed. Every query here is scoped to
  /// `recipient=request.profile` server-side, so there is no id to pass and no
  /// way to ask for someone else's.
  ///
  /// `?unread=true` narrows it and `?limit=` caps it at 100. Delivery is by
  /// polling with `?since=<iso>`; there is no stream.
  static String notifications({int limit = 50}) =>
      '$apiBaseUrl/notifications/?limit=$limit';

  /// Mark one notification read. Idempotent: re-marking does not move the
  /// timestamp backwards.
  static String notificationRead(String id) =>
      '$apiBaseUrl/notifications/$id/read/';

  /// Mark every unread notification read.
  static String get notificationsReadAll =>
      '$apiBaseUrl/notifications/read-all/';

  // ==========================================================================
  // USERS & TAGS ENDPOINTS
  // ==========================================================================

  /// Get teams and users (for assignment dropdowns)
  static String get teamsAndUsers => '$apiBaseUrl/users/get-teams-and-users/';

  /// Auto-routing rules, ordered by `priority_order` server-side. That order is
  /// the behaviour: the engine takes the first match and, when the rule says
  /// so, stops.
  static String get routingRules => '$apiBaseUrl/cases/routing-rules/';

  /// One routing rule. PUT is partial, so a body may carry one key. DELETE is a
  /// HARD delete here, unlike custom fields and tags.
  static String routingRule(String id) =>
      '$apiBaseUrl/cases/routing-rules/$id/';

  /// Tags management. Active tags only by default; the settings screen passes
  /// `?include_archived=true` because it is the one that turns a tag back on
  /// and cannot offer that for a row it never fetched.
  static String get tags => '$apiBaseUrl/tags/';

  /// One tag. DELETE turns it off (a soft archive: the row and every record's
  /// link to it stay), and nothing hard-deletes a tag anywhere.
  static String tag(String id) => '$apiBaseUrl/tags/$id/';

  /// Turn an archived tag back on.
  static String tagRestore(String id) => '$apiBaseUrl/tags/$id/restore/';

  /// Move every record off this tag onto the one named by `into`, then archive
  /// this one. Both ids are re-resolved inside the caller's org server-side.
  static String tagMerge(String id) => '$apiBaseUrl/tags/$id/merge/';

  /// Escalation policies, at most one per priority. Each row arrives with
  /// `breaches_last_30d`, a server-side count the list has no other way to get.
  static String get escalationPolicies =>
      '$apiBaseUrl/cases/escalation-policies/';

  /// One escalation policy. PUT is partial, so a one-key body is legal, and it
  /// strips `priority` before the serializer sees it. DELETE is a HARD delete
  /// here, unlike custom fields and tags.
  static String escalationPolicy(String id) =>
      '$apiBaseUrl/cases/escalation-policies/$id/';

  /// Inbound email addresses. GET is open to any member, and strips the two
  /// admin-only integration fields (`topic_arn`, `has_webhook_secret`) for
  /// everyone else. POST, PUT and DELETE are admin-only.
  static String get mailboxes => '$apiBaseUrl/cases/mailboxes/';

  /// One mailbox. PUT is partial, so a body may carry one key. DELETE is a HARD
  /// delete and takes the topic pin with it.
  static String mailbox(String id) => '$apiBaseUrl/cases/mailboxes/$id/';

  /// Approval rules, the gates on a ticket close. GET is open to any member;
  /// every write is admin-only.
  static String get approvalRules => '$apiBaseUrl/cases/approval-rules/';

  /// One approval rule. PUT is partial. DELETE is two outcomes behind one verb:
  /// a rule with approval history is turned off rather than destroyed, and both
  /// answer 2xx, so the response body is what says which happened.
  static String approvalRule(String id) =>
      '$apiBaseUrl/cases/approval-rules/$id/';

  /// The org's default business calendar, created on first read. Reading is
  /// open to any member; every write below is admin-only.
  static String get businessCalendar => '$apiBaseUrl/business-hours/calendar/';

  /// The calendar's own detail url, which is what PUT goes to. The collection
  /// path above does not accept one.
  static String businessCalendarDetail(String id) =>
      '$apiBaseUrl/business-hours/calendar/$id/';

  /// Holidays on a calendar. POST is idempotent on date: a date already stored
  /// answers 200 with the EXISTING row, name included, and creates nothing.
  static String businessHolidays(String calendarId) =>
      '$apiBaseUrl/business-hours/calendar/$calendarId/holidays/';

  /// One holiday. DELETE is a hard delete and the day counts as working time
  /// again at once.
  static String businessHoliday(String calendarId, String holidayId) =>
      '$apiBaseUrl/business-hours/calendar/$calendarId/holidays/$holidayId/';

  /// The org's reopen policy, a singleton created on first read. **GET is
  /// admin-only here**, unlike the business calendar, so the screen that reads
  /// it has to gate on role rather than show a member a 403.
  static String get reopenPolicy => '$apiBaseUrl/cases/reopen-policy/';

  /// Custom field definitions (per-org schema for entities like Case/Lead/...).
  /// Query with `?target_model=Case&active_only=true`. Pass
  /// `include_counts=false` unless you need `records_missing_value`: computing
  /// it costs a full scan of the org's records per definition.
  static String get customFieldDefinitions => '$apiBaseUrl/custom-fields/';

  /// Saved replies. GET lists the ones visible to the caller: every org macro
  /// plus their own personal ones, never anybody else's. `?active=true` for
  /// the reply-box picker, unfiltered for the settings screen, which has to
  /// see the turned-off rows to offer turning one back on.
  static String get macros => '$apiBaseUrl/macros/';

  /// One saved reply. PATCH edits it (PUT runs the serializer non-partial and
  /// 400s without both title and body). DELETE turns an org macro off and
  /// deletes a personal one for good, decided server-side from the row.
  static String macro(String id) => '$apiBaseUrl/macros/$id/';

  /// Substitute the `%token%` placeholders against a ticket and return the
  /// text: POST `{"case_id": "<ticket id>"}`. Server-side on purpose, so the
  /// supported token set lives in one place and no client can drift from it.
  static String macroRender(String id) => '$apiBaseUrl/macros/$id/render/';

  /// One definition: PUT edits it, DELETE turns it off. DELETE is a soft
  /// delete (`CustomFieldDefinitionDetailView.delete` flips `is_active` and
  /// leaves stored values readable), so nothing here says "delete" to a user.
  /// Both verbs are admin-only server-side.
  static String customField(String id) => '$apiBaseUrl/custom-fields/$id/';

  /// People in the org: GET lists active and inactive, POST invites.
  /// Admin-only server-side, 403 for everyone else on both verbs.
  static String get orgUsers => '$apiBaseUrl/users/';

  /// One person's role: PATCH `{"role": "ADMIN"|"USER"}`. Takes the USER id,
  /// not the profile id.
  static String orgUser(String userId) => '$apiBaseUrl/user/$userId/';

  /// Activate or deactivate: POST `{"status": "Active"|"Inactive"}`. Also the
  /// USER id. The server refuses to deactivate the last active admin.
  static String orgUserStatus(String userId) =>
      '$apiBaseUrl/user/$userId/status/';

  // ==========================================================================
  // REQUEST CONFIGURATION
  // ==========================================================================

  /// Connection timeout duration
  static const Duration connectTimeout = Duration(seconds: 30);

  /// Uploads get longer. A 25 MB attachment (the server's limit) does not
  /// finish in 30 seconds on a phone connection, and the timeout that fired
  /// would look like a server fault.
  static const Duration uploadTimeout = Duration(minutes: 3);

  /// Receive timeout duration
  static const Duration receiveTimeout = Duration(seconds: 30);

  /// Default request headers
  static Map<String, String> get defaultHeaders => {
    'Content-Type': 'application/json',
    'Accept': 'application/json',
  };
}
