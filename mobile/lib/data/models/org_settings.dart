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
/// what refuses. `frontend/src/lib/server/v2/organization.js` carries the same
/// allow-list.
library;

import 'countries.dart';

/// Text and select fields the edit form may submit.
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

  final String? logoUrl;
  final int memberCount;
  final DateTime? createdAt;

  factory OrgSettings.fromJson(Map<String, dynamic> json) => OrgSettings(
    id: json['id']?.toString() ?? '',
    name: _text(json['name']),
    companyName: _text(json['company_name']),
    addressLine: _text(json['address_line']),
    city: _text(json['city']),
    state: _text(json['state']),
    postcode: _text(json['postcode']),
    country: _text(json['country']),
    phone: _text(json['phone']),
    email: _text(json['email']),
    website: _text(json['website']),
    taxId: _text(json['tax_id']),
    defaultCurrency: json['default_currency']?.toString() ?? 'USD',
    defaultCountry: _text(json['default_country']),
    currencySymbol: json['currency_symbol']?.toString() ?? '\$',
    timezone: json['timezone']?.toString() ?? 'UTC',
    csatEnabled: json['csat_enabled'] as bool? ?? true,
    autoCloseChildren:
        json['auto_close_children_on_parent_close'] as bool? ?? false,
    vertical: _text(json['vertical']),
    logoUrl: json['logo_url']?.toString(),
    memberCount: _intOr(json['member_count'], 0),
    createdAt: DateTime.tryParse(
      json['created_at']?.toString() ?? '',
    )?.toLocal(),
  );

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

String _text(dynamic value) => value?.toString() ?? '';

int _intOr(dynamic value, int fallback) {
  if (value is int) return value;
  if (value is num) return value.round();
  if (value is String) return int.tryParse(value) ?? fallback;
  return fallback;
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

  factory TimezoneOption.fromJson(Map<String, dynamic> json) => TimezoneOption(
    name: json['name']?.toString() ?? 'UTC',
    offsetMinutes: _intOr(json['offset_minutes'], 0),
  );

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
};

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

/// A vertical pack as `/api/packs/` lists it.
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

  factory VerticalPack.fromJson(Map<String, dynamic> json) => VerticalPack(
    id: json['id']?.toString() ?? '',
    name: json['name']?.toString() ?? '',
    description: json['description']?.toString() ?? '',
    version: json['version']?.toString() ?? '',
  );
}

/// What an apply actually did.
///
/// A pack applied to an org that already has some of this configured is the
/// normal case, not a partial failure, so both halves are reported.
class PackApplyReport {
  const PackApplyReport({this.created = 0, this.skipped = 0});

  final int created;
  final int skipped;

  factory PackApplyReport.fromJson(Map<String, dynamic> json) {
    final report = (json['report'] as Map?)?.cast<String, dynamic>() ?? json;
    return PackApplyReport(
      created: (report['created'] as List?)?.length ?? 0,
      skipped: (report['skipped'] as List?)?.length ?? 0,
    );
  }

  String get summary {
    if (created == 0 && skipped == 0) {
      return 'Nothing to add. This org already has all of it.';
    }
    if (skipped == 0) return 'Created $created item${created == 1 ? '' : 's'}.';
    if (created == 0) {
      return 'Already had everything from this pack, skipped $skipped '
          'item${skipped == 1 ? '' : 's'}.';
    }
    return 'Created $created item${created == 1 ? '' : 's'}, skipped $skipped '
        'you already had.';
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
