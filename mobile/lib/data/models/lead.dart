import 'package:flutter/material.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';
import '../../core/theme/app_colors.dart';
import 'attachment.dart';
import 'comment.dart';

/// Lead status enumeration matching backend LEAD_STATUS
enum LeadStatus {
  assigned('assigned', 'Assigned', AppColors.primary400),
  inProcess('in process', 'In Process', AppColors.warning500),
  converted('converted', 'Converted', AppColors.success600),
  recycled('recycled', 'Recycled', AppColors.gray500),
  closed('closed', 'Closed', AppColors.danger500);

  final String value;
  final String label;
  final Color color;

  const LeadStatus(this.value, this.label, this.color);

  String get displayName => label;

  static LeadStatus fromString(String? value) {
    if (value == null) return LeadStatus.assigned;
    final normalized = value.toLowerCase().trim();
    return LeadStatus.values.firstWhere(
      (s) => s.value == normalized,
      orElse: () => LeadStatus.assigned,
    );
  }
}

/// Lead source enumeration matching backend LEAD_SOURCE
enum LeadSource {
  none('', 'None', LucideIcons.circleHelp),
  call('call', 'Call', LucideIcons.phone),
  email('email', 'Email', LucideIcons.mail),
  existingCustomer('existing customer', 'Existing Customer', LucideIcons.users),
  partner('partner', 'Partner', LucideIcons.handshake),
  publicRelations('public relations', 'Public Relations', LucideIcons.newspaper),
  campaign(
    'compaign',
    'Campaign',
    LucideIcons.megaphone,
  ), // Note: backend has typo "compaign"
  other('other', 'Other', LucideIcons.ellipsis);

  final String value;
  final String label;
  final IconData icon;

  const LeadSource(this.value, this.label, this.icon);

  String get displayName => label;

  static LeadSource fromString(String? value) {
    if (value == null || value.isEmpty) return LeadSource.none;
    final normalized = value.toLowerCase().trim();
    return LeadSource.values.firstWhere(
      (s) => s.value == normalized,
      orElse: () => LeadSource.other,
    );
  }
}

/// Lead rating/priority enumeration matching backend rating field
enum LeadRating {
  cold('COLD', 'Cold', AppColors.primary400),
  warm('WARM', 'Warm', AppColors.warning500),
  hot('HOT', 'Hot', AppColors.danger500);

  final String value;
  final String label;
  final Color color;

  const LeadRating(this.value, this.label, this.color);

  String get displayName => label;

  static LeadRating fromString(String? value) {
    if (value == null) return LeadRating.cold;
    final normalized = value.toUpperCase().trim();
    return LeadRating.values.firstWhere(
      (r) => r.value == normalized,
      orElse: () => LeadRating.cold,
    );
  }
}

/// Priority enumeration (for backwards compatibility)
enum Priority {
  low('Low', AppColors.success500),
  medium('Medium', AppColors.warning500),
  high('High', AppColors.danger500),
  urgent('Urgent', AppColors.purple500);

  final String label;
  final Color color;

  const Priority(this.label, this.color);

  String get displayName => label;

  static Priority fromString(String? value) {
    if (value == null) return Priority.medium;
    switch (value.toLowerCase()) {
      case 'low':
        return Priority.low;
      case 'medium':
        return Priority.medium;
      case 'high':
        return Priority.high;
      case 'urgent':
        return Priority.urgent;
      default:
        return Priority.medium;
    }
  }

  /// Convert from LeadRating
  static Priority fromRating(LeadRating rating) {
    switch (rating) {
      case LeadRating.cold:
        return Priority.low;
      case LeadRating.warm:
        return Priority.medium;
      case LeadRating.hot:
        return Priority.high;
    }
  }
}

/// Lead model for BottleCRM
class Lead {
  final String id;
  final String? title;
  final String? salutation;
  final String firstName;
  final String lastName;
  final String email;
  final String? phone;
  final String? jobTitle;
  final String? website;
  final String? linkedinUrl;
  final String companyName;
  final LeadStatus status;
  final LeadSource source;
  final LeadRating rating;
  final String? industry;
  final double? opportunityAmount;
  final String? currency;
  final int? probability;
  final DateTime? closeDate;
  final String? addressLine;
  final String? city;
  final String? state;
  final String? postcode;
  final String? country;
  final DateTime? lastContacted;
  final DateTime? nextFollowUp;
  final String? description;
  final List<String> tags;
  final List<String> tagIds;
  final List<Map<String, dynamic>>? assignedTo;
  final List<String> assignedToIds;
  final List<Comment> comments;
  final List<Attachment> attachments;
  final Map<String, dynamic> customFieldValues;

  /// Email of the profile that created the lead, from `LeadSerializer`'s
  /// nested `created_by`. Carried because delete is the creator's right, not
  /// the assignee's, so the detail sheet needs it to decide what to offer.
  final String? createdByEmail;
  final DateTime createdAt;
  final DateTime? updatedAt;
  final bool isActive;

  const Lead({
    required this.id,
    this.title,
    this.salutation,
    required this.firstName,
    required this.lastName,
    required this.email,
    this.phone,
    this.jobTitle,
    this.website,
    this.linkedinUrl,
    required this.companyName,
    required this.status,
    required this.source,
    required this.rating,
    this.industry,
    this.opportunityAmount,
    this.currency,
    this.probability,
    this.closeDate,
    this.addressLine,
    this.city,
    this.state,
    this.postcode,
    this.country,
    this.lastContacted,
    this.nextFollowUp,
    this.description,
    this.tags = const [],
    this.tagIds = const [],
    this.assignedTo,
    this.assignedToIds = const [],
    this.comments = const [],
    this.attachments = const [],
    this.customFieldValues = const {},
    this.createdByEmail,
    required this.createdAt,
    this.updatedAt,
    this.isActive = true,
  });

  /// Full name combining first and last name
  String get name {
    final parts = [firstName, lastName].where((p) => p.isNotEmpty).toList();
    return parts.isNotEmpty ? parts.join(' ') : email;
  }

  /// Alias for companyName
  String get company => companyName;

  /// Priority based on rating
  Priority get priority => Priority.fromRating(rating);

  /// Get assigned user name (first one if multiple).
  ///
  /// Backend shape (ProfileSerializer): `{id, user_details: {name, email,
  /// profile_pic, ...}, role, ...}`. We prefer the display name, fall back
  /// to the email-local-part, and finally to "Unknown" so the avatar never
  /// shows just "?".
  String get assignedToName {
    if (assignedTo == null || assignedTo!.isEmpty) return 'Unassigned';
    final first = assignedTo!.first;
    final details = first['user_details'];
    if (details is Map<String, dynamic>) {
      final name = (details['name'] as String?)?.trim() ?? '';
      if (name.isNotEmpty) return name;
      final email = (details['email'] as String?)?.trim() ?? '';
      if (email.isNotEmpty) return email.split('@').first;
    }
    // Legacy / flat shapes some endpoints used to return.
    final flatEmail = (first['user__email'] as String?)?.trim() ?? '';
    if (flatEmail.isNotEmpty) return flatEmail.split('@').first;
    return 'Unknown';
  }

  /// Profile picture URL of the first assignee, if any.
  String? get assignedToProfilePic {
    if (assignedTo == null || assignedTo!.isEmpty) return null;
    final details = assignedTo!.first['user_details'];
    if (details is Map<String, dynamic>) {
      final pic = (details['profile_pic'] as String?)?.trim();
      if (pic != null && pic.isNotEmpty) return pic;
    }
    return null;
  }

  /// Get lead initials for avatar fallback
  String get initials {
    if (firstName.isNotEmpty && lastName.isNotEmpty) {
      return '${firstName[0]}${lastName[0]}'.toUpperCase();
    } else if (firstName.isNotEmpty) {
      return firstName
          .substring(0, firstName.length >= 2 ? 2 : 1)
          .toUpperCase();
    } else if (lastName.isNotEmpty) {
      return lastName.substring(0, lastName.length >= 2 ? 2 : 1).toUpperCase();
    }
    return '?';
  }

  /// Factory constructor to create Lead from JSON
  factory Lead.fromJson(Map<String, dynamic> json) {
    // Parse tags - both names and IDs
    List<String> parsedTags = [];
    List<String> parsedTagIds = [];
    if (json['tags'] != null) {
      final tagsList = json['tags'] as List<dynamic>;
      for (final t in tagsList) {
        if (t is Map<String, dynamic>) {
          final name = t['name'] as String? ?? '';
          final id = t['id']?.toString() ?? '';
          if (name.isNotEmpty) parsedTags.add(name);
          if (id.isNotEmpty) parsedTagIds.add(id);
        } else if (t is String) {
          parsedTags.add(t);
        }
      }
    }

    // Parse assigned_to - both full objects and IDs
    List<Map<String, dynamic>>? parsedAssignedTo;
    List<String> parsedAssignedToIds = [];
    if (json['assigned_to'] != null) {
      final assignedList = json['assigned_to'] as List<dynamic>;
      parsedAssignedTo = [];
      for (final a in assignedList) {
        if (a is Map<String, dynamic>) {
          parsedAssignedTo.add(a);
          final id = a['id']?.toString() ?? '';
          if (id.isNotEmpty) parsedAssignedToIds.add(id);
        }
      }
    }

    // Parse comments (lead_comments from backend)
    List<Comment> parsedComments = [];
    if (json['lead_comments'] != null) {
      final commentsList = json['lead_comments'] as List<dynamic>;
      parsedComments = commentsList
          .map((c) => Comment.fromJson(c as Map<String, dynamic>))
          .toList();
    }

    // Parse attachments. List backend uses `lead_attachment` inside the
    // serialized lead object; the detail endpoint also returns a separate
    // top-level `attachments` key, which is merged in by the provider.
    List<Attachment> parsedAttachments = [];
    final rawAttachments = json['lead_attachment'] ?? json['attachments'];
    if (rawAttachments is List) {
      parsedAttachments = rawAttachments
          .whereType<Map<String, dynamic>>()
          .map(Attachment.fromJson)
          .toList();
    }

    // Custom fields, backend stores them as a single JSON dict on the
    // lead row, keyed by the CustomFieldDefinition.key.
    Map<String, dynamic> parsedCustomFields = const {};
    final cf = json['custom_fields'];
    if (cf is Map<String, dynamic>) {
      parsedCustomFields = Map<String, dynamic>.from(cf);
    }

    return Lead(
      id: json['id']?.toString() ?? '',
      title: json['title'] as String?,
      salutation: json['salutation'] as String?,
      firstName: json['first_name'] as String? ?? '',
      lastName: json['last_name'] as String? ?? '',
      email: json['email'] as String? ?? '',
      phone: json['phone'] as String?,
      jobTitle: json['job_title'] as String?,
      website: json['website'] as String?,
      linkedinUrl: json['linkedin_url'] as String?,
      companyName: json['company_name'] as String? ?? '',
      status: LeadStatus.fromString(json['status'] as String?),
      source: LeadSource.fromString(json['source'] as String?),
      rating: LeadRating.fromString(json['rating'] as String?),
      industry: json['industry'] as String?,
      opportunityAmount: json['opportunity_amount'] != null
          ? double.tryParse(json['opportunity_amount'].toString())
          : null,
      currency: json['currency'] as String?,
      probability: json['probability'] as int?,
      closeDate: json['close_date'] != null
          ? DateTime.tryParse(json['close_date'] as String)
          : null,
      addressLine: json['address_line'] as String?,
      city: json['city'] as String?,
      state: json['state'] as String?,
      postcode: json['postcode'] as String?,
      country: json['country'] as String?,
      lastContacted: json['last_contacted'] != null
          ? DateTime.tryParse(json['last_contacted'] as String)
          : null,
      nextFollowUp: json['next_follow_up'] != null
          ? DateTime.tryParse(json['next_follow_up'] as String)
          : null,
      description: json['description'] as String?,
      tags: parsedTags,
      tagIds: parsedTagIds,
      assignedTo: parsedAssignedTo,
      assignedToIds: parsedAssignedToIds,
      comments: parsedComments,
      attachments: parsedAttachments,
      customFieldValues: parsedCustomFields,
      // `created_by` is a nested UserSerializer on the detail route and may be
      // absent or a bare id on lighter payloads, so read it defensively.
      createdByEmail: json['created_by'] is Map<String, dynamic>
          ? (json['created_by'] as Map<String, dynamic>)['email'] as String?
          : null,
      createdAt: json['created_at'] != null
          ? DateTime.tryParse(json['created_at'] as String) ?? DateTime.now()
          : DateTime.now(),
      updatedAt: json['updated_at'] != null
          ? DateTime.tryParse(json['updated_at'] as String)
          : null,
      isActive: json['is_active'] as bool? ?? true,
    );
  }

  /// Convert Lead to JSON
  Map<String, dynamic> toJson() {
    return {
      'id': id,
      'title': title,
      'salutation': salutation,
      'first_name': firstName,
      'last_name': lastName,
      'email': email,
      'phone': phone,
      'job_title': jobTitle,
      'website': website,
      'linkedin_url': linkedinUrl,
      'company_name': companyName,
      'status': status.value,
      'source': source.value,
      'rating': rating.value,
      'industry': industry,
      'opportunity_amount': opportunityAmount,
      'currency': currency,
      'probability': probability,
      'close_date': closeDate?.toIso8601String(),
      'address_line': addressLine,
      'city': city,
      'state': state,
      'postcode': postcode,
      'country': country,
      'last_contacted': lastContacted?.toIso8601String(),
      'next_follow_up': nextFollowUp?.toIso8601String(),
      'description': description,
      'is_active': isActive,
    };
  }

  Lead copyWith({
    String? id,
    String? title,
    String? salutation,
    String? firstName,
    String? lastName,
    String? email,
    String? phone,
    String? jobTitle,
    String? website,
    String? linkedinUrl,
    String? companyName,
    LeadStatus? status,
    LeadSource? source,
    LeadRating? rating,
    String? industry,
    double? opportunityAmount,
    String? currency,
    int? probability,
    DateTime? closeDate,
    String? addressLine,
    String? city,
    String? state,
    String? postcode,
    String? country,
    DateTime? lastContacted,
    DateTime? nextFollowUp,
    String? description,
    List<String>? tags,
    List<String>? tagIds,
    List<Map<String, dynamic>>? assignedTo,
    List<String>? assignedToIds,
    List<Comment>? comments,
    List<Attachment>? attachments,
    Map<String, dynamic>? customFieldValues,
    String? createdByEmail,
    DateTime? createdAt,
    DateTime? updatedAt,
    bool? isActive,
  }) {
    return Lead(
      id: id ?? this.id,
      title: title ?? this.title,
      salutation: salutation ?? this.salutation,
      firstName: firstName ?? this.firstName,
      lastName: lastName ?? this.lastName,
      email: email ?? this.email,
      phone: phone ?? this.phone,
      jobTitle: jobTitle ?? this.jobTitle,
      website: website ?? this.website,
      linkedinUrl: linkedinUrl ?? this.linkedinUrl,
      companyName: companyName ?? this.companyName,
      status: status ?? this.status,
      source: source ?? this.source,
      rating: rating ?? this.rating,
      industry: industry ?? this.industry,
      opportunityAmount: opportunityAmount ?? this.opportunityAmount,
      currency: currency ?? this.currency,
      probability: probability ?? this.probability,
      closeDate: closeDate ?? this.closeDate,
      addressLine: addressLine ?? this.addressLine,
      city: city ?? this.city,
      state: state ?? this.state,
      postcode: postcode ?? this.postcode,
      country: country ?? this.country,
      lastContacted: lastContacted ?? this.lastContacted,
      nextFollowUp: nextFollowUp ?? this.nextFollowUp,
      description: description ?? this.description,
      tags: tags ?? this.tags,
      tagIds: tagIds ?? this.tagIds,
      assignedTo: assignedTo ?? this.assignedTo,
      assignedToIds: assignedToIds ?? this.assignedToIds,
      comments: comments ?? this.comments,
      attachments: attachments ?? this.attachments,
      customFieldValues: customFieldValues ?? this.customFieldValues,
      createdByEmail: createdByEmail ?? this.createdByEmail,
      createdAt: createdAt ?? this.createdAt,
      updatedAt: updatedAt ?? this.updatedAt,
      isActive: isActive ?? this.isActive,
    );
  }

  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      other is Lead && runtimeType == other.runtimeType && id == other.id;

  @override
  int get hashCode => id.hashCode;
}
