/// An account: the company a contact works for and a deal is booked against.
///
/// Mobile had no accounts module at all, while the deal form already fetched
/// them for its picker, so `AccountLookup` in `lookup_models.dart` exists and
/// carries four fields. This is the full record. The two are kept apart on
/// purpose: a picker that only needs an id and a name should not pay for a
/// rollup query, and widening the lookup would have made every deal form
/// heavier to serve one screen.
class Account {
  const Account({
    required this.id,
    required this.name,
    this.email,
    this.phone,
    this.website,
    this.industry,
    this.numberOfEmployees,
    this.annualRevenue,
    this.currency,
    this.addressLine,
    this.city,
    this.state,
    this.postcode,
    this.country,
    this.countryDisplay,
    this.description,
    this.isActive = true,
    this.assignedToIds = const [],
    this.assignedToNames = const [],
    this.tagIds = const [],
    this.tagNames = const [],
    this.contacts = const [],
    this.opportunities = const [],
    this.cases = const [],
    this.tasks = const [],
    this.rollups,
    this.customFields = const {},
    this.createdByEmail,
    this.createdAt,
  });

  final String id;
  final String name;
  final String? email;
  final String? phone;
  final String? website;

  final String? industry;
  final int? numberOfEmployees;
  final String? annualRevenue;
  final String? currency;

  final String? addressLine;
  final String? city;
  final String? state;
  final String? postcode;

  /// The stored ISO code, e.g. "IN".
  final String? country;

  /// The server's human label for [country], e.g. "India". Present on list and
  /// detail; absent when the account has no country, which is why it is
  /// nullable rather than defaulted to the code.
  final String? countryDisplay;

  final String? description;
  final bool isActive;

  final List<String> assignedToIds;
  final List<String> assignedToNames;
  final List<String> tagIds;
  final List<String> tagNames;

  final List<AccountRelation> contacts;
  final List<AccountRelation> opportunities;
  final List<AccountRelation> cases;
  final List<AccountRelation> tasks;

  /// What the account is worth, owes, and is complaining about.
  ///
  /// Null is meaningful and must stay distinguishable from zero: the server
  /// sends `null` on any endpoint that did not run the rollup annotation, and
  /// a screen given no numbers should say nothing rather than claim every
  /// total is zero.
  final AccountRollups? rollups;

  final Map<String, dynamic> customFields;

  /// The creator's email, not their id, because that is the key
  /// `isAdminOrOwner` is given on every other detail screen: the signed-in
  /// user is known by email there. Comparing an id against an email is the
  /// mismatch that has silently disabled permission checks in this codebase
  /// before.
  final String? createdByEmail;
  final DateTime? createdAt;

  String get initials {
    final trimmed = name.trim();
    if (trimmed.isEmpty) return '?';
    final parts = trimmed.split(RegExp(r'\s+'));
    if (parts.length == 1) {
      return parts.first.substring(0, 1).toUpperCase();
    }
    return (parts.first.substring(0, 1) + parts[1].substring(0, 1))
        .toUpperCase();
  }

  /// One line for a list row: the city and country if known, else the website.
  String? get locationLine {
    final place = [
      city,
      countryDisplay ?? country,
    ].where((v) => v != null && v.trim().isNotEmpty).join(', ');
    if (place.isNotEmpty) return place;
    final site = website?.trim();
    return (site == null || site.isEmpty) ? null : site;
  }

  factory Account.fromJson(Map<String, dynamic> json) {
    return Account(
      id: json['id']?.toString() ?? '',
      name: json['name'] as String? ?? '',
      email: _str(json['email']),
      phone: _str(json['phone']),
      website: _str(json['website']),
      industry: _str(json['industry']),
      numberOfEmployees: _int(json['number_of_employees']),
      annualRevenue: _str(json['annual_revenue']),
      currency: _str(json['currency']),
      addressLine: _str(json['address_line']),
      city: _str(json['city']),
      state: _str(json['state']),
      postcode: _str(json['postcode']),
      country: _str(json['country']),
      countryDisplay: _str(json['country_display']),
      description: _str(json['description']),
      isActive: json['is_active'] as bool? ?? true,
      assignedToIds: _ids(json['assigned_to']),
      assignedToNames: _names(json['assigned_to']),
      tagIds: _ids(json['tags']),
      tagNames: _names(json['tags']),
      contacts: AccountRelation.listFrom(json['contacts'], _contactLabel),
      opportunities: AccountRelation.listFrom(
        json['opportunities'],
        (row) => row['name'] as String?,
      ),
      cases: AccountRelation.listFrom(
        json['cases'],
        (row) => row['name'] as String?,
      ),
      tasks: AccountRelation.listFrom(
        json['tasks'],
        (row) => row['title'] as String?,
      ),
      rollups: json['rollups'] is Map<String, dynamic>
          ? AccountRollups.fromJson(json['rollups'] as Map<String, dynamic>)
          : null,
      customFields: json['custom_fields'] is Map<String, dynamic>
          ? Map<String, dynamic>.from(json['custom_fields'] as Map)
          : const {},
      createdByEmail: _nestedEmail(json['created_by']),
      createdAt: _date(json['created_at']),
    );
  }

  /// The payload the create and edit forms submit. Also the snapshot the
  /// unsaved-changes guard compares against, so anything omitted here is
  /// something the form cannot lose.
  Map<String, dynamic> toPayload() {
    return {
      'name': name.trim(),
      'email': _orNull(email),
      'phone': _orNull(phone),
      'website': _orNull(website),
      'industry': _orNull(industry),
      'number_of_employees': numberOfEmployees,
      'annual_revenue': _orNull(annualRevenue),
      'currency': _orNull(currency),
      'address_line': _orNull(addressLine),
      'city': _orNull(city),
      'state': _orNull(state),
      'postcode': _orNull(postcode),
      'country': _orNull(country),
      'description': _orNull(description),
      'assigned_to': assignedToIds,
      'tags': tagIds,
      'contacts': contacts.map((c) => c.id).toList(),
      'custom_fields': customFields,
    };
  }

  Account copyWith({
    String? name,
    String? email,
    String? phone,
    String? website,
    String? industry,
    int? numberOfEmployees,
    bool clearNumberOfEmployees = false,
    String? annualRevenue,
    String? currency,
    String? addressLine,
    String? city,
    String? state,
    String? postcode,
    String? country,
    String? description,
    bool? isActive,
    List<String>? assignedToIds,
    List<String>? tagIds,
    List<AccountRelation>? contacts,
    Map<String, dynamic>? customFields,
  }) {
    return Account(
      id: id,
      name: name ?? this.name,
      email: email ?? this.email,
      phone: phone ?? this.phone,
      website: website ?? this.website,
      industry: industry ?? this.industry,
      numberOfEmployees: clearNumberOfEmployees
          ? null
          : (numberOfEmployees ?? this.numberOfEmployees),
      annualRevenue: annualRevenue ?? this.annualRevenue,
      currency: currency ?? this.currency,
      addressLine: addressLine ?? this.addressLine,
      city: city ?? this.city,
      state: state ?? this.state,
      postcode: postcode ?? this.postcode,
      country: country ?? this.country,
      countryDisplay: countryDisplay,
      description: description ?? this.description,
      isActive: isActive ?? this.isActive,
      assignedToIds: assignedToIds ?? this.assignedToIds,
      assignedToNames: assignedToNames,
      tagIds: tagIds ?? this.tagIds,
      tagNames: tagNames,
      contacts: contacts ?? this.contacts,
      opportunities: opportunities,
      cases: cases,
      tasks: tasks,
      rollups: rollups,
      customFields: customFields ?? this.customFields,
      createdByEmail: createdByEmail,
      createdAt: createdAt,
    );
  }
}

/// A row the account links to: a contact, deal, ticket or task. Enough to draw
/// a line and navigate, nothing more, because that is all the account
/// serializer sends for them.
class AccountRelation {
  const AccountRelation({required this.id, required this.label, this.detail});

  final String id;
  final String label;

  /// Stage for a deal, as the org names it (`stage_label`, falling back to
  /// the code from a server that predates it); absent for everything else.
  final String? detail;

  static List<AccountRelation> listFrom(
    dynamic raw,
    String? Function(Map<String, dynamic>) label,
  ) {
    if (raw is! List) return const [];
    return raw
        .whereType<Map<String, dynamic>>()
        .map((row) {
          return AccountRelation(
            id: row['id']?.toString() ?? '',
            label: label(row)?.trim().isNotEmpty == true
                ? label(row)!.trim()
                : 'Untitled',
            detail: _str(row['stage_label']) ?? _str(row['stage']),
          );
        })
        .toList(growable: false);
  }
}

/// The account's totals, from `accounts.views.ROLLUP_FIELDS` and
/// `attach_money_rollups`.
///
/// Every count is nullable for the same reason the whole object is: a number
/// the server did not compute must not read as zero. The names are the
/// server's, checked against the view rather than guessed, because a
/// misspelled key here yields a silent null and a panel that shows nothing.
///
/// Deals and invoices each carry a currency and there are no exchange rates,
/// so the money arrives per currency (`by_currency`) and is kept that way in
/// [money]. A server older than that sends only the plain figures, which are
/// kept under an empty code.
class AccountRollups {
  const AccountRollups({
    this.money = const [],
    this.wonCount,
    this.openDealCount,
    this.openTickets,
    this.firstWonOn,
  });

  /// Won, open and overdue money, one entry per currency, ordered by code.
  final List<RollupMoney> money;
  final int? wonCount;
  final int? openDealCount;
  final int? openTickets;

  /// When this account first bought anything. Null for a prospect.
  final DateTime? firstWonOn;

  bool get isEmpty =>
      money.isEmpty &&
      wonCount == null &&
      openDealCount == null &&
      openTickets == null;

  factory AccountRollups.fromJson(Map<String, dynamic> json) {
    final rows = json['by_currency'];
    final hasPlain = json.containsKey('won_amount');
    return AccountRollups(
      money: rows is List
          ? [
              for (final row in rows.whereType<Map<String, dynamic>>())
                if (row['currency'] is String) RollupMoney.fromJson(row),
            ]
          : [
              if (hasPlain) RollupMoney.fromJson({...json, 'currency': ''}),
            ],
      wonCount: _int(json['won_count']),
      openDealCount: _int(json['open_deal_count']),
      openTickets: _int(json['open_tickets']),
      firstWonOn: _date(json['first_won_on']),
    );
  }
}

/// An account's money in one currency.
class RollupMoney {
  const RollupMoney({
    required this.currency,
    this.wonAmount = 0,
    this.openPipeline = 0,
    this.overdueAmount = 0,
  });

  /// The currency code, or empty from a server that sent plain figures only.
  final String currency;

  /// Closed-won total.
  final double wonAmount;

  /// Value of everything still open.
  final double openPipeline;

  /// Invoiced and past due.
  final double overdueAmount;

  factory RollupMoney.fromJson(Map<String, dynamic> json) {
    return RollupMoney(
      currency: json['currency'] as String,
      wonAmount: _double(json['won_amount']) ?? 0,
      openPipeline: _double(json['open_pipeline']) ?? 0,
      overdueAmount: _double(json['overdue_amount']) ?? 0,
    );
  }
}

String? _contactLabel(Map<String, dynamic> row) {
  final name = [
    row['first_name'],
    row['last_name'],
  ].whereType<String>().where((s) => s.trim().isNotEmpty).join(' ');
  if (name.isNotEmpty) return name;
  return row['primary_email'] as String? ?? row['email'] as String?;
}

String? _str(dynamic v) {
  if (v == null) return null;
  final s = v.toString().trim();
  return s.isEmpty ? null : s;
}

String? _orNull(String? v) {
  final s = v?.trim();
  return (s == null || s.isEmpty) ? null : s;
}

int? _int(dynamic v) => v is int ? v : int.tryParse(v?.toString() ?? '');

double? _double(dynamic v) =>
    v is num ? v.toDouble() : double.tryParse(v?.toString() ?? '');

DateTime? _date(dynamic v) =>
    v == null ? null : DateTime.tryParse(v.toString());

/// The API nests `assigned_to` and `tags` as objects, not bare ids.
List<String> _ids(dynamic raw) {
  if (raw is! List) return const [];
  return raw
      .map((e) => e is Map ? e['id']?.toString() : e?.toString())
      .whereType<String>()
      .where((s) => s.isNotEmpty)
      .toList(growable: false);
}

List<String> _names(dynamic raw) {
  if (raw is! List) return const [];
  return raw
      .map((e) {
        if (e is! Map) return null;
        final user = e['user_details'];
        if (user is Map) {
          return (user['name'] ?? user['email'])?.toString();
        }
        return (e['name'] ?? e['title'])?.toString();
      })
      .whereType<String>()
      .where((s) => s.trim().isNotEmpty)
      .toList(growable: false);
}

String? _nestedEmail(dynamic raw) {
  if (raw is Map) return _str(raw['email']);
  return null;
}
