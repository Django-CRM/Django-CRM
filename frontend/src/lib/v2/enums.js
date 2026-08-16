/**
 * v2 enums, mirrored 1:1 from the Django models so the API swap is a
 * field mapping, not a translation.
 *
 *   STAGES, OPPORTUNITY_TYPES, SOURCES  → backend/common/utils.py
 *   LEAD_STATUS, LEAD_SOURCE            → backend/common/utils.py
 *   STATUS_CHOICE, PRIORITY_CHOICE,
 *   CASE_TYPE                           → backend/common/utils.py (cases)
 *   INVOICE_STATUS                      → backend/invoices/models.py
 *   aging_status                        → Opportunity.get_aging_status()
 *                                         returns 'green' | 'yellow' | 'red'
 *
 * TONE is the only place colour is decided. Six tones, and Ember is not one
 * of them. Ember belongs to actions, never to status.
 *   ink   → neutral, structural
 *   slate → nothing to do here (includes aging "green": on pace is the
 *           absence of a problem, not an achievement)
 *   clay  → attention, past expected_days
 *   rust  → overdue, stalled, urgent, lost
 *   moss  → won, paid, completed
 */

export const STAGES = [
  'PROSPECTING',
  'QUALIFICATION',
  'PROPOSAL',
  'NEGOTIATION',
  'CLOSED_WON',
  'CLOSED_LOST'
];

/** The four stages a deal moves through before it closes. Drives the meter. */
export const OPEN_STAGES = STAGES.slice(0, 4);

export const STAGE_LABEL = {
  PROSPECTING: 'Prospecting',
  QUALIFICATION: 'Qualification',
  PROPOSAL: 'Proposal',
  NEGOTIATION: 'Negotiation',
  CLOSED_WON: 'Closed Won',
  CLOSED_LOST: 'Closed Lost'
};

export const STAGE_TONE = {
  PROSPECTING: 'slate',
  QUALIFICATION: 'slate',
  PROPOSAL: 'ink',
  NEGOTIATION: 'ink',
  CLOSED_WON: 'moss',
  CLOSED_LOST: 'rust'
};

export const OPPORTUNITY_TYPE_LABEL = {
  NEW_BUSINESS: 'New Business',
  EXISTING_BUSINESS: 'Existing Business',
  RENEWAL: 'Renewal',
  UPSELL: 'Upsell',
  CROSS_SELL: 'Cross-sell'
};

/** Opportunity.aging_status. The API returns these three strings verbatim. */
export const AGING_TONE = { green: 'slate', yellow: 'clay', red: 'rust' };
export const AGING_LABEL = { green: 'On pace', yellow: 'Past expected', red: 'Stalled' };

export const LEAD_STATUS_TONE = {
  assigned: 'slate',
  'in process': 'ink',
  converted: 'moss',
  recycled: 'slate',
  closed: 'rust'
};

/**
 * common.utils LEAD_STATUS / LEAD_SOURCE / INDCHOICES: the stored values,
 * verbatim, lowercase and misspellings included. "compaign" is the value in
 * the database; correcting it here would produce a form that writes a value
 * the column rejects.
 *
 * Labels are separate because these are stored the way somebody typed them in
 * 2015 and SHOUTING an industry at a person is not a design decision.
 */
export const LEAD_STATUSES = ['assigned', 'in process', 'converted', 'recycled', 'closed'];

export const LEAD_STATUS_LABEL = {
  assigned: 'Assigned',
  'in process': 'In process',
  converted: 'Converted',
  recycled: 'Recycled',
  closed: 'Closed'
};

/**
 * The statuses the leads LIST can display. `LeadListView` excludes `converted`
 * outright and splits `closed` into a `close_leads` block the frontend never
 * reads, so offering either as a filter option gives a permanently empty page
 * that reads as "you have none" rather than "this page cannot show them".
 * The full set stays in LEAD_STATUSES for the detail page's status select.
 */
export const LEAD_LIST_STATUSES = ['assigned', 'in process', 'recycled'];

export const LEAD_SOURCES = [
  'call',
  'email',
  'existing customer',
  'partner',
  'public relations',
  'compaign',
  'other'
];

export const LEAD_SOURCE_LABEL = {
  call: 'Call',
  email: 'Email',
  'existing customer': 'Existing customer',
  partner: 'Partner',
  'public relations': 'Public relations',
  compaign: 'Campaign',
  other: 'Other'
};

/**
 * Mirrors `leads/workflow.py::IRREVERSIBLE_STATUSES`. A converted lead can be
 * neither re-converted (which would build a second opportunity) nor moved
 * back out (which would orphan the account, contact and opportunity that
 * conversion created). `LeadCreateSerializer.validate_status` refuses both.
 *
 * "closed" is deliberately absent: reopening a closed lead creates nothing and
 * destroys nothing, so it stays an ordinary edit.
 */
export const LEAD_IRREVERSIBLE_STATUSES = ['converted'];

export const INDUSTRIES = [
  'ADVERTISING',
  'AGRICULTURE',
  'APPAREL & ACCESSORIES',
  'AUTOMOTIVE',
  'BANKING',
  'BIOTECHNOLOGY',
  'BUILDING MATERIALS & EQUIPMENT',
  'CHEMICAL',
  'COMPUTER',
  'EDUCATION',
  'ELECTRONICS',
  'ENERGY',
  'ENTERTAINMENT & LEISURE',
  'FINANCE',
  'FOOD & BEVERAGE',
  'GROCERY',
  'HEALTHCARE',
  'INSURANCE',
  'LEGAL',
  'MANUFACTURING',
  'PUBLISHING',
  'REAL ESTATE',
  'SERVICE',
  'SOFTWARE',
  'SPORTS',
  'TECHNOLOGY',
  'TELECOMMUNICATIONS',
  'TELEVISION',
  'TRANSPORTATION',
  'VENTURE CAPITAL'
];

/** "FOOD & BEVERAGE" → "Food & beverage". The value sent to the API is unchanged. */
export const industryLabel = (v) => (!v ? '' : v.charAt(0) + v.slice(1).toLowerCase());

export const PRIORITY_TONE = { Urgent: 'rust', High: 'clay', Normal: 'slate', Low: 'slate' };

export const CASE_STATUS_TONE = {
  New: 'ink',
  Assigned: 'ink',
  Pending: 'clay',
  Closed: 'moss',
  Rejected: 'rust',
  Duplicate: 'slate'
};

export const INVOICE_STATUS_TONE = {
  Draft: 'slate',
  Sent: 'ink',
  Viewed: 'ink',
  Paid: 'moss',
  Partially_Paid: 'clay',
  Overdue: 'rust',
  Pending: 'clay',
  Cancelled: 'slate'
};

/** INVOICE_STATUS uses an underscore on the wire; never show it to a person. */
export const invoiceStatusLabel = (s) => String(s ?? '').replace(/_/g, ' ');

/**
 * tasks.Task.STATUS_CHOICES / PRIORITY_CHOICES.
 *
 * Note the trap: a Task's priority is Low/Medium/High, while a Case's is
 * Low/Normal/High/Urgent. They are different enums on different models and
 * "Medium" is not a valid case priority. Keeping them in separate maps means
 * a mismatch is a missing key you can see, not a silently unstyled pill.
 */
export const TASK_STATUS = ['New', 'In Progress', 'Completed'];
export const TASK_STATUS_TONE = { New: 'slate', 'In Progress': 'ink', Completed: 'moss' };

export const TASK_PRIORITY = ['Low', 'Medium', 'High'];
export const TASK_PRIORITY_TONE = { Low: 'slate', Medium: 'slate', High: 'clay' };

/**
 * cases.Solution.STATUS_CHOICES, plus the separate is_published flag.
 *
 * Status and published are two different facts: an article can be approved
 * and still unpublished. v1 showed only one of them, so "approved" articles
 * customers could not see looked live. Both are surfaced here.
 */
export const SOLUTION_STATUS = ['draft', 'reviewed', 'approved'];
export const SOLUTION_STATUS_LABEL = { draft: 'Draft', reviewed: 'Reviewed', approved: 'Approved' };
export const SOLUTION_STATUS_TONE = { draft: 'slate', reviewed: 'clay', approved: 'moss' };

/**
 * opportunity.SalesGoal, GOAL_TYPES and PERIOD_TYPES from common/utils.py.
 *
 * The status values are the four SalesGoal.status returns, and the labels say
 * what they mean rather than repeating the wire value: "behind" alone does not
 * tell you behind on what. They are pace judgements, not percentages.
 */
export const GOAL_TYPE_LABEL = {
  REVENUE: 'Revenue',
  DEALS_CLOSED: 'Deals closed',
  ACTIVITIES: 'Activities'
};

/** Opportunity types, for the per-goal weighting on a revenue or deals goal. */
export const DEAL_TYPE_LABEL = {
  NEW_BUSINESS: 'New business',
  EXISTING_BUSINESS: 'Existing business',
  RENEWAL: 'Renewal',
  UPSELL: 'Upsell',
  CROSS_SELL: 'Cross-sell'
};

export const PERIOD_TYPE_LABEL = {
  MONTHLY: 'Monthly',
  QUARTERLY: 'Quarterly',
  YEARLY: 'Yearly',
  CUSTOM: 'Custom'
};

export const GOAL_STATUS_LABEL = {
  completed: 'Target met',
  on_track: 'On pace',
  at_risk: 'Slipping',
  behind: 'Behind pace'
};

export const GOAL_STATUS_TONE = {
  completed: 'moss',
  on_track: 'slate',
  at_risk: 'clay',
  behind: 'rust'
};

/** cases.approvals: APPROVAL_STATE_CHOICES. */
export const APPROVAL_STATE_LABEL = {
  pending: 'Waiting',
  approved: 'Approved',
  rejected: 'Rejected',
  cancelled: 'Withdrawn'
};

export const APPROVAL_STATE_TONE = {
  pending: 'clay',
  approved: 'moss',
  rejected: 'rust',
  cancelled: 'slate'
};

/**
 * invoices.ESTIMATE_STATUS, its own enum, not the invoice one. There is no
 * Paid estimate and no Accepted invoice; keeping the maps apart makes a
 * mix-up show up as a missing key instead of an unstyled pill.
 *
 * Accepted is moss because the customer said yes. Whether it has been turned
 * into an invoice yet is a separate fact, and the list shows it separately.
 * A green pill on money nobody has billed is exactly the wrong reassurance.
 */
export const ESTIMATE_STATUS_TONE = {
  Draft: 'slate',
  Sent: 'ink',
  Viewed: 'ink',
  Accepted: 'moss',
  Declined: 'rust',
  Expired: 'clay'
};

/** invoices.RECURRING_FREQUENCIES; CUSTOM carries its interval in custom_days. */
export const RECURRING_FREQUENCY_LABEL = {
  WEEKLY: 'Weekly',
  BIWEEKLY: 'Every 2 weeks',
  MONTHLY: 'Monthly',
  QUARTERLY: 'Quarterly',
  SEMI_ANNUALLY: 'Every 6 months',
  YEARLY: 'Yearly',
  CUSTOM: 'Custom'
};

export const PAYMENT_TERMS_LABEL = {
  DUE_ON_RECEIPT: 'Due on receipt',
  NET_15: 'Net 15',
  NET_30: 'Net 30',
  NET_45: 'Net 45',
  NET_60: 'Net 60',
  CUSTOM: 'Custom'
};

/**
 * common.Profile.role. Two values, and that is the whole set. ADMIN and USER.
 * Role is server-derived from the profile; nothing the browser sends decides
 * it. This map exists to label a value the API gave us, never to offer one.
 */
export const ROLE_LABEL = { ADMIN: 'Admin', USER: 'Member' };
export const ROLE_TONE = { ADMIN: 'clay', USER: 'slate' };

/* ── ticket handling configuration ──────────────────────────────────────── */

/**
 * cases.RoutingRule.STRATEGY_CHOICES. The model's own labels read like field
 * documentation ("Round-robin within target_assignees"); these read like the
 * sentence the rule performs, because they sit next to the target list that
 * completes them.
 */
export const ROUTING_STRATEGY_LABEL = {
  direct: 'Always to',
  round_robin: 'Round-robin between',
  least_busy: 'Whoever has fewest open, of',
  by_team: 'Anyone on'
};

/** Standalone names for the strategy select. `ROUTING_STRATEGY_LABEL` above
 *  is a sentence fragment that runs into the target names on the rule card,
 *  so it cannot double as an option label. */
export const ROUTING_STRATEGY_NAME = {
  direct: 'Direct',
  round_robin: 'Round robin',
  least_busy: 'Least busy',
  by_team: 'By team'
};

/** Fields a routing condition can test, in the words the rest of the app uses. */
export const CONDITION_FIELD_LABEL = {
  priority: 'Priority',
  case_type: 'Type',
  account: 'Account',
  tags: 'Tags',
  from_email_domain: 'Sender domain',
  mailbox_id: 'Mailbox'
};

export const CONDITION_OP_LABEL = {
  eq: 'is',
  in: 'is one of',
  contains: 'includes',
  regex: 'matches'
};

/** cases.EscalationPolicy.ACTION_CHOICES. */
export const ESCALATION_ACTION_LABEL = {
  notify: 'Notify',
  reassign: 'Reassign to',
  notify_and_reassign: 'Notify and reassign to'
};

/** cases.EscalationPolicy priorities, worst first. This is the display order
 *  the escalation page sorts by, not the model's `ordering = ("priority",)`,
 *  which sorts the CharField alphabetically and puts Low between High and
 *  Normal. Imported by both the page and `lib/server/v2/escalation.js`. */
export const ESCALATION_PRIORITIES = ['Urgent', 'High', 'Normal', 'Low'];

/** cases/workflow.py DEFAULT_FIRST_RESPONSE_SLA and DEFAULT_RESOLUTION_SLA, in
 *  hours. What a case gets when its org has not set a target of its own, so the
 *  settings form shows these as the placeholder behind an empty input rather
 *  than leaving "blank" looking like "no SLA at all". */
export const DEFAULT_SLA_HOURS = {
  Urgent: { first_response: 1, resolution: 4 },
  High: { first_response: 4, resolution: 24 },
  Normal: { first_response: 8, resolution: 48 },
  Low: { first_response: 24, resolution: 72 }
};

/** cases.InboundMailbox.PROVIDER_CHOICES, only SES ships today. */
export const MAILBOX_PROVIDER_LABEL = {
  ses: 'Amazon SES',
  mailgun: 'Mailgun',
  postmark: 'Postmark',
  imap: 'IMAP'
};

/* ── vocabulary ─────────────────────────────────────────────────────────── */

/** common.CustomFieldDefinition.FIELD_TYPE_CHOICES. */
export const FIELD_TYPE_LABEL = {
  text: 'Text',
  textarea: 'Long text',
  number: 'Number',
  dropdown: 'Dropdown',
  date: 'Date',
  checkbox: 'Checkbox'
};

/** The statuses a reopened ticket may come back as.
 *
 * `ReopenPolicySerializer.NON_TERMINAL_STATUSES` in
 * `backend/cases/serializer.py` is the authority and rejects anything else.
 * Reopening into a terminal status would close the ticket again on arrival,
 * so the model's own choice list, which also carries Closed, Rejected and
 * Duplicate, is not the list to offer.
 */
export const REOPEN_TO_STATUSES = ['New', 'Assigned', 'Pending'];

/** Plural forms for the target model, for reading in a heading. */
export const TARGET_MODEL_LABEL = {
  Account: 'Accounts',
  Case: 'Tickets',
  Contact: 'Contacts',
  Estimate: 'Estimates',
  Invoice: 'Invoices',
  Lead: 'Leads',
  Opportunity: 'Deals',
  RecurringInvoice: 'Recurring invoices',
  Task: 'Tasks'
};

export const MACRO_SCOPE_LABEL = { org: 'Everyone', personal: 'Just you' };

/* ── board ──────────────────────────────────────────────────────────────── */

/**
 * tasks.BoardTask.PRIORITY_CHOICES. Lowercase, and a different set from
 * TASK_PRIORITY (Low/Medium/High) and PRIORITY_TONE (Urgent/High/Normal/Low).
 * Three enums for one word across three models; keep them apart.
 */
export const BOARD_PRIORITY_LABEL = {
  low: 'Low',
  medium: 'Medium',
  high: 'High',
  urgent: 'Urgent'
};
export const BOARD_PRIORITY_TONE = {
  low: 'slate',
  medium: 'slate',
  high: 'clay',
  urgent: 'rust'
};

/** tasks.BoardMember.ROLE_CHOICES, board-local, unrelated to Profile.role. */
export const BOARD_ROLE_LABEL = { owner: 'Owner', admin: 'Admin', member: 'Member' };

/**
 * Option lists for the list-page filters. Values are what goes on the wire;
 * labels are derived where they differ.
 *
 * CASE_PRIORITIES is not TASK_PRIORITY. A Case is Low/Normal/High/Urgent and a
 * Task is Low/Medium/High: "Medium" is not a valid case priority and "Normal"
 * is not a valid task priority. See the note at TASK_STATUS above.
 */
export const CASE_PRIORITIES = ['Low', 'Normal', 'High', 'Urgent'];
export const CASE_TYPES = ['Question', 'Incident', 'Problem'];
export const CASE_STATUSES = ['New', 'Assigned', 'Pending', 'Closed', 'Rejected', 'Duplicate'];
export const INVOICE_STATUSES = [
  'Draft',
  'Sent',
  'Viewed',
  'Paid',
  'Partially_Paid',
  'Overdue',
  'Pending',
  'Cancelled'
];
export const ESTIMATE_STATUSES = ['Draft', 'Sent', 'Viewed', 'Accepted', 'Declined', 'Expired'];
export const DOCUMENT_STATUSES = ['active', 'inactive'];
