/// A contact: a person at an account.
///
/// Like `Account`, this sits beside the four-field `ContactLookup` that the
/// deal form's picker has used all along. The lookup stays narrow on purpose.
class Contact {
  const Contact({
    required this.id,
    required this.firstName,
    required this.lastName,
    this.email,
    this.phone,
    this.organization,
    this.title,
    this.department,
    this.doNotCall = false,
    this.linkedinUrl,
    this.addressLine,
    this.city,
    this.state,
    this.postcode,
    this.country,
    this.description,
    this.isActive = true,
    this.accountId,
    this.accountName,
    this.linkedAccounts = const [],
    this.assignedToNames = const [],
    this.tagNames = const [],
    this.createdAt,
    this.canDelete = false,
  });

  final String id;
  final String firstName;
  final String lastName;
  final String? email;
  final String? phone;

  final String? organization;
  final String? title;
  final String? department;

  /// Set by the person, not by us. Surfaced on the detail screen because
  /// calling someone who asked not to be called is the kind of mistake a CRM
  /// exists to prevent.
  final bool doNotCall;
  final String? linkedinUrl;

  final String? addressLine;
  final String? city;
  final String? state;
  final String? postcode;
  final String? country;

  final String? description;
  final bool isActive;

  /// The primary account FK. Distinct from [linkedAccounts]: the web app has
  /// both, and on this data the FK is frequently empty while the many-to-many
  /// carries the real relationships. Showing only the FK would make a
  /// well-linked contact look unattached.
  final String? accountId;
  final String? accountName;
  final List<ContactAccountLink> linkedAccounts;

  final List<String> assignedToNames;
  final List<String> tagNames;

  final DateTime? createdAt;

  /// Whether the signed-in user may delete this contact: the server's own delete
  /// rule (an admin, or whoever created it), sent as `can_delete` beside the
  /// detail record. False wherever it was not sent, such as a list row, so
  /// Delete is never offered on a guess.
  final bool canDelete;

  String get fullName {
    final name = '${firstName.trim()} ${lastName.trim()}'.trim();
    return name.isEmpty ? (email ?? 'Unnamed contact') : name;
  }

  String get initials {
    final first = firstName.trim();
    final last = lastName.trim();
    if (first.isEmpty && last.isEmpty) return '?';
    final a = first.isNotEmpty ? first[0] : '';
    final b = last.isNotEmpty ? last[0] : '';
    return '$a$b'.toUpperCase();
  }

  /// The line under the name in a list row: their role and company if known.
  String? get subtitle {
    final parts = [
      title,
      accountName ?? organization,
    ].where((v) => v != null && v.trim().isNotEmpty).join(' at ');
    if (parts.isNotEmpty) return parts;
    return email;
  }

  factory Contact.fromJson(
    Map<String, dynamic> json, {
    bool canDelete = false,
  }) {
    final account = json['account_detail'];
    return Contact(
      id: json['id']?.toString() ?? '',
      firstName: json['first_name'] as String? ?? '',
      lastName: json['last_name'] as String? ?? '',
      email: _str(json['email']) ?? _str(json['primary_email']),
      phone: _str(json['phone']) ?? _str(json['mobile_number']),
      organization: _str(json['organization']),
      title: _str(json['title']),
      department: _str(json['department']),
      doNotCall: json['do_not_call'] as bool? ?? false,
      linkedinUrl: _str(json['linkedin_url']),
      addressLine: _str(json['address_line']),
      city: _str(json['city']),
      state: _str(json['state']),
      postcode: _str(json['postcode']),
      country: _str(json['country']),
      description: _str(json['description']),
      isActive: json['is_active'] as bool? ?? true,
      accountId: account is Map
          ? account['id']?.toString()
          : _str(json['account']),
      accountName: account is Map ? _str(account['name']) : null,
      linkedAccounts: ContactAccountLink.listFrom(json['linked_accounts']),
      assignedToNames: _profileNames(json['assigned_to']),
      tagNames: _tagNames(json['tags']),
      createdAt: json['created_at'] == null
          ? null
          : DateTime.tryParse(json['created_at'].toString()),
      canDelete: canDelete,
    );
  }

  /// The payload the forms submit, matching `CreateContactSerializer.Meta`.
  /// Fields outside that list are read-only on the server, so sending them
  /// would be noise the serializer discards.
  Map<String, dynamic> toPayload() {
    return {
      'first_name': firstName.trim(),
      'last_name': lastName.trim(),
      'email': _orNull(email),
      'phone': _orNull(phone),
      'organization': _orNull(organization),
      'title': _orNull(title),
      'department': _orNull(department),
      'do_not_call': doNotCall,
      'linkedin_url': _orNull(linkedinUrl),
      'address_line': _orNull(addressLine),
      'city': _orNull(city),
      'state': _orNull(state),
      'postcode': _orNull(postcode),
      'country': _orNull(country),
      'description': _orNull(description),
      'account': _orNull(accountId),
      'is_active': isActive,
    };
  }

  Contact copyWith({
    String? firstName,
    String? lastName,
    String? email,
    String? phone,
    String? organization,
    String? title,
    String? department,
    bool? doNotCall,
    String? linkedinUrl,
    String? addressLine,
    String? city,
    String? state,
    String? postcode,
    String? country,
    String? description,
    bool? isActive,
    String? accountId,
    bool clearAccount = false,
  }) {
    return Contact(
      id: id,
      firstName: firstName ?? this.firstName,
      lastName: lastName ?? this.lastName,
      email: email ?? this.email,
      phone: phone ?? this.phone,
      organization: organization ?? this.organization,
      title: title ?? this.title,
      department: department ?? this.department,
      doNotCall: doNotCall ?? this.doNotCall,
      linkedinUrl: linkedinUrl ?? this.linkedinUrl,
      addressLine: addressLine ?? this.addressLine,
      city: city ?? this.city,
      state: state ?? this.state,
      postcode: postcode ?? this.postcode,
      country: country ?? this.country,
      description: description ?? this.description,
      isActive: isActive ?? this.isActive,
      accountId: clearAccount ? null : (accountId ?? this.accountId),
      accountName: clearAccount ? null : accountName,
      linkedAccounts: linkedAccounts,
      assignedToNames: assignedToNames,
      tagNames: tagNames,
      createdAt: createdAt,
      canDelete: canDelete,
    );
  }
}

/// An account this contact is attached to through the many-to-many.
class ContactAccountLink {
  const ContactAccountLink({required this.id, required this.name});

  final String id;
  final String name;

  static List<ContactAccountLink> listFrom(dynamic raw) {
    if (raw is! List) return const [];
    return raw
        .whereType<Map<String, dynamic>>()
        .map(
          (row) => ContactAccountLink(
            id: row['id']?.toString() ?? '',
            name: (row['name'] as String?)?.trim().isNotEmpty == true
                ? (row['name'] as String).trim()
                : 'Unnamed account',
          ),
        )
        .toList(growable: false);
  }
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

/// `assigned_to` is a list of profiles, whose display name lives one level
/// down in `user_details`.
List<String> _profileNames(dynamic raw) {
  if (raw is! List) return const [];
  return raw
      .map((e) {
        if (e is! Map) return null;
        final user = e['user_details'];
        if (user is Map) return (user['name'] ?? user['email'])?.toString();
        return null;
      })
      .whereType<String>()
      .where((s) => s.trim().isNotEmpty)
      .toList(growable: false);
}

List<String> _tagNames(dynamic raw) {
  if (raw is! List) return const [];
  return raw
      .map((e) => e is Map ? e['name']?.toString() : e?.toString())
      .whereType<String>()
      .where((s) => s.trim().isNotEmpty)
      .toList(growable: false);
}
