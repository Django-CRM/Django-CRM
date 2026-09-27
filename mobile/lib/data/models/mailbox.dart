/// Inbound email addresses, the ones that turn mail into tickets.
///
/// **No secrets here.** `InboundMailbox` carries a `webhook_secret` column,
/// write-only server-side, so it never arrives and this model has no field for
/// it. The `topic_arn` pin is not a secret but embeds the AWS account id, so the
/// server sends it to admins only; [Mailbox.topicArn] holds it for the admin's
/// form. Every viewer gets [Mailbox.hasTopicArn], the boolean that says whether
/// the address is connected.
///
/// `frontend/src/routes/(app)/settings/inbound-email/delivery.js` carries the
/// same rules.
library;

import 'lookup_models.dart';

/// What an address actually does with mail, as opposed to what its switch says.
///
/// `isActive` is one of three gates, and the settings pages used to draw it as
/// the only one. Two states read as working and are silent:
///
/// - [unsupported]: `InboundMailboxWebhookView.post` answers 501 for every
///   provider except SES, and the model offers four.
/// - [unconfirmed]: an SES address with no SNS topic pin rejects every
///   message. It gets one when an admin enters the Topic ARN, or when a
///   verified SubscriptionConfirmation arrives from a topic in an AWS account
///   the server allows (`INBOUND_SNS_ACCOUNT_IDS`); until then it is here.
///
/// Ordered as the webhook checks them, so this answers "which gate does a
/// delivery fail first". None of it is a security control: every gate is
/// enforced in `cases/inbound_views.py`.
enum MailboxDelivery {
  off('Creating nothing'),
  unsupported('Provider not wired up'),
  unconfirmed('Not connected yet'),
  live('Creating tickets');

  const MailboxDelivery(this.label);

  final String label;

  bool get isLive => this == MailboxDelivery.live;
}

/// The providers the webhook actually handles. Everything else 501s.
const List<String> supportedMailboxProviders = ['ses'];

/// `InboundMailbox.PROVIDER_CHOICES`, in the model's order.
const Map<String, String> mailboxProviderLabels = {
  'ses': 'AWS SES',
  'mailgun': 'Mailgun',
  'postmark': 'Postmark',
  'imap': 'IMAP',
};

/// The label a provider picker shows.
///
/// The three unimplemented providers stay selectable: the column accepts them,
/// an admin may be recording an address before the integration exists, and
/// hiding them would silently rewrite a mailbox already set to one.
String mailboxProviderLabel(String provider) {
  final label = mailboxProviderLabels[provider] ?? provider;
  return supportedMailboxProviders.contains(provider)
      ? label
      : '$label (not wired up yet)';
}

class Mailbox {
  const Mailbox({
    required this.id,
    this.address = '',
    this.provider = 'ses',
    this.isActive = true,
    this.hasTopicArn = false,
    this.topicArn,
    this.defaultPriority = 'Normal',
    this.defaultCaseType,
    this.defaultAssignee,
    this.casesLast30d = 0,
    this.lastReceivedAt,
  });

  final String id;
  final String address;
  final String provider;
  final bool isActive;

  /// Whether the mailbox has its SNS topic pin. **Defaults to false**, which
  /// reads as "not connected yet": failing closed is the safe direction for the
  /// flag a green pill is drawn from.
  final bool hasTopicArn;

  /// The pin itself, which the server sends to admins only. Null for a member,
  /// and for an admin whose mailbox has none (the server sends `""`).
  final String? topicArn;

  final String defaultPriority;

  /// Null means the ticket opens with no type set, which is a real choice.
  final String? defaultCaseType;

  /// Whoever new tickets from this address land on before routing runs. Null is
  /// "unassigned, then routed".
  final UserLookup? defaultAssignee;

  /// Server-computed from the `EmailMessage.mailbox` FK, so it is attributed
  /// rather than guessed.
  final int casesLast30d;

  final DateTime? lastReceivedAt;

  factory Mailbox.fromJson(Map<String, dynamic> json) {
    final assignee = json['default_assignee'];
    return Mailbox(
      id: json['id']?.toString() ?? '',
      address: json['address']?.toString() ?? '',
      provider: json['provider']?.toString() ?? 'ses',
      isActive: json['is_active'] as bool? ?? true,
      hasTopicArn: json['has_topic_arn'] == true,
      topicArn: json['topic_arn'] is String && json['topic_arn'] != ''
          ? json['topic_arn'] as String
          : null,
      defaultPriority: json['default_priority']?.toString() ?? 'Normal',
      defaultCaseType:
          (json['default_case_type'] as String?)?.trim().isEmpty ?? true
          ? null
          : json['default_case_type'] as String,
      defaultAssignee: assignee is Map
          ? UserLookup.fromJson(assignee.cast<String, dynamic>())
          : null,
      casesLast30d: (json['cases_last_30d'] as num?)?.round() ?? 0,
      lastReceivedAt: json['last_received_at'] == null
          ? null
          : DateTime.tryParse(json['last_received_at'].toString())?.toLocal(),
    );
  }

  /// The first gate mail to this address would fail, or [MailboxDelivery.live].
  MailboxDelivery get delivery {
    if (!isActive) return MailboxDelivery.off;
    if (!supportedMailboxProviders.contains(provider)) {
      return MailboxDelivery.unsupported;
    }
    if (!hasTopicArn) return MailboxDelivery.unconfirmed;
    return MailboxDelivery.live;
  }

  /// Why nothing is arriving, or null when something is.
  String? get deliveryExplanation {
    switch (delivery) {
      case MailboxDelivery.off:
        return 'Mail still arrives here and no ticket is opened. Nothing '
            'bounces, so whoever wrote gets silence rather than an error.';
      case MailboxDelivery.unsupported:
        final label = mailboxProviderLabels[provider] ?? provider;
        return '$label deliveries are not implemented. The webhook refuses '
            'them, so mail to this address becomes nothing whatever else is '
            'set here. Only AWS SES is wired up.';
      case MailboxDelivery.unconfirmed:
        return 'Waiting for its SNS topic. Every message is refused until '
            'this address has a Topic ARN: an admin can enter it here, or it is '
            'set when AWS confirms a subscription from an AWS account this '
            'server allows.';
      case MailboxDelivery.live:
        return null;
    }
  }

  /// What a ticket from here starts out as, in one line.
  String get opensAs {
    final parts = <String>[defaultPriority];
    if (defaultCaseType != null) parts.add(defaultCaseType!);
    parts.add(
      defaultAssignee == null
          ? 'then routed'
          : 'assigned to ${defaultAssignee!.displayName}',
    );
    return parts.join(' · ');
  }
}

/// The addresses that would actually open a ticket right now.
///
/// Derived from the rows rather than from the server's `totals.active`, which
/// counts `is_active` alone. `GET /cases/mailboxes/` returns every mailbox in
/// the org unfiltered and unpaginated, so this counts the same set and answers
/// the question the header asks.
int deliveringMailboxCount(List<Mailbox> mailboxes) =>
    mailboxes.where((m) => m.delivery.isLive).length;

/// The rows worth a banner: switched on, and creating nothing.
///
/// A turned-off address is left out on purpose. Somebody chose that, and it is
/// the one state of the three the row already read correctly.
List<Mailbox> silentMailboxes(List<Mailbox> mailboxes) => mailboxes
    .where(
      (m) =>
          m.delivery == MailboxDelivery.unsupported ||
          m.delivery == MailboxDelivery.unconfirmed,
    )
    .toList(growable: false);

/// Live addresses first, then alphabetically, which is how an admin scans.
List<Mailbox> sortedMailboxes(List<Mailbox> mailboxes) {
  final out = [...mailboxes];
  out.sort((a, b) {
    if (a.delivery.isLive != b.delivery.isLive) {
      return a.delivery.isLive ? -1 : 1;
    }
    return a.address.compareTo(b.address);
  });
  return out;
}

/// `cases.models.PRIORITY_CHOICE` and the mailbox's own case-type choices.
const List<String> mailboxPriorities = ['Low', 'Normal', 'High', 'Urgent'];
const List<String> mailboxCaseTypes = ['Question', 'Incident', 'Problem'];

/// The body for creating or editing a mailbox.
///
/// **No `webhook_secret`.** It would mean a credential travels to a phone and
/// back on every edit, and an empty field would blank the column, so it is not
/// a field on the form, which is the surest way not to send one.
///
/// `topicArn` is sent only when given: pass [mailboxTopicArnEdit]'s answer, so
/// a save sends the pin only when the admin changed it, and `''` clears it.
///
/// `isActive` is optional because the edit form does not own it: the row's own
/// Turn off / Turn on control does, and sending it from an edit would let a
/// stale form flip an address back on.
Map<String, dynamic> mailboxPayload({
  required String address,
  required String provider,
  required String defaultPriority,
  String? defaultCaseType,
  String? defaultAssigneeId,
  bool? isActive,
  String? topicArn,
}) {
  final body = <String, dynamic>{
    'address': address.trim().toLowerCase(),
    'provider': provider,
    'default_priority': defaultPriority,
    // Both are `allow_null=True`, and the empty string fails the ChoiceField
    // and the PK lookup alike, so "none selected" has to travel as null.
    'default_case_type': (defaultCaseType?.trim().isEmpty ?? true)
        ? null
        : defaultCaseType,
    'default_assignee_id': (defaultAssigneeId?.trim().isEmpty ?? true)
        ? null
        : defaultAssigneeId,
  };
  if (isActive != null) body['is_active'] = isActive;
  if (topicArn != null) body['topic_arn'] = topicArn.trim();
  return body;
}

/// The `topic_arn` a form save should send, or null to leave it alone.
///
/// Only a change is sent. The form is prefilled with the pin as it stood when
/// the list loaded, and the webhook can pin a mailbox after that (a
/// confirmation from an allowed AWS account), so resending the stale value, or
/// the empty one, would silently undo a pin nobody on this form chose to undo.
/// Clearing the field on purpose is a change, and sends `''`.
///
/// `frontend/src/lib/server/v2/inbound-email.js` `topicArnEdit` is the same
/// rule.
String? mailboxTopicArnEdit(String submitted, String? loaded) {
  final next = submitted.trim();
  return next == (loaded ?? '').trim() ? null : next;
}

/// Turn an address on or off without rewriting it.
///
/// Its own body rather than a reuse of [mailboxPayload]: that builds a whole
/// mailbox, and a form that is not open has nothing to say about the address.
/// `InboundMailboxDetailView.put` is `partial=True`, so a single key leaves
/// everything else alone.
Map<String, dynamic> mailboxActivePayload(bool isActive) => {
  'is_active': isActive,
};

/// What a create or edit has to fix before it is worth sending, or null.
///
/// `InboundMailboxSerializer` is the authority, including the duplicate-address
/// check, which needs the org and cannot happen here. This exists so an empty
/// field answers immediately rather than after a round trip.
String? mailboxAddressProblem(String address) {
  final trimmed = address.trim();
  if (trimmed.isEmpty) return 'Give the address.';
  // Deliberately loose. `EmailField` is what decides, and a stricter pattern
  // here would refuse addresses the server accepts.
  final at = trimmed.indexOf('@');
  if (at <= 0 || at == trimmed.length - 1 || trimmed.contains(' ')) {
    return 'That does not look like an email address.';
  }
  return null;
}

/// What deleting an address does, said before it is done.
const String mailboxDeleteExplanation =
    'Deleted for good. Mail sent here stops becoming tickets, and the topic '
    'pin goes with it, so a delivery already in flight from AWS stops '
    'verifying. Tickets already opened from this address are not touched.';

/// What turning one off does, which is not the same as deleting it.
const String mailboxDeactivateExplanation =
    'Stops opening tickets. Mail keeps arriving and nothing bounces, so anyone '
    'writing here gets no ticket and no error. It stays in the list, off, '
    'until it is turned back on.';

/// The two checks a delivery has to clear, said once on the screen.
///
/// This replaced a card that claimed a per-address shared secret proved a
/// delivery genuine and that the server minted one on create. Neither is true:
/// nothing in the backend compares that column.
const String mailboxAuthExplanation =
    'AWS signs each notification, and the address has to be pinned to the exact '
    'SNS topic it was subscribed to. The signature alone proves only that some '
    'AWS account sent it, so without the pin anyone who learned an address id '
    'could have AWS sign forged mail into this organization. An admin can '
    'enter the pin as the address\'s Topic ARN. Left blank, it is set by the '
    'first subscription AWS confirms from an AWS account this server allows; '
    'a subscription from any other account is refused. Only admins can see '
    'the ARN, because it carries the AWS account id.';
