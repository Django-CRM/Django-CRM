/// The organization's own settings: the company profile printed on documents,
/// the locale defaults, and the two org-wide case-handling switches.
///
/// `GET/PATCH /api/org/settings/` always acts on `request.profile.org`. **There
/// is no org id in the URL**, so a caller can only ever read or edit their own
/// org. That is the tenant barrier, and there is nothing here to scope by hand.
///
/// The org API key is absent from this payload entirely. It authenticates as
/// the org's first admin, so it is excluded from `OrgSettingsSerializer` and
/// rotated only through `OrgApiKeyView`. `is_active`, the org kill switch, is
/// likewise not editable here.
///
/// The read is open to any member; the PATCH is admin-only and the backend is
/// what refuses. `frontend/src/lib/server/v2/organization.ts` carries the same
/// allow-list.
library;

import '../api/schema.g.dart' as api;
import 'countries.dart';

/// Text, select and number fields the edit form may submit.
///
/// An allow-list, so a field the form never offered cannot be smuggled into a
/// body, and so `api_key`, `is_active`, `id`, `vertical` and `terminology` are
/// never sent from a client even by accident.
const List<String> editableOrgFields = [
  'name',
  'company_name',
  'address_line',
  'city',
  'state',
  'postcode',
  'country',
  'phone',
  'email',
  'website',
  'tax_id',
  'default_currency',
  'default_country',
  'timezone',
  // Invoice and estimate numbering (#802).
  'invoice_number_prefix',
  'estimate_number_prefix',
  'number_date_part',
  'number_padding',
  'invoice_number_start',
  // Null is off.
  'lead_reminder_minutes',
];

/// The two org-wide behaviour switches. Kept apart from the text fields because
/// they are booleans and always travel explicitly: "off" is a real value, not
/// the "absent, so leave alone" convention a blank text field uses.
const List<String> orgBooleanFields = [
  'csat_enabled',
  'auto_close_children_on_parent_close',
];

class OrgSettings {
  const OrgSettings({
    this.id = '',
    this.name = '',
    this.companyName = '',
    this.addressLine = '',
    this.city = '',
    this.state = '',
    this.postcode = '',
    this.country = '',
    this.phone = '',
    this.email = '',
    this.website = '',
    this.taxId = '',
    this.defaultCurrency = 'USD',
    this.defaultCountry = '',
    this.currencySymbol = '\$',
    this.timezone = 'UTC',
    this.csatEnabled = true,
    this.autoCloseChildren = false,
    this.vertical = '',
    this.invoiceNumberPrefix = 'INV-',
    this.estimateNumberPrefix = 'EST-',
    this.numberDatePart = 'DAY',
    this.numberPadding = 4,
    this.invoiceNumberStart = 1,
    this.leadReminderMinutes,
    this.logoUrl,
    this.memberCount = 0,
    this.createdAt,
  });

  final String id;

  /// What the org is called across the app. `company_name` is the legal name
  /// printed on a document; these are deliberately two fields.
  final String name;
  final String companyName;

  final String addressLine;
  final String city;
  final String state;
  final String postcode;
  final String country;
  final String phone;
  final String email;
  final String website;
  final String taxId;

  final String defaultCurrency;
  final String defaultCountry;
  final String currencySymbol;
  final String timezone;

  /// Org-level kill switch. False short-circuits the post-close signal before
  /// any survey email is sent, org-wide, with no per-team exception.
  final bool csatEnabled;

  /// The DEFAULT state of the cascade checkbox when a parent ticket is closed.
  /// See [cascadeDefaultExplanation]: it decides how the prompt starts, not
  /// what happens without one.
  final bool autoCloseChildren;

  /// Which vertical pack was applied. Written by the pack applier server-side
  /// and read-only here.
  final String vertical;

  /// How new invoice and estimate numbers look (#802): the prefix, then the
  /// date and a hyphen unless [numberDatePart] is `NONE`, then a counter
  /// padded to [numberPadding] digits. The defaults are the format every org
  /// had before this was a setting. See [exampleNumber].
  final String invoiceNumberPrefix;
  final String estimateNumberPrefix;
  final String numberDatePart;
  final int numberPadding;

  /// The lowest counter the next invoice may take. Undated numbers only.
  final int invoiceNumberStart;

  /// Remind the assignee when a lead has waited this many minutes for a first
  /// reply: one of [leadReminderChoices], or null for off.
  final int? leadReminderMinutes;

  final String? logoUrl;
  final int memberCount;
  final DateTime? createdAt;

  /// Reads an `OrgSettings`, which the GET and the PATCH both answer.
  factory OrgSettings.fromJson(Map<String, dynamic> json) {
    final w = api.OrgSettings.fromJson(json);
    return OrgSettings(
      id: w.id ?? '',
      name: w.name ?? '',
      companyName: w.companyName ?? '',
      addressLine: w.addressLine ?? '',
      city: w.city ?? '',
      state: w.state ?? '',
      postcode: w.postcode ?? '',
      country: w.country ?? '',
      phone: w.phone ?? '',
      email: w.email ?? '',
      website: w.website ?? '',
      taxId: w.taxId ?? '',
      defaultCurrency: w.defaultCurrency ?? 'USD',
      defaultCountry: w.defaultCountry ?? '',
      currencySymbol: w.currencySymbol ?? '\$',
      timezone: w.timezone ?? 'UTC',
      csatEnabled: w.csatEnabled ?? true,
      autoCloseChildren: w.autoCloseChildrenOnParentClose ?? false,
      vertical: w.vertical ?? '',
      invoiceNumberPrefix: w.invoiceNumberPrefix ?? 'INV-',
      estimateNumberPrefix: w.estimateNumberPrefix ?? 'EST-',
      numberDatePart: w.numberDatePart ?? 'DAY',
      numberPadding: w.numberPadding ?? 4,
      invoiceNumberStart: w.invoiceNumberStart ?? 1,
      leadReminderMinutes: w.leadReminderMinutes,
      logoUrl: w.logoUrl,
      memberCount: w.memberCount ?? 0,
      createdAt: DateTime.tryParse(w.createdAt ?? '')?.toLocal(),
    );
  }

  /// The postal address on one line, in the order a label prints it.
  String get addressSummary => [
    addressLine,
    city,
    state,
    postcode,
    country,
  ].where((part) => part.trim().isNotEmpty).join(', ');

  /// The timezone with its underscores opened out, for reading.
  String get timezoneLabel => timezone.replaceAll('_', ' ');
}

/// One IANA zone as `/api/org/timezones/` sends it.
///
/// Served rather than read from the device because the two vocabularies
/// disagree: a phone reports `Asia/Calcutta` where the server also knows
/// `Asia/Kolkata`, and a picker that cannot find the stored value would offer
/// to move the org somewhere it never chose.
class TimezoneOption {
  const TimezoneOption({required this.name, required this.offsetMinutes});

  final String name;
  final int offsetMinutes;

  /// Reads a `TimezoneOption`.
  factory TimezoneOption.fromJson(Map<String, dynamic> json) {
    final w = api.TimezoneOption.fromJson(json);
    return TimezoneOption(
      name: w.name ?? 'UTC',
      offsetMinutes: w.offsetMinutes ?? 0,
    );
  }

  /// "Asia/Kolkata (UTC+05:30)". The offset is what makes a 490-entry list
  /// readable.
  String get label =>
      '${name.replaceAll('_', ' ')} (${_offset(offsetMinutes)})';
}

String _offset(int minutes) {
  if (minutes == 0) return 'UTC';
  final sign = minutes < 0 ? '-' : '+';
  final abs = minutes.abs();
  final hh = (abs ~/ 60).toString().padLeft(2, '0');
  final mm = (abs % 60).toString().padLeft(2, '0');
  return 'UTC$sign$hh:$mm';
}

/// The body for `PATCH /api/org/settings/`.
///
/// Every field goes every time, because this form edits them all at once and a
/// partial body would make "cleared" and "not sent" the same thing. The two
/// booleans go as real booleans for the same reason.
///
/// [invoiceNumberStart] goes as 1 whenever [numberDatePart] dates the number:
/// the server refuses a start with a date, including one still stored from
/// undated numbering, because a dated count restarts each period.
///
/// Nothing read-only is included: no `id`, no `api_key` (which the payload has
/// never carried), no `is_active`, no `vertical`, no `terminology`,
/// no `member_count`.
Map<String, dynamic> orgSettingsPayload({
  required String name,
  required String companyName,
  required String addressLine,
  required String city,
  required String state,
  required String postcode,
  required String country,
  required String phone,
  required String email,
  required String website,
  required String taxId,
  required String defaultCurrency,
  required String defaultCountry,
  required String timezone,
  required bool csatEnabled,
  required bool autoCloseChildren,
  required String invoiceNumberPrefix,
  required String estimateNumberPrefix,
  required String numberDatePart,
  required int numberPadding,
  required int invoiceNumberStart,
  int? leadReminderMinutes,
  bool sendLeadReminder = false,
}) => {
  'name': name.trim(),
  'company_name': companyName.trim(),
  'address_line': addressLine.trim(),
  'city': city.trim(),
  'state': state.trim(),
  'postcode': postcode.trim(),
  'country': country.trim(),
  'phone': phone.trim(),
  'email': email.trim(),
  'website': website.trim(),
  'tax_id': taxId.trim(),
  'default_currency': defaultCurrency.trim(),
  'default_country': defaultCountry.trim(),
  'timezone': timezone.trim(),
  'csat_enabled': csatEnabled,
  'auto_close_children_on_parent_close': autoCloseChildren,
  'invoice_number_prefix': invoiceNumberPrefix.trim(),
  'estimate_number_prefix': estimateNumberPrefix.trim(),
  'number_date_part': numberDatePart,
  'number_padding': numberPadding,
  'invoice_number_start': numberDatePart == 'NONE' ? invoiceNumberStart : 1,
  if (sendLeadReminder) 'lead_reminder_minutes': leadReminderMinutes,
};

/// The waits the reminder offers, in minutes, as the API's choices.
const List<int> leadReminderChoices = [5, 15, 30, 60];

/// What an org save has to fix before it is worth sending, or null.
///
/// The serializer is the rule; these two exist so a typo answers immediately.
/// `email` is an `EmailField` and `website` a `URLField`, both of which reject
/// on the server with a message that names the field.
String? orgSettingsProblem({required String email, required String website}) {
  final mail = email.trim();
  if (mail.isNotEmpty &&
      !RegExp(r'^[^@\s]+@[^@\s]+\.[^@\s]+$').hasMatch(mail)) {
    return 'That does not look like an email address.';
  }
  final site = website.trim();
  if (site.isNotEmpty && !RegExp(r'^https?://.+\..+').hasMatch(site)) {
    return 'Include the full website address, starting with http:// or https://.';
  }
  return null;
}

/// One entry in a picker.
typedef PickerOption = ({String value, String label});

/// `Org.NUMBER_DATE_PARTS`, labelled as the web's org form labels them.
const List<PickerOption> numberDatePartOptions = [
  (value: 'NONE', label: 'No date, one running count'),
  (value: 'YEAR', label: 'Year, count restarts each year'),
  (value: 'MONTH', label: 'Year and month, count restarts each month'),
  (value: 'DAY', label: 'Full date, count restarts each day'),
];

/// How many digits of `YYYYMMDD` each date part keeps.
const Map<String, int> _dateDigits = {
  'NONE': 0,
  'YEAR': 4,
  'MONTH': 6,
  'DAY': 8,
};

/// What a new number looks like: the series and its first counter. A preview
/// only, as on the web (`$lib/v2/document-numbers.js`); the server assigns the
/// real number on save, counting what the org has already issued.
///
/// [start] counts for undated numbers only, as on the server. [padding] is
/// clamped to the server's 1 to 10, so a width still being typed cannot build
/// an enormous string.
String exampleNumber({
  required String prefix,
  required String datePart,
  required int padding,
  required DateTime today,
  int start = 1,
}) {
  final ymd =
      '${today.year.toString().padLeft(4, '0')}'
      '${today.month.toString().padLeft(2, '0')}'
      '${today.day.toString().padLeft(2, '0')}';
  final digits = ymd.substring(0, _dateDigits[datePart] ?? 8);
  final series = digits.isEmpty ? prefix : '$prefix$digits-';
  final counter = datePart == 'NONE' ? start : 1;
  return '$series${counter.toString().padLeft(padding.clamp(1, 10), '0')}';
}

/// The server's prefix rule (`number_prefix_validator`): a number becomes a
/// PDF filename and an email subject, so nothing that means something in a
/// header or a path.
final RegExp _numberPrefix = RegExp(r'^[A-Za-z0-9_-]*$');

/// What the numbering section has to fix before a save is worth sending, or
/// null. The serializer is the rule; this answers a typo immediately. The
/// start is only checked when it will be sent, which is when [datePart] is
/// `NONE`.
String? numberingProblem({
  required String invoicePrefix,
  required String estimatePrefix,
  required String datePart,
  required String padding,
  required String start,
}) {
  if (!_numberPrefix.hasMatch(invoicePrefix.trim()) ||
      !_numberPrefix.hasMatch(estimatePrefix.trim())) {
    return 'A number prefix can use letters, numbers, hyphens and underscores '
        'only.';
  }
  final width = int.tryParse(padding.trim());
  if (width == null || width < 1 || width > 10) {
    return 'Counter digits must be from 1 to 10.';
  }
  final first = int.tryParse(start.trim());
  if (datePart == 'NONE' && (first == null || first < 1 || first > 999999999)) {
    return 'Start invoices at a whole number from 1 to 999,999,999.';
  }
  return null;
}

/// The countries the org form offers: every code the backend `COUNTRIES` set
/// accepts, as the web's org form does, so any stored country has an option.
final List<PickerOption> orgCountryOptions = [
  for (final c in kCountries) (value: c.code, label: c.label),
];

/// [options], guaranteed to contain [current].
///
/// **A picker that cannot display the stored value is a data-loss bug.** A
/// Flutter dropdown whose value matches no item throws outright, and the usual
/// repair, quietly falling back to the first entry, saves a value the org never
/// chose. The stored value is a code the server accepted, so the honest move is
/// to keep offering it. Used for country, currency and time zone, because an
/// installed build can lag the server: a code the backend added after the build
/// shipped (ZAR was one) must still round-trip unchanged.
List<PickerOption> withCurrent(List<PickerOption> options, String current) {
  final value = current.trim();
  if (value.isEmpty) return options;
  if (options.any((option) => option.value == value)) return options;
  return [...options, (value: value, label: value)];
}

/// What the CSAT switch really does, in the words the screen uses.
String csatExplanation(bool enabled) => enabled
    ? 'A survey goes out after a ticket closes. Turning this off stops every '
          'survey org-wide, with no per-team exception.'
    : 'No surveys are sent, anywhere in this organization. Nothing on a ticket '
          'says so, and there is no per-team exception.';

/// What the cascade switch really does, which is narrower than it looks.
///
/// **It sets how the close prompt starts, and only this app has that prompt.**
/// `CaseCloseWithChildrenView` reads
/// `Org.auto_close_children_on_parent_close` only when the caller omits
/// `cascade` entirely; both clients send it explicitly, so the setting reaches
/// a person through the checkbox and nowhere else. The web app has no such
/// prompt at all: closing a parent there leaves its children open. Saying "it
/// controls whether children close" would describe neither client.
String cascadeDefaultExplanation(bool enabled) => enabled
    ? 'Closing a parent ticket here starts with "also close linked tickets" '
          'ticked. You still confirm, and can untick it.'
    : 'Closing a parent ticket here starts with "also close linked tickets" '
          'unticked. You still confirm, and can tick it.';

/// The footnote both organization screens carry about the cascade switch.
const String cascadeWebGapNote =
    'The web app has no close prompt yet, so closing a parent there leaves its '
    'children open whatever this is set to.';

/// A vertical pack, the signup face of an industry app, as `/api/packs/` lists
/// it.
class VerticalPack {
  const VerticalPack({
    required this.id,
    required this.name,
    this.description = '',
    this.version = '',
  });

  final String id;
  final String name;
  final String description;
  final String version;

  /// Reads a `PackSummary`, whose `version` is an integer.
  factory VerticalPack.fromJson(Map<String, dynamic> json) {
    final w = api.PackSummary.fromJson(json);
    return VerticalPack(
      id: w.id ?? '',
      name: w.name ?? '',
      description: w.description ?? '',
      version: w.version?.toString() ?? '',
    );
  }
}

/// What a sample-data clear actually deleted, and what it kept.
///
/// Retained rows are not a failure: they are demo records the org has since
/// attached real work to, which the applier deliberately keeps because deleting
/// one would take that work with it.
String sampleDataResult({required int deleted, required int retained}) {
  final head = deleted == 0
      ? 'No sample data to clear.'
      : 'Deleted $deleted sample record${deleted == 1 ? '' : 's'}.';
  if (retained == 0) return head;
  return '$head Kept $retained record${retained == 1 ? '' : 's'} you have '
      'since attached real work to.';
}
