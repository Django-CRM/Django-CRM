/// Embeddable web forms, from `WebFormDetailSerializer` (issue #634).
///
/// A published web form is an endpoint anyone on the internet can post to, and
/// every accepted post writes a lead or a ticket into the org (the form's
/// `target`, chosen at creation and fixed once it has submissions). That is closer to a
/// credential than to a record, which is why every write here is admin-only
/// server-side and why this file is careful about three things in particular.
///
/// `is_published` NEVER GOES ON THE WIRE
/// It is read-only on the serializer, so sending it is silently ignored rather
/// than rejected, and a client that sent it would appear to publish while
/// publishing nothing. Publishing has its own endpoint, which validates the
/// source state (already-published is a 400, not a no-op) and the form's shape
/// (no email field, no publish).
///
/// `captcha_secret` IS WRITE-ONLY
/// The API never returns it, so it is absent from `fromJson` and omitted from
/// `toJson` unless somebody typed a new one. Emitting an empty string would
/// blank a working Turnstile secret on any unrelated save, and because the
/// value is never read back nothing would show that it had happened until the
/// next visitor was refused. Turnstile fails closed. `hasCaptchaSecret` is the
/// server's boolean for "one is stored", which is how a screen can tell an
/// empty box apart from a hidden value.
///
/// `order` IS THE SERVER'S TO ASSIGN
/// `WebFormDetailSerializer._write_fields` enumerates the rows it is given and
/// stamps `order` from list position, so the client's job is to send the list
/// in the right order rather than to send its own numbers. `toJson` therefore
/// omits `order` entirely.
library;

import 'lookup_models.dart';

/// One built-in field a web form may collect (a Lead column, or a ticket
/// field).
class WebFormFieldChoice {
  const WebFormFieldChoice(this.value, this.label);

  final String value;
  final String label;
}

/// Mirrored 1:1 from `backend/webforms/constants.py::LEAD_FIELD_CHOICES`, and
/// matching `WEBFORM_LEAD_FIELDS` in `frontend/src/lib/v2/webform-fields.js`.
///
/// A whitelist rather than "any field on Lead": the person filling this in is
/// an anonymous stranger, so which columns they can reach is a decision
/// somebody made. Assignment, pipeline stage, probability and deal value are
/// deliberately absent.
///
/// `title` and `salutation` are easy to confuse and mean different things.
/// `Lead.title` is the subject line ("Website enquiry"); `Lead.salutation` is
/// the honorific ("Ms", "Dr"). The labels say so.
///
/// Drift is contained rather than prevented: `WebFormFieldSerializer
/// .validate_lead_field` refuses anything outside the backend's own list, so a
/// stale entry here is a clean 400 rather than a bad write.
const List<WebFormFieldChoice> webFormLeadFields = [
  WebFormFieldChoice('salutation', 'Salutation'),
  WebFormFieldChoice('first_name', 'First name'),
  WebFormFieldChoice('last_name', 'Last name'),
  WebFormFieldChoice('email', 'Email'),
  WebFormFieldChoice('phone', 'Phone'),
  WebFormFieldChoice('company_name', 'Company name'),
  WebFormFieldChoice('job_title', 'Job title'),
  WebFormFieldChoice('website', 'Website'),
  WebFormFieldChoice('title', 'Subject'),
  WebFormFieldChoice('description', 'Message'),
  WebFormFieldChoice('city', 'City'),
  WebFormFieldChoice('state', 'State'),
  WebFormFieldChoice('country', 'Country'),
  WebFormFieldChoice('postcode', 'Postal code'),
  WebFormFieldChoice('industry', 'Industry'),
];

/// What a ticket form may collect, mirrored 1:1 from
/// `backend/webforms/constants.py::TICKET_FIELD_CHOICES` and matching
/// `WEBFORM_TICKET_FIELDS` on the web. `name` and `description` land on the
/// ticket; the rest describe the person and only reach a contact the
/// submission creates. Priority, type and assignment are the form's own
/// settings, never the visitor's.
const List<WebFormFieldChoice> webFormTicketFields = [
  WebFormFieldChoice('email', 'Email'),
  WebFormFieldChoice('first_name', 'First name'),
  WebFormFieldChoice('last_name', 'Last name'),
  WebFormFieldChoice('phone', 'Phone'),
  WebFormFieldChoice('company_name', 'Company name'),
  WebFormFieldChoice('name', 'Subject'),
  WebFormFieldChoice('description', 'Message'),
];

/// Case priorities and types, mirroring `PRIORITY_CHOICE` and `CASE_TYPE` in
/// `backend/common/utils.py`. The server validates both.
const List<String> ticketPriorities = ['Low', 'Normal', 'High', 'Urgent'];
const List<String> ticketTypes = ['Question', 'Incident', 'Problem'];

/// The one field a form must collect before it can be published, mirroring
/// `REQUIRED_LEAD_FIELD` and `REQUIRED_TICKET_FIELD` (both "email"). On a
/// lead form it is the key the submission service dedupes on; on a ticket
/// form it is how the ticket finds or creates its contact.
const String requiredLeadField = 'email';

/// The built-in whitelist for a form [target].
List<WebFormFieldChoice> builtinFieldsFor(String target) =>
    target == WebForm.targetTicket ? webFormTicketFields : webFormLeadFields;

/// The `CustomFieldDefinition.target_model` a form [target] may collect.
String customFieldModelFor(String target) =>
    target == WebForm.targetTicket ? 'Case' : 'Lead';

String builtinFieldLabel(String target, String value) {
  for (final field in builtinFieldsFor(target)) {
    if (field.value == value) return field.label;
  }
  return value;
}

/// Whether this list would survive `WebFormPublishView`'s check.
///
/// A display hint, so the screen can explain a disabled Publish button before
/// the round trip. The server runs the same check and is what decides. Only a
/// row of the form's own built-in source counts: the server refuses a lead row
/// on a ticket form, so it must not read as publishable here.
bool hasRequiredField(
  List<WebFormField> fields, {
  String target = WebForm.targetLead,
}) {
  final source = WebFormField.builtinSourceFor(target);
  return fields.any(
    (f) => f.source == source && f.builtinField == requiredLeadField,
  );
}

/// One ordered question on a form.
///
/// The row IS the field mapping: `leadField` names the Lead column a value
/// lands in, `ticketField` a ticket form's field, or `customField` a
/// CustomFieldDefinition whose value lands in the record's `custom_fields`.
/// Exactly one is set, and `source` says which. That is a serializer check and
/// a database `CheckConstraint` as well as [isComplete] here.
class WebFormField {
  const WebFormField({
    this.id,
    required this.source,
    this.leadField = '',
    this.ticketField = '',
    this.customField,
    this.label = '',
    this.placeholder = '',
    this.isRequired = false,
  });

  static const String sourceLead = 'lead';
  static const String sourceTicket = 'ticket';
  static const String sourceCustom = 'custom';

  /// The row source for a built-in field on a form of [target].
  static String builtinSourceFor(String target) =>
      target == WebForm.targetTicket ? sourceTicket : sourceLead;

  /// Null until the row has been saved. A row added on this screen has no id,
  /// and the whole list is replaced on every save, so nothing depends on one.
  final String? id;

  final String source;
  final String leadField;
  final String ticketField;
  final String? customField;
  final String label;
  final String placeholder;
  final bool isRequired;

  bool get isCustom => source == sourceCustom;

  /// The whitelist key this row names: its ticket field on a ticket row, its
  /// Lead column otherwise, and '' on a custom row.
  String get builtinField => switch (source) {
    sourceTicket => ticketField,
    sourceCustom => '',
    _ => leadField,
  };

  /// Whether the row is complete enough to save. Mirrors `isFieldComplete` in
  /// `frontend/src/lib/v2/webform-fields.js`, and behind both of them the
  /// `web_form_field_exactly_one_target` constraint.
  bool get isComplete {
    if (label.trim().isEmpty) return false;
    return isCustom
        ? (customField?.isNotEmpty ?? false)
        : builtinField.isNotEmpty;
  }

  /// The whitelist label of [builtinField], or the key itself when unknown.
  String get builtinLabel => builtinFieldLabel(
    source == sourceTicket ? WebForm.targetTicket : WebForm.targetLead,
    builtinField,
  );

  /// What the visitor sees, falling back to the target's own name so a row
  /// mid-edit still reads as something.
  String get displayLabel => label.trim().isNotEmpty ? label : builtinLabel;

  factory WebFormField.fromJson(Map<String, dynamic> json) {
    return WebFormField(
      id: json['id']?.toString(),
      source: json['source']?.toString() ?? sourceLead,
      leadField: json['lead_field']?.toString() ?? '',
      ticketField: json['ticket_field']?.toString() ?? '',
      customField: json['custom_field']?.toString(),
      label: json['label'] as String? ?? '',
      placeholder: json['placeholder'] as String? ?? '',
      isRequired: json['is_required'] as bool? ?? false,
    );
  }

  /// Exactly the keys `WebFormFieldSerializer` accepts.
  ///
  /// `order` is absent: the server assigns it from list position (see the
  /// library doc-comment), so including it would suggest a client could
  /// reorder a form by sending its own numbers. `id` is read-only.
  Map<String, dynamic> toJson() {
    return {
      'source': source,
      'lead_field': source == sourceLead ? leadField : '',
      'ticket_field': source == sourceTicket ? ticketField : '',
      'custom_field': isCustom ? customField : null,
      'label': label,
      'placeholder': placeholder,
      'is_required': isRequired,
    };
  }

  WebFormField copyWith({
    String? source,
    String? leadField,
    String? ticketField,
    String? customField,
    bool clearCustomField = false,
    String? label,
    String? placeholder,
    bool? isRequired,
  }) {
    return WebFormField(
      id: id,
      source: source ?? this.source,
      leadField: leadField ?? this.leadField,
      ticketField: ticketField ?? this.ticketField,
      customField: clearCustomField ? null : (customField ?? this.customField),
      label: label ?? this.label,
      placeholder: placeholder ?? this.placeholder,
      isRequired: isRequired ?? this.isRequired,
    );
  }
}

/// One embeddable form.
class WebForm {
  const WebForm({
    required this.id,
    this.name = '',
    this.target = targetLead,
    this.isPublished = false,
    this.allowedOrigins = const [],
    this.submitButtonLabel = 'Submit',
    this.successMode = successMessage,
    this.successMessageText = '',
    this.redirectUrl = '',
    this.assignTo,
    this.storedAssignee,
    this.notifyProfiles = const [],
    this.leadSource = 'other',
    this.tags = const [],
    this.ticketPriority = 'Normal',
    this.ticketType = '',
    this.captchaProvider = '',
    this.captchaSiteKey = '',
    this.hasCaptchaSecret = false,
    this.rejectDisposableEmail = true,
    this.embedHtml = '',
    this.embedJs = '',
    this.fields = const [],
    this.submissionCount = 0,
    this.fieldCount = 0,
    this.createdAt,
  });

  static const String targetLead = 'lead';
  static const String targetTicket = 'ticket';
  static const String successMessage = 'message';
  static const String successRedirect = 'redirect';
  static const String captchaNone = '';
  static const String captchaTurnstile = 'turnstile';

  final String id;
  final String name;

  /// What an accepted submission creates: [targetLead] or [targetTicket].
  /// Chosen when the form is created and never sent on an update, because the
  /// server refuses to change it once the form has submissions.
  final String target;

  final bool isPublished;
  final List<String> allowedOrigins;
  final String submitButtonLabel;
  final String successMode;

  /// Named `successMessageText` because `successMessage` is the mode constant.
  /// Two things called `successMessage` on one class is how the wrong one gets
  /// rendered into a form a customer sees.
  final String successMessageText;

  final String redirectUrl;
  final String? assignTo;

  /// Who the stored `assign_to` is, from `assign_to_details`, and whether they
  /// are still active. The people picker lists active members only, so this
  /// is how the editor can still offer a deactivated assignee rather than
  /// dropping them on the next save. Describes the stored value, not the
  /// draft, so `copyWith` carries it through unchanged.
  final UserLookup? storedAssignee;

  final List<String> notifyProfiles;
  final String leadSource;
  final List<String> tags;

  /// Ticket forms only: the priority and type every ticket is opened with.
  /// Set by the form, never by the visitor. An empty [ticketType] is "no
  /// type".
  final String ticketPriority;
  final String ticketType;

  final String captchaProvider;
  final String captchaSiteKey;

  /// Whether a secret is stored, never which one. See the library comment.
  final bool hasCaptchaSecret;

  final bool rejectDisposableEmail;

  /// Built server-side, because they need the API's own base URL and this app
  /// only knows the one it was configured with. Never assembled here.
  final String embedHtml;
  final String embedJs;

  final List<WebFormField> fields;

  /// Present on the list payload, absent on the detail one.
  final int submissionCount;
  final int fieldCount;

  final DateTime? createdAt;

  bool get isTicket => target == targetTicket;
  bool get usesTurnstile => captchaProvider == captchaTurnstile;
  bool get redirectsOnSuccess => successMode == successRedirect;

  /// What stops this form being published, or null when nothing does.
  ///
  /// Kept close to `WebFormPublishView`'s own wording so the screen and the
  /// server never read like different rules. The server is what decides.
  String? get publishBlocker {
    if (fields.isEmpty) return 'Add at least one field first.';
    if (!hasRequiredField(fields, target: target)) {
      return isTicket
          ? 'Add an email field before publishing. It is how each ticket '
                'finds its contact, or creates one.'
          : 'Add an email field before publishing. It is what lets a repeat '
                'submission update the existing lead instead of failing.';
    }
    if (!fields.every((f) => f.isComplete)) {
      return 'Every field needs a label and something to write into.';
    }
    if (redirectsOnSuccess && redirectUrl.isEmpty) {
      return 'This form redirects on success but has no redirect URL set.';
    }
    return null;
  }

  static List<String> _stringList(dynamic value) {
    if (value is! List) return const [];
    return value.map((v) => v.toString()).toList(growable: false);
  }

  factory WebForm.fromJson(Map<String, dynamic> json) {
    final rawFields = json['fields'];
    final fields = <WebFormField>[];
    if (rawFields is List) {
      final rows = rawFields.whereType<Map<String, dynamic>>().toList();
      // Sorted rather than trusted. The API orders by `order` already, so this
      // is a no-op on every response it actually sends; it is here because the
      // order IS the form, and a list that silently arrived shuffled would ask
      // a visitor for their message before their email.
      rows.sort((a, b) {
        final left = a['order'];
        final right = b['order'];
        return (left is int ? left : 0).compareTo(right is int ? right : 0);
      });
      fields.addAll(rows.map(WebFormField.fromJson));
    }

    return WebForm(
      id: json['id']?.toString() ?? '',
      name: json['name'] as String? ?? '',
      target: json['target']?.toString() ?? targetLead,
      isPublished: json['is_published'] as bool? ?? false,
      allowedOrigins: _stringList(json['allowed_origins']),
      submitButtonLabel: json['submit_button_label'] as String? ?? 'Submit',
      successMode: json['success_mode']?.toString() ?? successMessage,
      successMessageText: json['success_message'] as String? ?? '',
      redirectUrl: json['redirect_url'] as String? ?? '',
      assignTo: json['assign_to']?.toString(),
      storedAssignee: json['assign_to_details'] is Map
          ? UserLookup.fromJson(
              (json['assign_to_details'] as Map).cast<String, dynamic>(),
            )
          : null,
      notifyProfiles: _stringList(json['notify_profiles']),
      leadSource: json['lead_source']?.toString() ?? 'other',
      tags: _stringList(json['tags']),
      ticketPriority: json['ticket_priority']?.toString() ?? 'Normal',
      ticketType: json['ticket_type']?.toString() ?? '',
      captchaProvider: json['captcha_provider']?.toString() ?? '',
      captchaSiteKey: json['captcha_site_key'] as String? ?? '',
      hasCaptchaSecret: json['has_captcha_secret'] as bool? ?? false,
      rejectDisposableEmail: json['reject_disposable_email'] as bool? ?? true,
      embedHtml: json['embed_html'] as String? ?? '',
      embedJs: json['embed_js'] as String? ?? '',
      fields: List.unmodifiable(fields),
      submissionCount: json['submission_count'] as int? ?? 0,
      fieldCount: json['field_count'] as int? ?? fields.length,
      createdAt: DateTime.tryParse(json['created_at']?.toString() ?? ''),
    );
  }

  /// The update payload.
  ///
  /// `is_published` and `has_captcha_secret` are absent because both are
  /// read-only server-side; `captcha_secret` appears only when [captchaSecret]
  /// carries something somebody typed. See the library doc-comment for why
  /// each of those matters.
  Map<String, dynamic> toJson({String? captchaSecret}) {
    final payload = <String, dynamic>{
      'name': name,
      'allowed_origins': allowedOrigins,
      'submit_button_label': submitButtonLabel,
      'success_mode': successMode,
      'success_message': successMessageText,
      'redirect_url': redirectUrl,
      'assign_to': assignTo,
      'notify_profiles': notifyProfiles,
      'lead_source': leadSource,
      'tags': tags,
      'ticket_priority': ticketPriority,
      'ticket_type': ticketType,
      'captcha_provider': captchaProvider,
      'captcha_site_key': captchaSiteKey,
      'reject_disposable_email': rejectDisposableEmail,
      'fields': fields.map((f) => f.toJson()).toList(growable: false),
    };
    if (captchaSecret != null && captchaSecret.trim().isNotEmpty) {
      payload['captcha_secret'] = captchaSecret.trim();
    }
    return payload;
  }

  WebForm copyWith({
    String? name,
    List<String>? allowedOrigins,
    String? submitButtonLabel,
    String? successMode,
    String? successMessageText,
    String? redirectUrl,
    String? assignTo,
    bool clearAssignTo = false,
    List<String>? notifyProfiles,
    String? leadSource,
    List<String>? tags,
    String? ticketPriority,
    String? ticketType,
    String? captchaProvider,
    String? captchaSiteKey,
    bool? rejectDisposableEmail,
    List<WebFormField>? fields,
  }) {
    return WebForm(
      id: id,
      name: name ?? this.name,
      target: target,
      isPublished: isPublished,
      allowedOrigins: allowedOrigins ?? this.allowedOrigins,
      submitButtonLabel: submitButtonLabel ?? this.submitButtonLabel,
      successMode: successMode ?? this.successMode,
      successMessageText: successMessageText ?? this.successMessageText,
      redirectUrl: redirectUrl ?? this.redirectUrl,
      assignTo: clearAssignTo ? null : (assignTo ?? this.assignTo),
      storedAssignee: storedAssignee,
      notifyProfiles: notifyProfiles ?? this.notifyProfiles,
      leadSource: leadSource ?? this.leadSource,
      tags: tags ?? this.tags,
      ticketPriority: ticketPriority ?? this.ticketPriority,
      ticketType: ticketType ?? this.ticketType,
      captchaProvider: captchaProvider ?? this.captchaProvider,
      captchaSiteKey: captchaSiteKey ?? this.captchaSiteKey,
      hasCaptchaSecret: hasCaptchaSecret,
      rejectDisposableEmail:
          rejectDisposableEmail ?? this.rejectDisposableEmail,
      embedHtml: embedHtml,
      embedJs: embedJs,
      fields: fields ?? this.fields,
      submissionCount: submissionCount,
      fieldCount: fieldCount,
      createdAt: createdAt,
    );
  }
}

/// One attempt at a form, accepted or refused.
///
/// `reject_reason` is deliberately absent from the API's response and so has
/// no field here: naming the control that caught a bot is how the next bot
/// gets past it. The status is what a person acts on.
class WebFormSubmission {
  const WebFormSubmission({
    required this.id,
    this.status = '',
    this.leadId,
    this.leadName,
    this.caseId,
    this.caseName,
    this.submittedIp,
    this.referer = '',
    this.createdAt,
  });

  static const String accepted = 'accepted';
  static const String acceptedDuplicate = 'accepted_duplicate';

  final String id;
  final String status;
  final String? leadId;
  final String? leadName;

  /// The ticket a ticket form's accepted submission opened. Never set beside
  /// [leadId]; the server enforces that with a check constraint.
  final String? caseId;
  final String? caseName;
  final String? submittedIp;
  final String referer;
  final DateTime? createdAt;

  /// A merged duplicate counts: a returning visitor whose details filled in
  /// blanks on an existing lead reached the org just as surely as a new one.
  bool get isAccepted => status == accepted || status == acceptedDuplicate;

  String get statusLabel => switch (status) {
    accepted => caseId != null ? 'Ticket opened' : 'Lead created',
    acceptedDuplicate => 'Merged into an existing lead',
    'rejected_spam' => 'Rejected as spam',
    'rejected_invalid' => 'Rejected, invalid',
    'rejected_captcha' => 'Rejected, captcha',
    _ => status,
  };

  factory WebFormSubmission.fromJson(Map<String, dynamic> json) {
    final lead = json['lead']?.toString();
    final ticket = json['case']?.toString();
    return WebFormSubmission(
      id: json['id']?.toString() ?? '',
      status: json['status']?.toString() ?? '',
      leadId: (lead == null || lead.isEmpty) ? null : lead,
      leadName: json['lead_name'] as String?,
      caseId: (ticket == null || ticket.isEmpty) ? null : ticket,
      caseName: json['case_name'] as String?,
      submittedIp: json['submitted_ip'] as String?,
      referer: json['referer'] as String? ?? '',
      createdAt: DateTime.tryParse(json['created_at']?.toString() ?? ''),
    );
  }
}

/// Views, submissions and conversion over the API's fixed trailing 30 days.
///
/// The window is not a parameter here because it is not one on the API either.
class WebFormAnalytics {
  const WebFormAnalytics({
    this.views = 0,
    this.submissions = 0,
    this.spam = 0,
    this.conversionRate = 0,
    this.windowDays = 30,
  });

  final int views;
  final int submissions;
  final int spam;
  final double conversionRate;
  final int windowDays;

  factory WebFormAnalytics.fromJson(Map<String, dynamic> json) {
    final totals = json['totals'];
    final t = totals is Map<String, dynamic> ? totals : const {};
    final rate = t['conversion_rate'];
    return WebFormAnalytics(
      views: t['views'] as int? ?? 0,
      submissions: t['submissions'] as int? ?? 0,
      spam: t['spam'] as int? ?? 0,
      conversionRate: rate is num ? rate.toDouble() : 0,
      windowDays: json['window_days'] as int? ?? 30,
    );
  }
}

/// The org-wide counts the list endpoint returns.
///
/// Read from the server rather than counted off the page: the list is
/// paginated, so counting rows would be right until the org's eleventh form
/// and quietly wrong afterwards.
class WebFormTotals {
  const WebFormTotals({
    this.count = 0,
    this.published = 0,
    this.submissions30d = 0,
    this.spam30d = 0,
  });

  final int count;
  final int published;
  final int submissions30d;
  final int spam30d;

  int get drafts => count - published < 0 ? 0 : count - published;

  factory WebFormTotals.fromJson(Map<String, dynamic>? json) {
    if (json == null) return const WebFormTotals();
    return WebFormTotals(
      count: json['count'] as int? ?? 0,
      published: json['published'] as int? ?? 0,
      submissions30d: json['submissions_30d'] as int? ?? 0,
      spam30d: json['spam_30d'] as int? ?? 0,
    );
  }
}
