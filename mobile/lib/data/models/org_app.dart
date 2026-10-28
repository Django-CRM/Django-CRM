import '../api/schema.g.dart' as api;
import 'custom_field_definition.dart' show customFieldTargets;

/// An industry app's state in this org.
enum AppStatus {
  notInstalled('not_installed'),
  installed('installed'),
  removed('removed');

  const AppStatus(this.value);

  final String value;

  static AppStatus fromString(Object? value) => AppStatus.values.firstWhere(
    (s) => s.value == value,
    orElse: () => AppStatus.notInstalled,
  );
}

/// What an admin can do to an app. [value] is the URL segment and the
/// preview's `?action=`.
enum AppAction {
  install('install'),
  upgrade('upgrade'),
  remove('remove');

  const AppAction(this.value);

  final String value;

  static AppAction fromString(Object? value, AppAction fallback) => AppAction
      .values
      .firstWhere((a) => a.value == value, orElse: () => fallback);
}

/// One row of `GET /api/apps/`. Versions are integers on the wire and kept
/// here as text.
class OrgApp {
  const OrgApp({
    required this.id,
    required this.name,
    this.description = '',
    this.version = '',
    this.status = AppStatus.notInstalled,
    this.installedVersion,
    this.updateAvailable = false,
    this.skippedModules = const [],
    this.installedAt,
    this.installedByName,
  });

  final String id;
  final String name;
  final String description;
  final String version;
  final AppStatus status;
  final String? installedVersion;
  final bool updateAvailable;

  /// Module keys whose parts were skipped because the module was off.
  final List<String> skippedModules;

  final DateTime? installedAt;
  final String? installedByName;

  /// Reads an `IndustryApp`, the row the list and every action answer carry.
  factory OrgApp.fromJson(Map<String, dynamic> json) {
    final w = api.IndustryApp.fromJson(json);
    final byName = w.installedByDetails?.name?.trim() ?? '';
    return OrgApp(
      id: w.id ?? '',
      name: w.name ?? '',
      description: w.description ?? '',
      version: w.version?.toString() ?? '',
      status: AppStatus.fromString(w.status),
      installedVersion: w.installedVersion?.toString(),
      updateAvailable: w.updateAvailable ?? false,
      skippedModules: [
        for (final key in w.skippedModules ?? const <String>[])
          if (key.isNotEmpty) key,
      ],
      installedAt: DateTime.tryParse(w.installedAt ?? ''),
      installedByName: byName.isEmpty ? null : byName,
    );
  }

  /// The actions offered, in button order. The server sets
  /// `update_available` for a newer version and for a skipped module that is
  /// now on.
  List<AppAction> get actions {
    if (status != AppStatus.installed) return const [AppAction.install];
    return [if (updateAvailable) AppAction.upgrade, AppAction.remove];
  }
}

/// `GET /api/apps/`, tagged with the org it was asked for.
class OrgAppsAnswer {
  const OrgAppsAnswer({this.orgId, this.apps = const []});

  final String? orgId;
  final List<OrgApp> apps;

  /// Reads an `AppListResponse`.
  factory OrgAppsAnswer.fromJson(Map<String, dynamic> json, {String? orgId}) {
    final w = api.AppListResponse.fromJson(json);
    return OrgAppsAnswer(
      orgId: orgId,
      apps: [
        for (final r in w.apps ?? const <api.IndustryApp>[])
          if (r.id?.isNotEmpty ?? false) OrgApp.fromJson(r.json),
      ],
    );
  }

  /// This answer with [app] in place of the row of the same id.
  OrgAppsAnswer withApp(OrgApp app) => OrgAppsAnswer(
    orgId: orgId,
    apps: [for (final a in apps) a.id == app.id ? app : a],
  );
}

/// One line of a preview: what happens to one wording key, field, pipeline,
/// stage, tag, source or product.
class AppPreviewItem {
  const AppPreviewItem({
    required this.type,
    required this.label,
    required this.effect,
    this.target,
  });

  final String type;
  final String label;
  final String effect;
  final String? target;

  /// Reads an `AppPreviewItem`.
  factory AppPreviewItem.fromJson(Map<String, dynamic> json) {
    final w = api.AppPreviewItem.fromJson(json);
    final target = w.target ?? '';
    return AppPreviewItem(
      type: w.type ?? '',
      label: w.label ?? '',
      effect: w.effect ?? '',
      target: target.isEmpty ? null : target,
    );
  }
}

/// A preview's items of one type, under the title the sheet shows.
typedef AppPreviewGroup = ({String title, List<AppPreviewItem> items});

const _typeOrder = [
  'wording',
  'object',
  'field',
  'pipeline',
  'stage',
  'tag',
  'source',
  'product',
];

const _typeTitles = {
  'wording': 'Wording',
  'object': 'Objects',
  'field': 'Fields',
  'pipeline': 'Pipelines',
  'stage': 'Stages',
  'tag': 'Tags',
  'source': 'Lead sources',
  'product': 'Products',
};

/// `GET /api/apps/<id>/preview/?action=`: what an action would do, and
/// whether sample records may come with an install.
class AppPreview {
  const AppPreview({
    required this.action,
    this.items = const [],
    this.sampleOffered = false,
    this.sampleDefaultOn = false,
    this.sampleRecords = 0,
  });

  final AppAction action;
  final List<AppPreviewItem> items;
  final bool sampleOffered;
  final bool sampleDefaultOn;
  final int sampleRecords;

  /// Reads an `AppPreviewResponse`.
  factory AppPreview.fromJson(Map<String, dynamic> json, AppAction asked) {
    final w = api.AppPreviewResponse.fromJson(json);
    final sample = w.sampleData;
    return AppPreview(
      action: AppAction.fromString(w.action, asked),
      items: [
        for (final r in w.items ?? const <api.AppPreviewItem>[])
          AppPreviewItem.fromJson(r.json),
      ],
      sampleOffered: sample?.offered ?? false,
      sampleDefaultOn: sample?.defaultOn ?? false,
      sampleRecords: sample?.records ?? 0,
    );
  }

  /// The items grouped by type in a fixed order, served order within a group.
  /// A type this build does not know follows the known ones, titled by type.
  List<AppPreviewGroup> get groups {
    final byType = <String, List<AppPreviewItem>>{};
    for (final item in items) {
      byType.putIfAbsent(item.type, () => []).add(item);
    }
    final types = [
      ..._typeOrder.where(byType.containsKey),
      ...byType.keys.where((t) => !_typeOrder.contains(t)),
    ];
    return [
      for (final t in types)
        (title: _typeTitles[t] ?? _sentence(t), items: byType[t]!),
    ];
  }

  /// Stages new to a pipeline the org already has, which an update leaves
  /// for the admin to add.
  bool get hasHandWork => items.any((i) => i.effect == 'add_by_hand');
}

/// The short word beside a preview row.
String appEffectLabel(String effect) => switch (effect) {
  'add' => 'New',
  'keep' => 'Kept',
  'turn_off' => 'Turned off',
  'turn_on' => 'Back on',
  'restore_wording' => 'Back to default',
  'skipped_module' => 'Module off',
  'add_by_hand' => 'Add by hand',
  'restore' => 'Comes back',
  'conflict' => 'Not added',
  _ => _sentence(effect),
};

/// A line under a row that its short label alone would not explain.
String? appEffectNote(String effect) => switch (effect) {
  'conflict' => "None of the app's objects is added",
  _ => null,
};

const _targetLabels = {
  'lead': 'Leads',
  'contact': 'Contacts',
  'account': 'Accounts',
  'deal': 'Deals',
  'opportunity': 'Deals',
  'ticket': 'Tickets',
  'case': 'Tickets',
  'task': 'Tasks',
  'invoice': 'Invoices',
  'estimate': 'Estimates',
  'recurring_invoice': 'Recurring invoices',
  'recurringinvoice': 'Recurring invoices',
};

/// "Leads" for `lead` or `Lead`; an unknown target reads as itself.
String appTargetLabel(String target) =>
    customFieldTargets[target] ??
    _targetLabels[target.toLowerCase()] ??
    _sentence(target);

String _sentence(String raw) {
  final text = raw.replaceAll('_', ' ').trim();
  return text.isEmpty ? text : '${text[0].toUpperCase()}${text.substring(1)}';
}
