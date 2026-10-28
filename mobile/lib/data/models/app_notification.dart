import '../../config/api_config.dart';
import '../../routes/app_routes.dart';
import '../../routes/record_routes.dart';
import '../../services/run_time.dart';
import '../api/schema.g.dart' as api;
import 'project.dart' show monthLabel;

/// An in-app notification, as `/api/notifications/` returns it.
///
/// Named `AppNotification` rather than `Notification` because Flutter already
/// has a `Notification` class in widgets, and the collision is silent: the
/// wrong one resolves and the error lands somewhere else entirely.
class AppNotification {
  const AppNotification({
    required this.id,
    required this.verb,
    this.actorName,
    this.entityName,
    this.commentExcerpt,
    this.link,
    this.readAt,
    required this.createdAt,
    this.message,
    this.ruleName,
    this.reason,
    this.ruleNames,
    this.windowStart,
    this.budgetKind,
    this.budgetPercent,
    this.projectName,
    this.estimateNumber,
    this.clientName,
    this.startedProjectId,
    this.depositInvoiceId,
    this.retainerMonth,
    this.invoiceNumber,
    this.leadSource,
  });

  final String id;

  /// Dotted identifier, e.g. `case.mentioned`. Only values in [producedVerbs]
  /// are dispatched by backend code today.
  final String verb;

  /// Null for a notification the system raised rather than a person.
  final String? actorName;
  final String? entityName;

  /// Somebody else's comment body, truncated server-side. Rendered as text.
  final String? commentExcerpt;

  /// The stored client path. Never navigated to as-is: see [ticketId].
  final String? link;
  final DateTime? readAt;
  final DateTime createdAt;

  /// From `data`, for the automation verbs; each is null when absent or not
  /// the type the API sends.
  final String? message;
  final String? ruleName;
  final String? reason;
  final List<String>? ruleNames;
  final DateTime? windowStart;

  /// From `data`, for `project.budget`: `hours` or `amount`, 80 or 100, and
  /// the project's name.
  final String? budgetKind;
  final int? budgetPercent;
  final String? projectName;

  /// From `data`, for `estimate.accepted` and `estimate.declined`: the
  /// estimate, the name the client typed (or the estimate's client), and on
  /// acceptance what the server made from it.
  final String? estimateNumber;
  final String? clientName;
  final String? startedProjectId;
  final String? depositInvoiceId;

  /// From `data`, for `project.retainer_invoice`: the month (`YYYY-MM`) and
  /// the invoice's number. The project's name is [projectName].
  final String? retainerMonth;
  final String? invoiceNumber;

  /// From `data`, for the `lead.*` verbs: the lead's source label.
  final String? leadSource;

  bool get isUnread => readAt == null;
  String get displayActorName => verb.startsWith('support.')
      ? 'BottleCRM Support'
      : actorName ?? 'The system';

  /// Whether anything in the backend actually produces this verb. A row with
  /// an unknown verb still renders, as a readable sentence, so a new producer
  /// shipping before its copy does not show a raw dotted identifier.
  bool get isKnownVerb => producedVerbs.contains(verb);

  /// The CRM ticket this notification points at, or null.
  ///
  /// The stored `link` is a value from the database, so it is parsed rather
  /// than followed. Only `/cases/<id>` and `/tickets/<id>` resolve here, and
  /// both identify the same CRM ticket. Product-support destinations are
  /// rebuilt separately in [destinationPath]. Anything else, including an
  /// absolute URL or a `javascript:` URL, yields no tappable destination.
  String? get ticketId {
    final value = link;
    if (value == null) return null;
    final match = RegExp(r'^/(?:cases|tickets)/([^/?#]+)/?$').firstMatch(value);
    return match?.group(1);
  }

  /// A safe in-app destination for the notification, or null.
  ///
  /// Product support resolves to `/help/<id>`. The `/support/<id>` spelling is
  /// accepted too, because that is what the producer wrote before the page was
  /// renamed, for the same reason `/cases/` is still accepted above. The
  /// automation verbs' web paths (a record, or the rules) and an estimate
  /// answer's (a project, an invoice or the estimates list) map to their
  /// screens only with a UUID id and a valid object key.
  String? get destinationPath => destinationFor(link);

  /// [destinationPath] for a bare link, which is also what a push carries.
  static String? destinationFor(String? value) {
    if (value == null) return null;
    final ticket = RegExp(
      r'^/(?:cases|tickets)/([^/?#]+)/?$',
    ).firstMatch(value)?.group(1);
    if (ticket != null) return '/tickets/$ticket';
    final help = RegExp(r'^/(?:support|help)/([^/?#]+)/?$').firstMatch(value);
    if (help != null) return '/help/${help.group(1)}';
    // Exactly the paths the engine writes, as the web's `resolvedLink` reads
    // them: no trailing slash, no case folding.
    if (value == '/settings/automation') return AppRoutes.settingsAutomation;
    if (value == '/invoices/estimates') return AppRoutes.estimates;
    final invoice = RegExp(
      r'^/invoices/([^/?#]+)$',
    ).firstMatch(value)?.group(1);
    if (invoice != null) {
      return ApiConfig.isUuid(invoice)
          ? '${AppRoutes.invoices}/$invoice'
          : null;
    }
    final rule = RegExp(
      r'^/settings/automation/([^/?#]+)$',
    ).firstMatch(value)?.group(1);
    if (rule != null) {
      return ApiConfig.isUuid(rule)
          ? AppRoutes.settingsAutomationRule(rule)
          : null;
    }
    final custom = RegExp(r'^/objects/([^/?#]+)/([^/?#]+)$').firstMatch(value);
    if (custom != null) {
      return _record('obj:${custom.group(1)}', custom.group(2)!);
    }
    final record = RegExp(
      r'^/(leads|contacts|accounts|pipeline|tasks|projects)/([^/?#]+)$',
    ).firstMatch(value);
    if (record == null) return null;
    return _record(_recordKinds[record.group(1)]!, record.group(2)!);
  }

  /// The feed's actor is a `CommentUser`: `{id, user_details: {email,
  /// profile_pic}}`, with no name.
  static String _actorName(api.CommentUser actor) {
    final email = actor.userDetails?.email ?? '';
    return email.isNotEmpty ? email : 'Someone';
  }

  static String? _record(String kind, String id) =>
      ApiConfig.isUuid(id) ? linkedRecordRoute(kind, id) : null;

  static const _recordKinds = {
    'leads': 'lead',
    'contacts': 'contact',
    'accounts': 'account',
    'pipeline': 'deal',
    'tasks': 'task',
    'projects': 'project',
  };

  /// The automation verbs' sentence, the web's `notificationText`, or null
  /// for any other verb or for `data` of the wrong type. [zone] is the org's
  /// timezone; null reads it from the session.
  String? automationText({String? zone, DateTime? now}) {
    switch (verb) {
      case 'automation.message':
        return ruleName == null ? null : message;
      case 'automation.failed':
        final rule = ruleName, why = reason;
        if (rule == null || why == null) return null;
        return 'The rule "$rule" failed: $why';
      case 'automation.paused':
        final names = ruleNames, why = reason;
        if (names == null || why == null) return null;
        return '$why Paused: ${names.join(', ')}.';
      case 'automation.limit':
        final at = windowStart;
        if (at == null) return null;
        final from = runTimeLabel(at, zone: zone, now: now).replaceFirstMapped(
          RegExp(r'^(Today|Yesterday) '),
          (m) => m[0]!.toLowerCase(),
        );
        return 'Automation reached its limit of 1,000 runs in the hour from '
            '$from. '
            "The rest of that hour's runs were skipped.";
    }
    return null;
  }

  /// The `project.budget` sentence, as the web's `notificationText` writes
  /// it, or null for any other verb or for `data` of the wrong type.
  String? get budgetText {
    if (verb != 'project.budget') return null;
    final name = projectName, kind = budgetKind, percent = budgetPercent;
    if (name == null || name.trim().isEmpty) return null;
    if (percent == null || percent <= 0) return null;
    if (kind != 'hours' && kind != 'amount') return null;
    final used = percent >= 100 ? 'all of its' : '$percent% of its';
    return '$name has used $used $kind budget';
  }

  /// The sentence for a client's answer to an estimate, as the web's
  /// `estimateNotificationText` writes it, or null for any other verb or without the
  /// estimate's number.
  String? get estimateText {
    final accepted = verb == 'estimate.accepted';
    if (!accepted && verb != 'estimate.declined') return null;
    final number = estimateNumber?.trim() ?? '';
    if (number.isEmpty) return null;
    final client = clientName?.trim() ?? '';
    final who = client.isEmpty ? 'The client' : client;
    if (!accepted) return '$who declined estimate $number';
    return [
      '$who accepted estimate $number',
      if ((startedProjectId ?? '').isNotEmpty) 'project started',
      if ((depositInvoiceId ?? '').isNotEmpty) 'deposit invoice drafted',
    ].join(', ');
  }

  /// The `project.retainer_invoice` sentence, as the web writes it, or null
  /// for any other verb or for `data` of the wrong type.
  String? get retainerText {
    if (verb != 'project.retainer_invoice') return null;
    final number = invoiceNumber?.trim() ?? '';
    final name = projectName?.trim() ?? '';
    final month = monthLabel(retainerMonth);
    if (number.isEmpty || name.isEmpty || month == null) return null;
    return 'Retainer invoice $number for $month drafted for $name';
  }

  /// The line under a message naming the rule that sent it.
  String? get automationSource =>
      verb == 'automation.message' && automationText() != null
      ? 'From the rule "$ruleName"'
      : null;

  /// The sentence a lead alert reads as, or null for any other verb. The
  /// name is the row's own; the source label is `data.source`, absent when
  /// the lead came from nowhere in particular.
  String? get leadText {
    final name = entityName ?? 'a lead';
    final from = (leadSource ?? '').isEmpty ? '' : ' ($leadSource)';
    return switch (verb) {
      'lead.assigned' => 'New lead assigned: $name$from',
      'lead.created' => 'New lead: $name$from',
      'lead.enquired' => '$name enquired again$from',
      'lead.waiting' => '$name is waiting for a reply',
      _ => null,
    };
  }

  /// "mentioned you on" / "commented on" for the verbs that exist, and a
  /// readable fallback for the ones that do not: `case.sla_breached` reads as
  /// "sla breached" rather than as its identifier.
  String get verbPhrase {
    if (verb == 'case.mentioned') return 'mentioned you on';
    if (verb == 'case.commented') return 'commented on';
    if (verb == 'support.replied') return 'replied to';
    if (verb == 'support.status_changed') return 'updated';
    return verb.replaceFirst(RegExp(r'^[^.]+\.'), '').replaceAll('_', ' ');
  }

  /// Reads a `Notification`. Its `data` is free-form, so each key is checked
  /// for the type the producer writes.
  factory AppNotification.fromJson(Map<String, dynamic> json) {
    final w = api.Notification.fromJson(json);
    final actor = w.actor;
    final data = w.data;
    String? text(String key) =>
        data != null && data[key] is String ? data[key] as String : null;
    final rules = data?['rules'];
    final windowStart = text('window_start');
    final percent = data?['percent'];
    return AppNotification(
      id: w.id ?? '',
      verb: w.verb ?? '',
      actorName: actor == null ? null : _actorName(actor),
      entityName: w.entityName,
      commentExcerpt: text('comment_excerpt'),
      link: w.link,
      readAt: DateTime.tryParse(w.readAt ?? ''),
      createdAt: DateTime.tryParse(w.createdAt ?? '') ?? DateTime.now(),
      message: text('message'),
      ruleName: text('rule'),
      reason: text('reason'),
      ruleNames: rules is List && rules.every((r) => r is String)
          ? List<String>.unmodifiable(rules)
          : null,
      windowStart: windowStart == null ? null : DateTime.tryParse(windowStart),
      budgetKind: text('kind'),
      budgetPercent: percent is num && percent.isFinite
          ? percent.round()
          : null,
      projectName: text('project'),
      estimateNumber: text('estimate_number'),
      clientName: text('client_name'),
      startedProjectId: text('project_id'),
      depositInvoiceId: text('deposit_invoice_id'),
      retainerMonth: text('month'),
      invoiceNumber: text('invoice_number'),
      leadSource: text('source'),
    );
  }

  AppNotification copyWith({DateTime? readAt}) => AppNotification(
    id: id,
    verb: verb,
    actorName: actorName,
    entityName: entityName,
    commentExcerpt: commentExcerpt,
    link: link,
    readAt: readAt ?? this.readAt,
    createdAt: createdAt,
    message: message,
    ruleName: ruleName,
    reason: reason,
    ruleNames: ruleNames,
    windowStart: windowStart,
    budgetKind: budgetKind,
    budgetPercent: budgetPercent,
    projectName: projectName,
    estimateNumber: estimateNumber,
    clientName: clientName,
    startedProjectId: startedProjectId,
    depositInvoiceId: depositInvoiceId,
    retainerMonth: retainerMonth,
    invoiceNumber: invoiceNumber,
    leadSource: leadSource,
  );
}

/// Verbs the backend dispatches. CRM tickets, platform support, automation
/// rules, project budgets, clients answering estimates and lead alerts are the
/// current producers. Anything else is rendered plainly rather than given copy for a
/// feature that does not exist.
const List<String> producedVerbs = [
  'case.mentioned',
  'case.commented',
  'support.replied',
  'support.status_changed',
  'automation.message',
  'automation.failed',
  'automation.paused',
  'automation.limit',
  'project.budget',
  'estimate.accepted',
  'estimate.declined',
  'project.retainer_invoice',
  'lead.assigned',
  'lead.created',
  'lead.enquired',
  'lead.waiting',
];
