import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../config/api_config.dart';
import '../data/api_envelope.dart';
import '../data/models/access_token.dart';
import '../data/models/approval_rule.dart';
import '../data/models/business_calendar.dart';
import '../data/models/custom_field_definition.dart';
import '../data/models/escalation_policy.dart';
import '../data/models/macro.dart';
import '../data/models/mailbox.dart';
import '../data/models/org_settings.dart';
import '../data/models/reopen_policy.dart';
import '../data/models/routing_rule.dart';
import '../data/models/tag.dart';
import '../services/api_service.dart';

/// The org settings cluster: every page of it, on the phone.
///
/// Every write here is admin-only server-side. The screens hide the controls
/// from everyone else, which is UX: `CustomFieldDefinitionListCreateView.post`
/// and `CustomFieldDefinitionDetailView.put`/`.delete` each call
/// `is_org_admin` first and answer 403, so hiding a button is not what keeps a
/// member out.

/// The server's own message where there is one. These endpoints refuse for
/// reasons a user can act on ("Admin access required", "key must be a
/// lowercase slug starting with a letter").
String _message(dynamic response) {
  final errors = response.data?['errors'];
  if (errors is Map) {
    for (final value in errors.values) {
      if (value is List && value.isNotEmpty) return value.first.toString();
      if (value is String && value.trim().isNotEmpty) return value;
    }
  }
  if (errors is String && errors.trim().isNotEmpty) return errors;
  final detail = response.data?['message'];
  if (detail is String && detail.trim().isNotEmpty) return detail;
  final raw = response.message as String?;
  return (raw == null || raw.trim().isEmpty) ? 'Something went wrong' : raw;
}

/// The definition list plus the counts the settings screen shows above it.
class CustomFieldsState {
  const CustomFieldsState({
    this.fields = const [],
    this.count = 0,
    this.active = 0,
    this.modelsExtended = 0,
    this.requiredWithGaps = 0,
  });

  final List<CustomFieldDefinition> fields;

  /// From the server's `totals` block, which is computed over the org's whole
  /// definition set rather than the rows on screen. Recomputing these from
  /// `fields` would give the same answer today and quietly diverge the moment
  /// the list is filtered.
  final int count;
  final int active;
  final int modelsExtended;

  /// Required fields that some records have no value for. The banner exists
  /// because marking a field required binds writes from that point on and
  /// backfills nothing.
  final int requiredWithGaps;

  /// Fields grouped by the record type they extend, since "what do we collect
  /// on a ticket" is how anyone looks for one. Groups are ordered by the
  /// target's display name; fields inside a group by `display_order`, then
  /// label, matching the order the record forms render them in.
  List<MapEntry<String, List<CustomFieldDefinition>>> get grouped {
    final byTarget = <String, List<CustomFieldDefinition>>{};
    for (final field in fields) {
      byTarget.putIfAbsent(field.targetModel, () => []).add(field);
    }
    final entries = byTarget.entries.toList()
      ..sort(
        (a, b) => customFieldTargetLabel(
          a.key,
        ).compareTo(customFieldTargetLabel(b.key)),
      );
    for (final entry in entries) {
      entry.value.sort((a, b) {
        final byOrder = a.displayOrder.compareTo(b.displayOrder);
        return byOrder != 0 ? byOrder : a.label.compareTo(b.label);
      });
    }
    return entries;
  }
}

class CustomFieldsNotifier extends AsyncNotifier<CustomFieldsState> {
  final ApiService _api = ApiService();

  @override
  Future<CustomFieldsState> build() => _fetch();

  Future<void> refresh() async {
    state = const AsyncValue.loading();
    state = await AsyncValue.guard(_fetch);
  }

  /// No `active_only`, because this is the screen that turns a field back on
  /// and cannot offer that for a row it never fetched. No `include_counts`
  /// either: `records_missing_value` is the number this page is here to show,
  /// so it is the one caller that wants the scan.
  Future<CustomFieldsState> _fetch() async {
    final response = await _api.get(ApiConfig.customFieldDefinitions);
    if (!response.success || response.data == null) {
      throw Exception(response.message ?? 'Failed to load custom fields');
    }
    final body = response.data!;
    final fields = listFromEnvelope(body, const [
      'definitions',
    ]).map(CustomFieldDefinition.fromJson).toList(growable: false);
    final totals = body['totals'];
    return CustomFieldsState(
      fields: fields,
      count: _int(totals, 'count', fields.length),
      active: _int(totals, 'active', fields.where((f) => f.isActive).length),
      modelsExtended: _int(
        totals,
        'models_extended',
        fields
            .where((f) => f.isActive)
            .map((f) => f.targetModel)
            .toSet()
            .length,
      ),
      requiredWithGaps: _int(
        totals,
        'required_with_gaps',
        fields.where((f) => f.isRequired && f.recordsMissingValue > 0).length,
      ),
    );
  }

  Future<String?> createField(Map<String, dynamic> payload) async {
    final response = await _api.post(ApiConfig.customFieldDefinitions, payload);
    if (!response.success) return _message(response);
    await refresh();
    return null;
  }

  Future<String?> updateField(String id, Map<String, dynamic> payload) async {
    final response = await _api.put(ApiConfig.customField(id), payload);
    if (!response.success) return _message(response);
    await refresh();
    return null;
  }

  /// Turn a field off. `DELETE` is a soft delete server-side: it flips
  /// `is_active` and leaves every stored value readable, which is why nothing
  /// on the screen calls this Delete.
  Future<String?> deactivateField(String id) async {
    final response = await _api.delete(ApiConfig.customField(id));
    if (!response.success) return _message(response);
    await refresh();
    return null;
  }

  /// Turn a field back on. There is no reactivate endpoint, so this is the
  /// same PUT carrying only `is_active`. See [customFieldActivatePayload] for
  /// why it must not reuse the edit body.
  Future<String?> activateField(String id) =>
      updateField(id, customFieldActivatePayload());
}

final customFieldsProvider =
    AsyncNotifierProvider<CustomFieldsNotifier, CustomFieldsState>(
      CustomFieldsNotifier.new,
    );

// ---------------------------------------------------------------------------
// Macros, the saved replies
// ---------------------------------------------------------------------------

/// The macros visible to the caller, plus the placeholder reference.
///
/// Visibility is the server's decision: `_visible_qs` returns every org macro
/// plus the caller's own personal ones, so nothing here filters rows. A
/// stranger's personal macro never arrives.
class MacrosState {
  const MacrosState({
    this.macros = const [],
    this.placeholders = const [],
    this.orgCount = 0,
    this.personalCount = 0,
    this.inactiveCount = 0,
    this.brokenCount = 0,
  });

  final List<Macro> macros;

  /// `{token, resolves}` pairs from `SUPPORTED_PLACEHOLDERS`. Server-owned, so
  /// the reference the form shows is exactly what the renderer expands.
  final List<MacroPlaceholder> placeholders;

  final int orgCount;
  final int personalCount;
  final int inactiveCount;

  /// Macros carrying a `%token%` the renderer does not know. Those render
  /// literally into a customer reply, which is why they are counted.
  final int brokenCount;
}

class MacroPlaceholder {
  const MacroPlaceholder({required this.token, required this.resolves});

  final String token;
  final String resolves;
}

class MacrosNotifier extends AsyncNotifier<MacrosState> {
  final ApiService _api = ApiService();

  @override
  Future<MacrosState> build() => _fetch();

  Future<void> refresh() async {
    state = const AsyncValue.loading();
    state = await AsyncValue.guard(_fetch);
  }

  /// Unfiltered on purpose. The reply-box picker asks for `?active=true`, but
  /// this screen is the one that turns a macro back on and cannot offer that
  /// for a row it never fetched.
  Future<MacrosState> _fetch() async {
    final response = await _api.get(ApiConfig.macros);
    if (!response.success || response.data == null) {
      throw Exception(response.message ?? 'Failed to load saved replies');
    }
    final body = response.data!;
    final macros = listFromEnvelope(body, const [
      'results',
    ]).map(Macro.fromJson).toList(growable: false);
    final totals = body['totals'];
    final rawPlaceholders = body['placeholders'];
    return MacrosState(
      macros: macros,
      placeholders: rawPlaceholders is List
          ? [
              for (final p in rawPlaceholders)
                if (p is Map)
                  MacroPlaceholder(
                    token: p['token']?.toString() ?? '',
                    resolves: p['resolves']?.toString() ?? '',
                  ),
            ]
          : const [],
      orgCount: _int(totals, 'org', macros.where((m) => !m.isPersonal).length),
      personalCount: _int(
        totals,
        'personal',
        macros.where((m) => m.isPersonal).length,
      ),
      inactiveCount: _int(
        totals,
        'inactive',
        macros.where((m) => !m.isActive).length,
      ),
      brokenCount: _int(
        totals,
        'with_unknown_placeholders',
        macros.where((m) => m.unknownPlaceholders.isNotEmpty).length,
      ),
    );
  }

  Future<String?> createMacro(Map<String, dynamic> payload) async {
    final response = await _api.post(ApiConfig.macros, payload);
    if (!response.success) return _macroMessage(response);
    await refresh();
    return null;
  }

  /// PATCH, not PUT. `MacroDetailView.put` runs the serializer with
  /// `partial=False` and `MacroSerializer` requires both `title` and `body`,
  /// so a PUT missing either is a 400 for no reason the user could see.
  Future<String?> updateMacro(String id, Map<String, dynamic> payload) async {
    final response = await _api.patch(ApiConfig.macro(id), payload);
    if (!response.success) return _macroMessage(response);
    await refresh();
    return null;
  }

  /// Turn an org macro off, or delete a personal one for good. One endpoint;
  /// which happens is the row's scope, decided server-side.
  Future<String?> removeMacro(String id) async {
    final response = await _api.delete(ApiConfig.macro(id));
    if (!response.success) return _macroMessage(response);
    await refresh();
    return null;
  }

  Future<String?> activateMacro(String id) =>
      updateMacro(id, macroActivatePayload());
}

final macrosProvider = AsyncNotifierProvider<MacrosNotifier, MacrosState>(
  MacrosNotifier.new,
);

/// The active macros, for the reply box.
///
/// A separate read from [macrosProvider] rather than a filter over it: the
/// picker wants `?active=true` server-side and must not pull the turned-off
/// rows onto a ticket screen at all, and the two are opened from different
/// places at different times.
final activeMacrosProvider = FutureProvider<List<Macro>>((ref) async {
  final url = Uri.parse(
    ApiConfig.macros,
  ).replace(queryParameters: {'active': 'true'}).toString();
  final response = await ApiService().get(url);
  if (!response.success || response.data == null) {
    throw Exception(response.message ?? 'Failed to load saved replies');
  }
  return listFromEnvelope(response.data!, const [
    'results',
  ]).map(Macro.fromJson).toList(growable: false);
});

/// Expand a macro against a ticket and return the text to insert.
///
/// Server-side substitution: `render_macro` owns the supported token set, and
/// an unknown token is left literal so the agent sees it rather than sending a
/// reply with a silent hole in it. Returns the message on failure instead of
/// throwing, matching the write methods above.
Future<({String? text, String? error})> renderMacro({
  required String macroId,
  required String ticketId,
}) async {
  final response = await ApiService().post(ApiConfig.macroRender(macroId), {
    'case_id': ticketId,
  });
  if (!response.success) {
    return (text: null, error: _macroMessage(response));
  }
  final rendered = response.data?['rendered_body'];
  if (rendered is! String) {
    return (text: null, error: 'The saved reply came back empty.');
  }
  return (text: rendered, error: null);
}

/// Apply a macro's actions to a ticket and say what happened.
///
/// [only] lists the kept action names (`status`, `priority`, `assignees`,
/// `tags`); null applies every action the macro carries. The server checks the
/// ticket's write rule and runs the ticket PATCH's gates, so a refusal (the
/// close approval, a merged ticket) comes back as [error], in its words.
Future<({String? summary, String? error})> applyMacro({
  required String macroId,
  required String ticketId,
  List<String>? only,
}) async {
  final response = await ApiService().post(ApiConfig.macroApply(macroId), {
    'case_id': ticketId,
    'only': ?only,
  });
  if (!response.success) {
    return (summary: null, error: _macroMessage(response));
  }
  return (summary: macroApplySummary(response.data), error: null);
}

/// The macro endpoints answer `{"error": "..."}` and `{"detail": "..."}` where
/// the rest of the app answers `{"errors": ...}`, so [_message] alone would
/// miss both. The wording matters here: "Only admins can manage org-scope
/// macros" is the difference between a bug and a rule.
String _macroMessage(dynamic response) {
  final error = response.data?['error'];
  if (error is String && error.trim().isNotEmpty) return error;
  final detail = response.data?['detail'];
  if (detail is String && detail.trim().isNotEmpty) return detail;
  return _message(response);
}

int _int(dynamic totals, String key, int fallback) {
  if (totals is Map && totals[key] is int) return totals[key] as int;
  return fallback;
}

// ---------------------------------------------------------------------------
// Tags, the labels shared across every record type
// ---------------------------------------------------------------------------

/// Every tag in the org, active and archived, plus the totals above the list.
class TagsState {
  const TagsState({
    this.tags = const [],
    this.count = 0,
    this.active = 0,
    this.unused = 0,
  });

  /// Ranked by [sortedTags]: most used first, then by name.
  final List<Tag> tags;

  /// From the server's `totals`, computed over the org's whole tag set. Note
  /// `unused` counts only ACTIVE tags applied to nothing, so it is not the
  /// same as "safe to remove": nothing removes a tag at all.
  final int count;
  final int active;
  final int unused;

  int get archived => count - active;

  List<TagDuplicateGroup> get duplicates => duplicateTagGroups(tags);
}

class TagsNotifier extends AsyncNotifier<TagsState> {
  final ApiService _api = ApiService();

  @override
  Future<TagsState> build() => _fetch();

  Future<void> refresh() async {
    state = const AsyncValue.loading();
    state = await AsyncValue.guard(_fetch);
  }

  /// `include_archived=true`, because this screen offers turning a tag back on
  /// and cannot do that for a row it never fetched. The record-form tag picker
  /// is a separate read (`tagsLookupProvider`) and keeps the default, so it
  /// never offers a turned-off tag on a new record.
  Future<TagsState> _fetch() async {
    final url = Uri.parse(
      ApiConfig.tags,
    ).replace(queryParameters: {'include_archived': 'true'}).toString();
    final response = await _api.get(url);
    if (!response.success || response.data == null) {
      throw Exception(response.message ?? 'Failed to load tags');
    }
    final body = response.data!;
    final tags = sortedTags(
      listFromEnvelope(body, const [
        'tags',
        'results',
      ]).map(Tag.fromJson).toList(),
    );
    final totals = body['totals'];
    return TagsState(
      tags: tags,
      count: _int(totals, 'count', tags.length),
      active: _int(totals, 'active', tags.where((t) => t.isActive).length),
      unused: _int(
        totals,
        'unused',
        tags.where((t) => t.isActive && t.used == 0).length,
      ),
    );
  }

  /// Create a tag, or revive one that was turned off under the same name.
  ///
  /// `TagsListView.post` does both from one branchless-looking call: an
  /// archived tag whose slug matches is reactivated and answered 200, a genuine
  /// create is answered 201. That difference is worth surfacing, because the
  /// two outcomes are not the same thing. A revived tag arrives with its old
  /// colour and description, and every record that carried it before still
  /// does, so an admin who typed a name expecting a fresh label has just
  /// re-adopted a pile of old ones.
  Future<({String? error, bool revived})> createTag(String name) async {
    final response = await _api.post(ApiConfig.tags, tagCreatePayload(name));
    if (!response.success) return (error: _message(response), revived: false);
    await refresh();
    return (error: null, revived: response.statusCode == 200);
  }

  /// Turn a tag off. A soft archive server-side, which is why nothing on the
  /// screen calls this Delete. See [tagArchiveExplanation].
  Future<String?> archiveTag(String id) async {
    final response = await _api.delete(ApiConfig.tag(id));
    if (!response.success) return _message(response);
    await refresh();
    return null;
  }

  /// Turn an archived tag back on. Its own endpoint, not a PUT: `TagsDetailView
  /// .put` requires a name and would rewrite the row, and this control has
  /// nothing to say about the name.
  Future<String?> restoreTag(String id) async {
    final response = await _api.post(ApiConfig.tagRestore(id), const {});
    if (!response.success) return _message(response);
    await refresh();
    return null;
  }

  /// Merge `from` into `into`, and report how many records changed hands.
  ///
  /// `moved` is the whole point of reporting anything back: a merge that moved
  /// nothing looks identical to one that moved two hundred records, and the
  /// second is the one an admin wants to have seen before they close the page.
  Future<({String? error, int moved})> mergeTags({
    required String from,
    required String into,
  }) async {
    final response = await _api.post(
      ApiConfig.tagMerge(from),
      tagMergePayload(into),
    );
    if (!response.success) return (error: _message(response), moved: 0);
    final moved = response.data?['moved'];
    await refresh();
    return (error: null, moved: moved is int ? moved : 0);
  }
}

/// Named for the screen rather than the resource, because `tagsProvider` is
/// already the record-form picker's flattened list in `lookup_provider.dart`
/// and six screens read it. These are two reads of one endpoint with different
/// contracts, not one list two places want, so they keep separate names.
final tagSettingsProvider = AsyncNotifierProvider<TagsNotifier, TagsState>(
  TagsNotifier.new,
);

// ---------------------------------------------------------------------------
// Routing rules, which decide who a new ticket lands on
// ---------------------------------------------------------------------------

/// The rules in the order the engine runs them, plus the org totals.
class RoutingRulesState {
  const RoutingRulesState({
    this.rules = const [],
    this.count = 0,
    this.active = 0,
    this.unroutedLast30d = 0,
  });

  /// Server-ordered by `priority_order`, then creation time. Not re-sorted
  /// here: the order IS the behaviour, and a client that re-sorted would show
  /// a different program from the one that runs.
  final List<RoutingRule> rules;

  final int count;
  final int active;

  /// Tickets in the last thirty days that no rule matched at all. The number
  /// the page exists to make visible: a rule that matched but could not assign
  /// is not counted here, it shows on its own row instead.
  final int unroutedLast30d;

  /// Active rules that match tickets and can assign none of them.
  int get dead => rules.where((r) => r.assignsNobody).length;
}

class RoutingRulesNotifier extends AsyncNotifier<RoutingRulesState> {
  final ApiService _api = ApiService();

  @override
  Future<RoutingRulesState> build() => _fetch();

  Future<void> refresh() async {
    state = const AsyncValue.loading();
    state = await AsyncValue.guard(_fetch);
  }

  Future<RoutingRulesState> _fetch() async {
    final response = await _api.get(ApiConfig.routingRules);
    if (!response.success || response.data == null) {
      throw Exception(response.message ?? 'Failed to load routing rules');
    }
    final body = response.data!;
    final rules = listFromEnvelope(body, const [
      'rules',
    ]).map(RoutingRule.fromJson).toList(growable: false);
    final totals = body['totals'];
    return RoutingRulesState(
      rules: rules,
      count: _int(totals, 'count', rules.length),
      active: _int(totals, 'active', rules.where((r) => r.isActive).length),
      unroutedLast30d: _int(totals, 'unrouted_last_30d', 0),
    );
  }

  Future<String?> createRule(Map<String, dynamic> payload) async {
    final response = await _api.post(ApiConfig.routingRules, payload);
    if (!response.success) return _message(response);
    await refresh();
    return null;
  }

  Future<String?> updateRule(String id, Map<String, dynamic> payload) async {
    final response = await _api.put(ApiConfig.routingRule(id), payload);
    if (!response.success) return _message(response);
    await refresh();
    return null;
  }

  /// Turn a rule off. Distinct from [deleteRule] and the only reversible one of
  /// the two, which is why the delete dialog points at it.
  Future<String?> deactivateRule(String id) =>
      updateRule(id, {'is_active': false});

  Future<String?> activateRule(String id) =>
      updateRule(id, routingActivatePayload());

  /// Permanent. `RoutingRuleDetailView.delete` calls `obj.delete()`.
  Future<String?> deleteRule(String id) async {
    final response = await _api.delete(ApiConfig.routingRule(id));
    if (!response.success) return _message(response);
    await refresh();
    return null;
  }
}

final routingRulesProvider =
    AsyncNotifierProvider<RoutingRulesNotifier, RoutingRulesState>(
      RoutingRulesNotifier.new,
    );

// ---------------------------------------------------------------------------
// Escalation policies, which decide what happens when a ticket misses its SLA
// ---------------------------------------------------------------------------

/// The policies, worst priority first, and what they add up to.
class EscalationState {
  const EscalationState({this.policies = const []});

  /// Sorted by severity in [_fetch], not left in the server's alphabetical
  /// `ordering = ("priority",)`.
  final List<EscalationPolicy> policies;

  /// Policies that do nothing at all when a ticket breaches.
  int get dead => policies.where((p) => p.isDead).length;

  /// Breaches in the last 30 days that reached nobody. Derived from the same
  /// rows the list shows, so the headline can never disagree with the cards
  /// under it.
  int get breachesGoingNowhere =>
      policies.fold(0, (sum, p) => sum + p.breachesGoingNowhere);

  List<String> get unconfigured => unconfiguredEscalationPriorities(policies);
}

class EscalationNotifier extends AsyncNotifier<EscalationState> {
  final ApiService _api = ApiService();

  @override
  Future<EscalationState> build() => _fetch();

  Future<void> refresh() async {
    state = const AsyncValue.loading();
    state = await AsyncValue.guard(_fetch);
  }

  Future<EscalationState> _fetch() async {
    final response = await _api.get(ApiConfig.escalationPolicies);
    if (!response.success || response.data == null) {
      throw Exception(response.message ?? 'Failed to load escalation policies');
    }
    final policies = listFromEnvelope(response.data!, const [
      'policies',
    ]).map(EscalationPolicy.fromJson).toList();
    return EscalationState(policies: sortedEscalationPolicies(policies));
  }

  Future<String?> createPolicy(Map<String, dynamic> payload) async {
    final response = await _api.post(ApiConfig.escalationPolicies, payload);
    if (!response.success) return _message(response);
    await refresh();
    return null;
  }

  Future<String?> updatePolicy(String id, Map<String, dynamic> payload) async {
    final response = await _api.put(ApiConfig.escalationPolicy(id), payload);
    if (!response.success) return _message(response);
    await refresh();
    return null;
  }

  /// Stop the policy firing, reversibly. The alternative [deletePolicy] points
  /// at, and the reason that control has to explain itself.
  Future<String?> deactivatePolicy(String id) =>
      updatePolicy(id, escalationActivePayload(false));

  Future<String?> activatePolicy(String id) =>
      updatePolicy(id, escalationActivePayload(true));

  /// Permanent. `EscalationPolicyDetailView.delete` calls `obj.delete()`.
  Future<String?> deletePolicy(String id) async {
    final response = await _api.delete(ApiConfig.escalationPolicy(id));
    if (!response.success) return _message(response);
    await refresh();
    return null;
  }
}

final escalationProvider =
    AsyncNotifierProvider<EscalationNotifier, EscalationState>(
      EscalationNotifier.new,
    );

// ---------------------------------------------------------------------------
// Business hours, the calendar every SLA target is measured against
// ---------------------------------------------------------------------------

/// The org's default calendar.
///
/// Reading is open to any member: `BusinessCalendarView.get` carries only
/// `IsAuthenticated, HasOrgContext`, and the admin check sits on the PUT and on
/// both holiday verbs. So the screen shows the week to everyone and hides only
/// the controls, which is what the web does.
class BusinessHoursNotifier extends AsyncNotifier<BusinessCalendar> {
  final ApiService _api = ApiService();

  @override
  Future<BusinessCalendar> build() => _fetch();

  Future<void> refresh() async {
    state = const AsyncValue.loading();
    state = await AsyncValue.guard(_fetch);
  }

  /// The GET creates the calendar if the org has none, so a first read is a
  /// write server-side. Nothing here depends on that; it is why the screen
  /// never has an empty state.
  Future<BusinessCalendar> _fetch() async {
    final response = await _api.get(ApiConfig.businessCalendar);
    if (!response.success || response.data == null) {
      throw Exception(response.message ?? 'Failed to load business hours');
    }
    return BusinessCalendar.fromJson(response.data!);
  }

  /// Save the week, the name and the timezone.
  ///
  /// PUTs the calendar's own url. The id comes from the loaded calendar rather
  /// than from anything the screen holds, so there is nothing to point at
  /// another org's row.
  Future<String?> saveHours({
    required List<BusinessDay> days,
    required String name,
    required String timezone,
  }) async {
    final calendar = state.value;
    if (calendar == null) return 'The calendar has not loaded yet.';
    final response = await _api.put(
      ApiConfig.businessCalendarDetail(calendar.id),
      businessHoursPayload(days: days, name: name, timezone: timezone),
    );
    if (!response.success) return _businessMessage(response);
    await refresh();
    return null;
  }

  /// Add a holiday, and say whether it was actually added.
  ///
  /// `existingName` is non-null when the POST answered 200 rather than 201,
  /// which means the date was already on the calendar and the row that came
  /// back is the one that was already there. The name typed into the form was
  /// discarded. That is a normal outcome server-side, but it is not the same
  /// event as adding a holiday, and reporting both as "added" is how a name
  /// silently fails to change.
  Future<({String? error, String? existingName})> addHoliday({
    required String date,
    required String name,
  }) async {
    final calendar = state.value;
    if (calendar == null) {
      return (error: 'The calendar has not loaded yet.', existingName: null);
    }
    final response = await _api.post(
      ApiConfig.businessHolidays(calendar.id),
      holidayPayload(date: date, name: name),
    );
    if (!response.success) {
      return (error: _businessMessage(response), existingName: null);
    }
    final wasExisting = response.statusCode == 200;
    final returnedName = response.data?['name']?.toString();
    await refresh();
    return (
      error: null,
      existingName: wasExisting ? (returnedName ?? name) : null,
    );
  }

  /// Permanent. `BusinessHolidayDetailView.delete` calls `holiday.delete()`.
  Future<String?> removeHoliday(String holidayId) async {
    final calendar = state.value;
    if (calendar == null) return 'The calendar has not loaded yet.';
    final response = await _api.delete(
      ApiConfig.businessHoliday(calendar.id, holidayId),
    );
    if (!response.success) return _businessMessage(response);
    await refresh();
    return null;
  }
}

/// The business-hours views answer `{"error": "Only admins can update business
/// hours."}`, a string under `error` where the rest of the app puts a map under
/// `errors`. [_message] alone would miss it and report "Something went wrong"
/// for the one refusal a user can act on.
String _businessMessage(dynamic response) {
  final error = response.data?['error'];
  if (error is String && error.trim().isNotEmpty) return error;
  return _message(response);
}

final businessHoursProvider =
    AsyncNotifierProvider<BusinessHoursNotifier, BusinessCalendar>(
      BusinessHoursNotifier.new,
    );

// ---------------------------------------------------------------------------
// Reopen policy, whether a customer's reply brings a closed ticket back
// ---------------------------------------------------------------------------

/// The org's reopen policy and its three 30-day metrics.
///
/// **`ReopenPolicyView.get` is admin-only**, unlike every other read in this
/// cluster, so the screen gates the read on role rather than showing a member a
/// 403 they can do nothing about.
class ReopenPolicyNotifier extends AsyncNotifier<ReopenPolicy> {
  final ApiService _api = ApiService();

  @override
  Future<ReopenPolicy> build() => _fetch();

  Future<void> refresh() async {
    state = const AsyncValue.loading();
    state = await AsyncValue.guard(_fetch);
  }

  Future<ReopenPolicy> _fetch() async {
    final response = await _api.get(ApiConfig.reopenPolicy);
    if (!response.success || response.data == null) {
      throw Exception(response.message ?? 'Failed to load the reopen policy');
    }
    return ReopenPolicy.fromJson(response.data!);
  }

  Future<String?> savePolicy(Map<String, dynamic> payload) async {
    final response = await _api.put(ApiConfig.reopenPolicy, payload);
    if (!response.success) return _message(response);
    await refresh();
    return null;
  }
}

final reopenPolicyProvider =
    AsyncNotifierProvider<ReopenPolicyNotifier, ReopenPolicy>(
      ReopenPolicyNotifier.new,
    );

// ---------------------------------------------------------------------------
// API tokens, the admin's org-wide oversight
// ---------------------------------------------------------------------------

/// Every token in the org, with the totals the screen ranks by.
///
/// **`OrgAccessTokenListView` is admin-only**, like the reopen policy and
/// unlike the rest of this cluster, so the screen gates the read on role.
///
/// A raw token value never lives in this state. [createToken] returns it to its
/// one caller and the screen shows it once; nothing here keeps a copy, and
/// nothing anywhere logs one.
class AccessTokensState {
  const AccessTokensState({
    this.tokens = const [],
    this.totals = const TokenTotals(),
  });

  final List<AccessToken> tokens;
  final TokenTotals totals;

  /// The ids "revoke them all" acts on, re-derived from the rows the server
  /// sent rather than from anything a screen holds.
  List<String> get orphanedIds => orphanedTokenIds(tokens);
}

class AccessTokensNotifier extends AsyncNotifier<AccessTokensState> {
  final ApiService _api = ApiService();

  @override
  Future<AccessTokensState> build() => _fetch();

  Future<void> refresh() async {
    state = const AsyncValue.loading();
    state = await AsyncValue.guard(_fetch);
  }

  Future<AccessTokensState> _fetch() async {
    final response = await _api.get(ApiConfig.orgTokens);
    if (!response.success || response.data == null) {
      throw Exception(response.message ?? 'Failed to load tokens');
    }
    final rows = listFromEnvelope(response.data!, [
      'tokens',
    ]).map(AccessToken.fromJson).toList();
    return AccessTokensState(
      tokens: sortedTokens(rows),
      totals: TokenTotals.fromJson(
        (response.data!['totals'] as Map?)?.cast<String, dynamic>() ?? const {},
      ),
    );
  }

  /// Create a token for the signed-in user and return the raw value, once.
  ///
  /// Posts to the SELF-scoped endpoint: the owner is the caller's own profile,
  /// set server-side. `value` is the only time the raw token exists on this
  /// device; the caller shows it and drops it.
  Future<({String? error, String? value, String? name})> createToken({
    required String name,
    required String expiryChoice,
    required String accessChoice,
  }) async {
    final response = await _api.post(
      ApiConfig.profileTokens,
      tokenCreatePayload(
        name: name,
        expiryChoice: expiryChoice,
        accessChoice: accessChoice,
        now: DateTime.now(),
      ),
    );
    if (!response.success) {
      return (error: _message(response), value: null, name: null);
    }
    final raw = response.data?['token']?.toString();
    await refresh();
    if (raw == null || raw.isEmpty) {
      // A 2xx with no value should not happen, and silently showing nothing
      // would look like the token was never created. It was.
      return (
        error:
            'The token was created but its value did not come back. '
            'Revoke it and create another.',
        value: null,
        name: null,
      );
    }
    return (
      error: null,
      value: raw,
      name: response.data?['name']?.toString() ?? name.trim(),
    );
  }

  /// Revoke one token: admin-scoped, so an admin can retire a colleague's.
  /// Idempotent server-side.
  Future<String?> revokeToken(String id) async {
    final response = await _api.delete(ApiConfig.orgToken(id));
    if (!response.success) return _message(response);
    await refresh();
    return null;
  }

  /// Revoke every live token on a deactivated owner.
  ///
  /// The ids come from the loaded rows, not from the screen, so this can only
  /// ever act on the orphaned set the server itself reported. One failure does
  /// not strand the rest; the count returned is what actually went through.
  Future<({String? error, int revoked})> revokeOrphaned() async {
    final ids = state.value?.orphanedIds ?? const [];
    if (ids.isEmpty) return (error: null, revoked: 0);

    var done = 0;
    String? failure;
    for (final id in ids) {
      final response = await _api.delete(ApiConfig.orgToken(id));
      if (response.success) {
        done += 1;
      } else {
        failure ??= _message(response);
      }
    }
    await refresh();
    return (error: done == 0 ? failure : null, revoked: done);
  }
}

final accessTokensProvider =
    AsyncNotifierProvider<AccessTokensNotifier, AccessTokensState>(
      AccessTokensNotifier.new,
    );

// ---------------------------------------------------------------------------
// API tokens, your own
// ---------------------------------------------------------------------------

/// The tokens you have issued yourself.
///
/// Separate from [AccessTokensNotifier] rather than a flag on it, because the
/// endpoints are separate on purpose: `/api/profile/tokens/` filters on
/// `profile=request.profile` server-side and is open to every member, while
/// `/api/org/tokens/` is admin-only and org-wide. One notifier reaching for
/// whichever it could is how a self guard stops being one.
///
/// This is the half neither client had. The endpoint has always been
/// self-service, and the only token screen either app carried was the admin
/// oversight list, so a member could not issue themselves a token at all.
class MyAccessTokensNotifier extends AsyncNotifier<List<AccessToken>> {
  final ApiService _api = ApiService();

  @override
  Future<List<AccessToken>> build() => _fetch();

  Future<void> refresh() async {
    state = const AsyncValue.loading();
    state = await AsyncValue.guard(_fetch);
  }

  Future<List<AccessToken>> _fetch() async {
    final response = await _api.get(ApiConfig.profileTokens);
    if (!response.success || response.data == null) {
      throw Exception(response.message ?? 'Failed to load your tokens');
    }
    return sortedTokens(
      listFromEnvelope(response.data!, [
        'tokens',
      ]).map(AccessToken.fromJson).toList(),
    );
  }

  /// Create one for yourself and return the raw value, once. Same endpoint the
  /// admin screen posts to: creating has always been self-scoped.
  Future<({String? error, String? value, String? name})> createToken({
    required String name,
    required String expiryChoice,
    required String accessChoice,
  }) async {
    final response = await _api.post(
      ApiConfig.profileTokens,
      tokenCreatePayload(
        name: name,
        expiryChoice: expiryChoice,
        accessChoice: accessChoice,
        now: DateTime.now(),
      ),
    );
    if (!response.success) {
      return (error: _message(response), value: null, name: null);
    }
    final raw = response.data?['token']?.toString();
    await refresh();
    if (raw == null || raw.isEmpty) {
      return (
        error:
            'The token was created but its value did not come back. '
            'Revoke it and create another.',
        value: null,
        name: null,
      );
    }
    return (
      error: null,
      value: raw,
      name: response.data?['name']?.toString() ?? name.trim(),
    );
  }

  /// Revoke one of your own. The self-scoped path, so somebody else's id 404s
  /// here instead of being revoked; the admin path would 403 a member out of
  /// retiring their own credential.
  Future<String?> revokeToken(String id) async {
    final response = await _api.delete(ApiConfig.profileToken(id));
    if (!response.success) return _message(response);
    await refresh();
    return null;
  }
}

final myAccessTokensProvider =
    AsyncNotifierProvider<MyAccessTokensNotifier, List<AccessToken>>(
      MyAccessTokensNotifier.new,
    );

// ---------------------------------------------------------------------------
// Organization settings, and the vertical packs that seed one
// ---------------------------------------------------------------------------

/// The org's own settings.
///
/// The read is open to any member; every write is admin-only server-side.
///
/// Kept apart from [orgPacksProvider] rather than fetched together, because the
/// ticket screen reads this to seed its cascade checkbox and has no use for a
/// pack list. One question, one request.
class OrgSettingsNotifier extends AsyncNotifier<OrgSettings> {
  final ApiService _api = ApiService();

  @override
  Future<OrgSettings> build() => _fetch();

  Future<void> refresh() async {
    state = const AsyncValue.loading();
    state = await AsyncValue.guard(_fetch);
  }

  Future<OrgSettings> _fetch() async {
    final response = await _api.get(ApiConfig.orgSettings);
    if (!response.success || response.data == null) {
      throw Exception(
        response.message ?? 'Failed to load organization settings',
      );
    }
    return OrgSettings.fromJson(response.data!);
  }

  /// Save the org. Admin-only server-side; a member's PATCH is a 403 whatever
  /// this app shows.
  Future<String?> save(Map<String, dynamic> payload) async {
    final response = await _api.patch(ApiConfig.orgSettings, payload);
    if (!response.success) return _orgMessage(response);
    await refresh();
    return null;
  }

  /// Apply a pack. Additive-only and safe to repeat, so there is deliberately
  /// no guard against re-applying the one already applied.
  ///
  /// The refresh matters: applying writes `org.vertical`, which is what the
  /// screen reads to mark a pack as applied.
  Future<({String? error, PackApplyReport? report})> applyPack(
    String packId,
  ) async {
    final response = await _api.post(ApiConfig.packApply(packId), const {});
    if (!response.success || response.data == null) {
      return (error: _orgMessage(response), report: null);
    }
    final report = PackApplyReport.fromJson(response.data!);
    await refresh();
    return (error: null, report: report);
  }

  /// Delete the demo records a pack created for this org.
  ///
  /// `retained` counts sample records kept because real work has since been
  /// attached to them. That is a normal outcome, not a partial failure, and
  /// reporting only `deleted` would show a smaller number than expected with
  /// no explanation.
  Future<({String? error, int deleted, int retained})> clearSampleData() async {
    final response = await _api.delete(ApiConfig.packSampleData);
    if (!response.success || response.data == null) {
      return (error: _orgMessage(response), deleted: 0, retained: 0);
    }
    final retainedByType =
        (response.data!['retained_by_type'] as Map?)?.values ?? const [];
    var retained = 0;
    for (final value in retainedByType) {
      if (value is num) retained += value.round();
    }
    await refresh();
    return (
      error: null,
      deleted: (response.data!['deleted'] as num?)?.round() ?? 0,
      retained: retained,
    );
  }
}

/// These endpoints refuse with a bare `{"error": "..."}` (the pack views and
/// `OrgSettingsView` both do) rather than the `errors` map `_message` reads, so
/// the reason reaches the user instead of a generic failure.
String _orgMessage(dynamic response) {
  final error = response.data?['error'];
  if (error is String && error.trim().isNotEmpty) return error;
  return _message(response);
}

final orgSettingsProvider =
    AsyncNotifierProvider<OrgSettingsNotifier, OrgSettings>(
      OrgSettingsNotifier.new,
    );

/// The vertical packs available to apply.
///
/// Its own provider, so the organization screen is the only thing that pays for
/// it. A failure yields an empty list rather than propagating: a pack list that
/// will not load must not be the reason the org details do not render, which is
/// the fallback the web load already established for the identical call.
final orgPacksProvider = FutureProvider<List<VerticalPack>>((ref) async {
  final response = await ApiService().get(ApiConfig.packs);
  if (!response.success || response.data == null) return const [];
  return listFromEnvelope(response.data!, [
    'packs',
  ]).map(VerticalPack.fromJson).toList();
});

// ---------------------------------------------------------------------------
// Inbound email, the addresses that turn mail into tickets
// ---------------------------------------------------------------------------

/// The org's inbound addresses, plus what they add up to.
///
/// The read is open to any member. Every write is admin-only server-side, so
/// the screen hides the controls and the API refuses regardless.
class MailboxesState {
  const MailboxesState({
    this.mailboxes = const [],
    this.count = 0,
    this.casesLast30d = 0,
  });

  /// Live addresses first, then alphabetically.
  final List<Mailbox> mailboxes;

  /// From the server's `totals`. `active` is deliberately not held: it counts
  /// `is_active` and the screen asks how many actually open tickets, which is a
  /// different number. See [delivering].
  final int count;

  /// Tickets opened from any of these addresses in 30 days, deduplicated
  /// server-side so an address cross-posted to two mailboxes counts once.
  final int casesLast30d;

  int get delivering => deliveringMailboxCount(mailboxes);

  List<Mailbox> get silent => silentMailboxes(mailboxes);
}

class MailboxesNotifier extends AsyncNotifier<MailboxesState> {
  final ApiService _api = ApiService();

  @override
  Future<MailboxesState> build() => _fetch();

  Future<void> refresh() async {
    state = const AsyncValue.loading();
    state = await AsyncValue.guard(_fetch);
  }

  Future<MailboxesState> _fetch() async {
    final response = await _api.get(ApiConfig.mailboxes);
    if (!response.success || response.data == null) {
      throw Exception(response.message ?? 'Failed to load inbound email');
    }
    final body = response.data!;
    final rows = listFromEnvelope(body, const [
      'mailboxes',
    ]).map(Mailbox.fromJson).toList();
    final totals = body['totals'];
    return MailboxesState(
      mailboxes: sortedMailboxes(rows),
      count: _int(totals, 'count', rows.length),
      casesLast30d: _int(totals, 'cases_last_30d', 0),
    );
  }

  Future<String?> createMailbox(Map<String, dynamic> payload) async {
    final response = await _api.post(ApiConfig.mailboxes, payload);
    if (!response.success) return _message(response);
    await refresh();
    return null;
  }

  Future<String?> updateMailbox(String id, Map<String, dynamic> payload) async {
    final response = await _api.put(ApiConfig.mailbox(id), payload);
    if (!response.success) return _message(response);
    await refresh();
    return null;
  }

  /// Stop an address opening tickets, reversibly. The alternative
  /// [deleteMailbox] points at, and the reason that control explains itself.
  Future<String?> deactivateMailbox(String id) =>
      updateMailbox(id, mailboxActivePayload(false));

  Future<String?> activateMailbox(String id) =>
      updateMailbox(id, mailboxActivePayload(true));

  /// Permanent. `InboundMailboxDetailView.delete` calls `obj.delete()`, which
  /// takes the topic pin with it.
  Future<String?> deleteMailbox(String id) async {
    final response = await _api.delete(ApiConfig.mailbox(id));
    if (!response.success) return _message(response);
    await refresh();
    return null;
  }
}

final mailboxesProvider =
    AsyncNotifierProvider<MailboxesNotifier, MailboxesState>(
      MailboxesNotifier.new,
    );

// ---------------------------------------------------------------------------
// Approval rules, the gates on a ticket close
// ---------------------------------------------------------------------------

/// The rules, plus the org's waiting approvals.
class ApprovalRulesState {
  const ApprovalRulesState({
    this.rules = const [],
    this.count = 0,
    this.active = 0,
    this.pending = 0,
  });

  /// Server-ordered by `-created_at`, which is also the tie-break between rules
  /// with identical conditions, so the order is left alone.
  final List<ApprovalRule> rules;

  final int count;
  final int active;

  /// Approvals waiting on any of these rules right now.
  final int pending;

  /// Active rules another active rule always beats to the case.
  Set<String> get shadowed => shadowedRuleIds(rules);

  /// Active rules gating closes that nobody in the org can clear.
  int get stranding => rules.where((r) => r.clearableByNobody).length;
}

class ApprovalRulesNotifier extends AsyncNotifier<ApprovalRulesState> {
  final ApiService _api = ApiService();

  @override
  Future<ApprovalRulesState> build() => _fetch();

  Future<void> refresh() async {
    state = const AsyncValue.loading();
    state = await AsyncValue.guard(_fetch);
  }

  Future<ApprovalRulesState> _fetch() async {
    final response = await _api.get(ApiConfig.approvalRules);
    if (!response.success || response.data == null) {
      throw Exception(response.message ?? 'Failed to load approval rules');
    }
    final body = response.data!;
    final rules = listFromEnvelope(body, const [
      'rules',
    ]).map(ApprovalRule.fromJson).toList(growable: false);
    final totals = body['totals'];
    return ApprovalRulesState(
      rules: rules,
      count: _int(totals, 'count', rules.length),
      active: _int(totals, 'active', rules.where((r) => r.isActive).length),
      pending: _int(totals, 'pending', 0),
    );
  }

  Future<String?> createRule(Map<String, dynamic> payload) async {
    final response = await _api.post(ApiConfig.approvalRules, payload);
    if (!response.success) return _message(response);
    await refresh();
    return null;
  }

  Future<String?> updateRule(String id, Map<String, dynamic> payload) async {
    final response = await _api.put(ApiConfig.approvalRule(id), payload);
    if (!response.success) return _message(response);
    await refresh();
    return null;
  }

  Future<String?> deactivateRule(String id) =>
      updateRule(id, approvalRuleActivePayload(false));

  Future<String?> activateRule(String id) =>
      updateRule(id, approvalRuleActivePayload(true));

  /// Delete a rule, or turn it off, depending on what the backend decides.
  ///
  /// `ApprovalRuleDetailView.delete` destroys a rule that has never gated a
  /// close and soft-disables one that has, because `Approval.rule` is
  /// `on_delete=PROTECT`. Both answer 2xx, so "did it throw" cannot tell them
  /// apart: the hard delete answers 204 with no body, and the soft one answers
  /// 200 carrying `is_active: false`. Reported rather than assumed, so an admin
  /// is never told "deleted" about a row still in the list.
  Future<({String? error, bool turnedOff})> deleteRule(String id) async {
    final response = await _api.delete(ApiConfig.approvalRule(id));
    if (!response.success) {
      return (error: _message(response), turnedOff: false);
    }
    final turnedOff = approvalRuleWasTurnedOff(response.data);
    await refresh();
    return (error: null, turnedOff: turnedOff);
  }
}

final approvalRulesProvider =
    AsyncNotifierProvider<ApprovalRulesNotifier, ApprovalRulesState>(
      ApprovalRulesNotifier.new,
    );

/// The IANA zones the org form's timezone picker may offer.
///
/// Fetched once per session. A failure falls back to UTC alone rather than an
/// empty picker, because a picker with no entries cannot save at all.
final orgTimezonesProvider = FutureProvider<List<TimezoneOption>>((ref) async {
  final response = await ApiService().get(ApiConfig.timezones);
  if (!response.success || response.data == null) {
    return const [TimezoneOption(name: 'UTC', offsetMinutes: 0)];
  }
  final zones = listFromEnvelope(response.data!, [
    'timezones',
  ]).map(TimezoneOption.fromJson).toList();
  return zones.isEmpty
      ? const [TimezoneOption(name: 'UTC', offsetMinutes: 0)]
      : zones;
});
