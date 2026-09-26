import 'attachment.dart';
import 'comment.dart';
import 'deal_pipeline.dart';
import 'lead.dart';
import 'lookup_models.dart';

/// Opportunity type enumeration
enum OpportunityType {
  // Sentinel, backend stores this as null (the column is blank=True,null=True).
  // The picker shows this as "Not Specified"; toJson translates it to null so
  // we don't auto-stamp NEW_BUSINESS on deals the user never categorized.
  unspecified('', 'Not Specified'),
  newBusiness('NEW_BUSINESS', 'New Business'),
  existingBusiness('EXISTING_BUSINESS', 'Existing Business'),
  renewal('RENEWAL', 'Renewal'),
  upsell('UPSELL', 'Upsell'),
  crossSell('CROSS_SELL', 'Cross Sell');

  final String value;
  final String label;

  const OpportunityType(this.value, this.label);

  static OpportunityType fromString(String? value) {
    if (value == null || value.isEmpty) return OpportunityType.unspecified;
    return OpportunityType.values.firstWhere(
      (e) => e.value.toLowerCase() == value.toLowerCase().replaceAll(' ', '_'),
      orElse: () => OpportunityType.unspecified,
    );
  }
}

/// Opportunity lead source enumeration (different from Lead's source)
enum OpportunitySource {
  none('NONE', 'None'),
  call('CALL', 'Call'),
  email('EMAIL', 'Email'),
  existingCustomer('EXISTING CUSTOMER', 'Existing Customer'),
  partner('PARTNER', 'Partner'),
  publicRelations('PUBLIC RELATIONS', 'Public Relations'),
  campaign('CAMPAIGN', 'Campaign'),
  website('WEBSITE', 'Website'),
  other('OTHER', 'Other');

  final String value;
  final String label;

  const OpportunitySource(this.value, this.label);

  static OpportunitySource fromString(String? value) {
    if (value == null) return OpportunitySource.none;
    return OpportunitySource.values.firstWhere(
      (e) => e.value.toLowerCase() == value.toLowerCase(),
      orElse: () => OpportunitySource.none,
    );
  }
}

/// Currency enumeration
enum Currency {
  usd('USD', 'USD', '\$'),
  eur('EUR', 'EUR', '€'),
  gbp('GBP', 'GBP', '£'),
  inr('INR', 'INR', '₹'),
  cad('CAD', 'CAD', 'C\$'),
  aud('AUD', 'AUD', 'A\$'),
  jpy('JPY', 'JPY', '¥'),
  cny('CNY', 'CNY', '¥'),
  chf('CHF', 'CHF', 'CHF'),
  sgd('SGD', 'SGD', 'S\$'),
  aed('AED', 'AED', 'د.إ'),
  brl('BRL', 'BRL', 'R\$'),
  mxn('MXN', 'MXN', '\$');

  final String value;
  final String label;
  final String symbol;

  const Currency(this.value, this.label, this.symbol);

  static Currency fromString(String? value) {
    if (value == null) return Currency.usd;
    return Currency.values.firstWhere(
      (e) => e.value.toLowerCase() == value.toLowerCase(),
      orElse: () => Currency.usd,
    );
  }

  /// The symbol for [code], or the code itself when this enum does not list
  /// it. Unlike [fromString] it never falls back to the dollar sign, so a
  /// figure in an unlisted currency cannot pass for one in dollars.
  static String symbolFor(String code) {
    for (final c in Currency.values) {
      if (c.value == code.toUpperCase()) return c.symbol;
    }
    return '$code ';
  }
}

/// Product in a deal
class DealProduct {
  final String id;
  final String name;

  /// A decimal on the server ("2.50"), so a double here.
  final double quantity;
  final double unitPrice;

  /// The line's own discount: `PERCENTAGE` takes that share, anything else a
  /// flat amount, as `OpportunityLineItem.save` reads it.
  final String discountType;
  final double discountValue;

  const DealProduct({
    required this.id,
    required this.name,
    required this.quantity,
    required this.unitPrice,
    this.discountType = '',
    this.discountValue = 0,
  });

  /// Trailing zeroes dropped, so 2 units reads "2" and 1.5 reads "1.5".
  String get quantityLabel {
    final whole = quantity.truncateToDouble() == quantity;
    return whole ? quantity.toStringAsFixed(0) : quantity.toString();
  }

  /// Quantity x unit price less the line's discount: the server's line `total`.
  double get netTotal {
    final gross = quantity * unitPrice;
    return gross -
        (discountType == 'PERCENTAGE'
            ? gross * discountValue / 100
            : discountValue);
  }
}

/// Deal model for BottleCRM
class Deal {
  final String id;
  final String title;
  final double value;

  /// The stage `code`, one of [pipelineId]'s stages. Read [stageLabel] to show
  /// it and [stageKind] (or [isClosed], [isWon], [isLost]) to decide anything.
  final String stage;
  final String stageLabel;

  /// `open`, `won` or `lost`. See `deal_pipeline.dart`.
  final String stageKind;

  /// Null from a server that predates configurable pipelines.
  final String? pipelineId;
  final int probability;
  final DateTime? closeDate;
  final String? leadId;
  final String companyName;
  final String? accountId;
  final List<DealProduct> products;
  final String assignedTo;
  final List<String> assignedToIds;
  // Raw assigned_to profile maps from the detail endpoint (each entry has
  // `id`, `user_details: { name, email, profile_pic }`). Used to render
  // every assignee in the detail screen, not just the first.
  final List<Map<String, dynamic>> assignedToRaw;
  final Priority priority;
  final List<String> labels;
  final List<String> tagIds;
  final List<String> contactIds;
  final String? notes;
  final OpportunityType opportunityType;
  final OpportunitySource leadSource;
  final Currency currency;
  final DateTime createdAt;
  final DateTime updatedAt;
  // When the deal last changed stage. Drives the rotten / aging indicator and
  // the "oldest first" sort. Null for legacy deals that pre-date the field.
  final DateTime? stageChangedAt;
  // Per-org JSONField on Opportunity. Empty map when the org defines no
  // custom fields or when the server hasn't returned them yet.
  final Map<String, dynamic> customFieldValues;
  // Detail-only payload. Empty/null on list responses; populated by
  // getDealDetail.
  final List<Comment> comments;
  final List<Attachment> attachments;
  final List<ContactLookup> contactsList;
  final List<String> teamIds;
  final List<String> teamNames;
  final String? createdByName;
  final String? createdByEmail;
  final String? closedByName;
  final String? closedByEmail;
  // Backend-computed aging: whole days in the current stage, and `green`,
  // `yellow` (past the stage's expected or warning days) or `red` (rotting).
  // The thresholds are per stage and live only on the server.
  final int? daysInStageServer;
  final String? agingStatus;
  final double? lineItemsTotal;

  const Deal({
    required this.id,
    required this.title,
    required this.value,
    required this.stage,
    this.stageLabel = '',
    this.stageKind = dealStageOpen,
    this.pipelineId,
    required this.probability,
    this.closeDate,
    this.leadId,
    required this.companyName,
    this.accountId,
    this.products = const [],
    required this.assignedTo,
    this.assignedToIds = const [],
    this.assignedToRaw = const [],
    required this.priority,
    this.labels = const [],
    this.tagIds = const [],
    this.contactIds = const [],
    this.notes,
    this.opportunityType = OpportunityType.newBusiness,
    this.leadSource = OpportunitySource.none,
    this.currency = Currency.usd,
    required this.createdAt,
    required this.updatedAt,
    this.stageChangedAt,
    this.customFieldValues = const {},
    this.comments = const [],
    this.attachments = const [],
    this.contactsList = const [],
    this.teamIds = const [],
    this.teamNames = const [],
    this.createdByName,
    this.createdByEmail,
    this.closedByName,
    this.closedByEmail,
    this.daysInStageServer,
    this.agingStatus,
    this.lineItemsTotal,
  });

  /// Factory constructor to create Deal from JSON (backend API)
  factory Deal.fromJson(Map<String, dynamic> json) {
    // Parse tags - both names and IDs
    List<String> parsedTags = [];
    List<String> parsedTagIds = [];
    if (json['tags'] != null) {
      final tagsList = json['tags'] as List<dynamic>? ?? [];
      for (final t in tagsList) {
        if (t is Map<String, dynamic>) {
          final name = t['name'] as String? ?? '';
          final id = t['id']?.toString() ?? '';
          if (name.isNotEmpty) parsedTags.add(name);
          if (id.isNotEmpty) parsedTagIds.add(id);
        }
      }
    }

    // Parse assigned_to. Two outputs:
    //   - assignedToIds: just the profile IDs (used by writes / filters)
    //   - assignedToRaw: the original profile maps (used by detail rendering)
    //   - assignedToName: a single display name for the *first* assignee
    //     (used by the list card where there's no room for more than one).
    String assignedToName = 'Unassigned';
    List<String> assignedToIds = [];
    List<Map<String, dynamic>> assignedToRaw = [];
    if (json['assigned_to'] != null) {
      final assignedList = json['assigned_to'] as List<dynamic>? ?? [];
      for (final item in assignedList) {
        if (item is Map<String, dynamic>) {
          assignedToRaw.add(item);
          final id = item['id']?.toString() ?? '';
          if (id.isNotEmpty) assignedToIds.add(id);
          if (assignedToName == 'Unassigned') {
            // Prefer the detail-endpoint user_details.name; fall back to
            // legacy fields when the response shape is the list endpoint's.
            final userDetails = item['user_details'];
            String name = '';
            String email = '';
            if (userDetails is Map<String, dynamic>) {
              name = (userDetails['name'] as String? ?? '').trim();
              email = (userDetails['email'] as String? ?? '').trim();
            }
            email = email.isNotEmpty
                ? email
                : (item['user']?['email'] as String? ??
                          item['user__email'] as String? ??
                          '')
                      .trim();
            if (name.isNotEmpty) {
              assignedToName = name;
            } else if (email.isNotEmpty) {
              assignedToName = email.split('@').first;
            }
            if (assignedToName.isEmpty) assignedToName = 'Assigned';
          }
        }
      }
    }

    // Parse contacts - both IDs (for writes) and full records (for display).
    List<String> contactIds = [];
    List<ContactLookup> contactsList = [];
    if (json['contacts'] != null) {
      final rawContacts = json['contacts'] as List<dynamic>? ?? [];
      for (final c in rawContacts) {
        if (c is Map<String, dynamic>) {
          final id = c['id']?.toString() ?? '';
          if (id.isNotEmpty) contactIds.add(id);
          try {
            contactsList.add(ContactLookup.fromJson(c));
          } catch (_) {
            // Skip unparsable contact; ID list still wins.
          }
        }
      }
    }

    // Parse teams - both IDs (for writes) and names (for display).
    final List<String> teamIds = [];
    final List<String> teamNames = [];
    if (json['teams'] != null) {
      final rawTeams = json['teams'] as List<dynamic>? ?? [];
      for (final t in rawTeams) {
        if (t is Map<String, dynamic>) {
          final id = t['id']?.toString() ?? '';
          final name = t['name'] as String? ?? '';
          if (id.isNotEmpty) teamIds.add(id);
          if (name.isNotEmpty) teamNames.add(name);
        }
      }
    }

    // created_by and closed_by, full user / profile maps.
    String? createdByName;
    String? createdByEmail;
    final cb = json['created_by'];
    if (cb is Map<String, dynamic>) {
      createdByEmail = cb['email'] as String?;
      final first = (cb['first_name'] as String? ?? '').trim();
      final last = (cb['last_name'] as String? ?? '').trim();
      final composed = [
        first,
        last,
      ].where((s) => s.isNotEmpty).join(' ').trim();
      if (composed.isNotEmpty) createdByName = composed;
    }

    String? closedByName;
    String? closedByEmail;
    final clb = json['closed_by'];
    if (clb is Map<String, dynamic>) {
      final userDetails = clb['user_details'];
      if (userDetails is Map<String, dynamic>) {
        closedByEmail = userDetails['email'] as String?;
        final n = (userDetails['name'] as String? ?? '').trim();
        if (n.isNotEmpty) closedByName = n;
      }
      closedByEmail ??= clb['email'] as String?;
    }

    // line_items_total. Backend pre-computes; may be int, double, string,
    // or null.
    double? lineItemsTotal;
    final lit = json['line_items_total'];
    if (lit is num) {
      lineItemsTotal = lit.toDouble();
    } else if (lit is String) {
      lineItemsTotal = double.tryParse(lit);
    }

    int? daysInStageServer;
    final dis = json['days_in_stage'];
    if (dis is num) {
      daysInStageServer = dis.toInt();
    } else if (dis is String) {
      daysInStageServer = int.tryParse(dis);
    }

    // Parse account/company name and ID
    String companyName = 'No Account';
    String? accountId;
    if (json['account'] != null && json['account'] is Map<String, dynamic>) {
      companyName = json['account']['name'] as String? ?? 'No Account';
      accountId = json['account']['id']?.toString();
    }

    // Parse products/line_items
    List<DealProduct> products = [];
    if (json['line_items'] != null) {
      final lineItems = json['line_items'] as List<dynamic>? ?? [];
      products = lineItems
          .map((item) {
            if (item is Map<String, dynamic>) {
              // DRF sends decimals as strings ("2.00"); a bare num is taken too.
              // An int parse of "2.00" fails, which used to read every line
              // as a quantity of 1.
              double decimal(Object? raw, double fallback) => raw is num
                  ? raw.toDouble()
                  : double.tryParse(raw?.toString() ?? '') ?? fallback;
              return DealProduct(
                id: item['id']?.toString() ?? '',
                name: item['name'] as String? ?? '',
                quantity: decimal(item['quantity'], 1),
                unitPrice: decimal(item['unit_price'], 0),
                discountType: item['discount_type'] as String? ?? '',
                discountValue: decimal(item['discount_value'], 0),
              );
            }
            return const DealProduct(
              id: '',
              name: '',
              quantity: 0,
              unitPrice: 0,
            );
          })
          .where((p) => p.id.isNotEmpty)
          .toList();
    }

    // Parse close date
    DateTime? closeDate;
    if (json['closed_on'] != null) {
      closeDate = DateTime.tryParse(json['closed_on'] as String);
    }

    // Parse amount - can be num or string
    double amount = 0.0;
    if (json['amount'] != null) {
      if (json['amount'] is num) {
        amount = (json['amount'] as num).toDouble();
      } else if (json['amount'] is String) {
        amount = double.tryParse(json['amount'] as String) ?? 0.0;
      }
    }

    // Parse probability - can be num or string
    int probability = 0;
    if (json['probability'] != null) {
      if (json['probability'] is num) {
        probability = (json['probability'] as num).toInt();
      } else if (json['probability'] is String) {
        probability = int.tryParse(json['probability'] as String) ?? 0;
      }
    }

    // The kind decides closed/won/lost. A row with none (a server older than
    // configurable pipelines, or a code its pipeline no longer has) falls
    // back to what the seeded codes have always meant.
    final stageCode = json['stage']?.toString() ?? '';
    final label = json['stage_label'] as String?;
    final pipeline = json['pipeline']?.toString();

    return Deal(
      id: json['id']?.toString() ?? '',
      title: json['name'] as String? ?? '',
      value: amount,
      stage: stageCode,
      stageLabel: (label == null || label.isEmpty)
          ? legacyDealStageLabel(stageCode)
          : label,
      stageKind:
          json['stage_kind'] as String? ?? legacyDealStageKind(stageCode),
      pipelineId: (pipeline == null || pipeline.isEmpty) ? null : pipeline,
      probability: probability,
      closeDate: closeDate,
      leadId: null, // API doesn't have lead reference directly
      companyName: companyName,
      accountId: accountId,
      products: products,
      assignedTo: assignedToName,
      assignedToIds: assignedToIds,
      assignedToRaw: assignedToRaw,
      priority: Priority.medium, // API doesn't have priority, default to medium
      labels: parsedTags,
      tagIds: parsedTagIds,
      contactIds: contactIds,
      notes: json['description'] as String?,
      opportunityType: OpportunityType.fromString(
        json['opportunity_type'] as String?,
      ),
      leadSource: OpportunitySource.fromString(json['lead_source'] as String?),
      currency: Currency.fromString(json['currency'] as String?),
      createdAt: json['created_at'] != null
          ? DateTime.tryParse(json['created_at'] as String) ?? DateTime.now()
          : DateTime.now(),
      updatedAt: json['updated_at'] != null
          ? DateTime.tryParse(json['updated_at'] as String) ?? DateTime.now()
          : DateTime.now(),
      stageChangedAt: json['stage_changed_at'] != null
          ? DateTime.tryParse(json['stage_changed_at'] as String)
          : null,
      customFieldValues: json['custom_fields'] is Map
          ? Map<String, dynamic>.from(json['custom_fields'] as Map)
          : const {},
      contactsList: contactsList,
      teamIds: teamIds,
      teamNames: teamNames,
      createdByName: createdByName,
      createdByEmail: createdByEmail,
      closedByName: closedByName,
      closedByEmail: closedByEmail,
      daysInStageServer: daysInStageServer,
      agingStatus: json['aging_status'] as String?,
      lineItemsTotal: lineItemsTotal,
    );
  }

  /// Convert Deal to JSON for API requests (create/update).
  ///
  /// IMPORTANT: nullable fields (amount, account, closed_on, description,
  /// opportunity_type) are emitted as explicit `null` when empty so edit-mode
  /// clears actually persist. The backend's PUT uses a non-partial serializer,
  /// so omitting a key leaves the old value in place, which would silently
  /// drop the user's edit. m2m collections (contacts/tags/teams/assigned_to)
  /// are always sent as arrays (possibly empty) because the view unconditionally
  /// calls `.clear()` then re-adds.
  ///
  /// `pipeline` goes only when known, and `stage` only when chosen: with
  /// neither, the server keeps the deal's pipeline (or the org default for a
  /// new deal) and starts a new deal in its first open stage.
  Map<String, dynamic> toJson() {
    return {
      'name': title,
      if (pipelineId != null && pipelineId!.isNotEmpty) 'pipeline': pipelineId,
      if (stage.isNotEmpty) 'stage': stage,
      'probability': probability,
      'amount': value > 0 ? value.toStringAsFixed(2) : null,
      'account': (accountId != null && accountId!.isNotEmpty)
          ? accountId
          : null,
      'closed_on': closeDate != null
          ? '${closeDate!.year}-${closeDate!.month.toString().padLeft(2, '0')}-${closeDate!.day.toString().padLeft(2, '0')}'
          : null,
      'description': (notes != null && notes!.isNotEmpty) ? notes : null,
      'assigned_to': assignedToIds,
      'contacts': contactIds,
      'tags': tagIds,
      'teams': teamIds,
      'opportunity_type': opportunityType == OpportunityType.unspecified
          ? null
          : opportunityType.value,
      'lead_source': leadSource.value,
      'currency': currency.value,
      'custom_fields': customFieldValues,
    };
  }

  bool get isWon => stageKind == dealStageWon;
  bool get isLost => stageKind == dealStageLost;
  bool get isClosed => isWon || isLost;

  /// Get weighted value based on probability
  double get weightedValue => value * (probability / 100);

  /// Check if deal is closing soon (within 7 days)
  bool get isClosingSoon {
    if (closeDate == null) return false;
    final daysUntilClose = closeDate!.difference(DateTime.now()).inDays;
    return daysUntilClose >= 0 && daysUntilClose <= 7;
  }

  /// Check if deal is overdue
  bool get isOverdue {
    if (closeDate == null || isClosed) return false;
    return closeDate!.isBefore(DateTime.now());
  }

  /// Days until close date
  int? get daysUntilClose {
    if (closeDate == null) return null;
    return closeDate!.difference(DateTime.now()).inDays;
  }

  Deal copyWith({
    String? id,
    String? title,
    double? value,
    String? stage,
    String? stageLabel,
    String? stageKind,
    String? pipelineId,
    int? probability,
    DateTime? closeDate,
    String? leadId,
    String? companyName,
    String? accountId,
    List<DealProduct>? products,
    String? assignedTo,
    List<String>? assignedToIds,
    List<Map<String, dynamic>>? assignedToRaw,
    Priority? priority,
    List<String>? labels,
    List<String>? tagIds,
    List<String>? contactIds,
    String? notes,
    OpportunityType? opportunityType,
    OpportunitySource? leadSource,
    Currency? currency,
    DateTime? createdAt,
    DateTime? updatedAt,
    DateTime? stageChangedAt,
    Map<String, dynamic>? customFieldValues,
    List<Comment>? comments,
    List<Attachment>? attachments,
    List<ContactLookup>? contactsList,
    List<String>? teamIds,
    List<String>? teamNames,
    String? createdByName,
    String? createdByEmail,
    String? closedByName,
    String? closedByEmail,
    int? daysInStageServer,
    String? agingStatus,
    double? lineItemsTotal,
  }) {
    return Deal(
      id: id ?? this.id,
      title: title ?? this.title,
      value: value ?? this.value,
      stage: stage ?? this.stage,
      stageLabel: stageLabel ?? this.stageLabel,
      stageKind: stageKind ?? this.stageKind,
      pipelineId: pipelineId ?? this.pipelineId,
      probability: probability ?? this.probability,
      closeDate: closeDate ?? this.closeDate,
      leadId: leadId ?? this.leadId,
      companyName: companyName ?? this.companyName,
      accountId: accountId ?? this.accountId,
      products: products ?? this.products,
      assignedTo: assignedTo ?? this.assignedTo,
      assignedToIds: assignedToIds ?? this.assignedToIds,
      assignedToRaw: assignedToRaw ?? this.assignedToRaw,
      priority: priority ?? this.priority,
      labels: labels ?? this.labels,
      tagIds: tagIds ?? this.tagIds,
      contactIds: contactIds ?? this.contactIds,
      notes: notes ?? this.notes,
      opportunityType: opportunityType ?? this.opportunityType,
      leadSource: leadSource ?? this.leadSource,
      currency: currency ?? this.currency,
      createdAt: createdAt ?? this.createdAt,
      updatedAt: updatedAt ?? this.updatedAt,
      stageChangedAt: stageChangedAt ?? this.stageChangedAt,
      customFieldValues: customFieldValues ?? this.customFieldValues,
      comments: comments ?? this.comments,
      attachments: attachments ?? this.attachments,
      contactsList: contactsList ?? this.contactsList,
      teamIds: teamIds ?? this.teamIds,
      teamNames: teamNames ?? this.teamNames,
      createdByName: createdByName ?? this.createdByName,
      createdByEmail: createdByEmail ?? this.createdByEmail,
      closedByName: closedByName ?? this.closedByName,
      closedByEmail: closedByEmail ?? this.closedByEmail,
      daysInStageServer: daysInStageServer ?? this.daysInStageServer,
      agingStatus: agingStatus ?? this.agingStatus,
      lineItemsTotal: lineItemsTotal ?? this.lineItemsTotal,
    );
  }

  /// Days since the deal last changed stage, counted on the phone. Only a
  /// fallback for [daysInStageServer]; null when the field is missing.
  int? get daysInCurrentStage {
    final ref = stageChangedAt;
    if (ref == null) return null;
    return DateTime.now().difference(ref).inDays;
  }

  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      other is Deal && runtimeType == other.runtimeType && id == other.id;

  @override
  int get hashCode => id.hashCode;
}
