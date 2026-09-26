import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../config/api_config.dart';
import '../services/api_service.dart';

/// The six lists a saved view can belong to, by the name the API uses.
///
/// Mirrors `LISTS` in `backend/common/saved_views.py` and `SAVED_VIEW_KEYS` in
/// `frontend/src/lib/server/v2/saved-views.js`.
enum SavedViewList {
  leads('leads'),
  contacts('contacts'),
  accounts('accounts'),
  deals('opportunities'),
  tickets('cases'),
  invoices('invoices');

  const SavedViewList(this.module);

  /// The `module` the API files a view under.
  final String module;
}

/// One saved view: a name and the list query parameters it puts back.
///
/// Private to whoever saved it. The API answers only the caller's own views,
/// and another person's id is a 404, so nothing here needs to check an owner.
class SavedView {
  const SavedView({
    required this.id,
    required this.name,
    required this.filters,
  });

  factory SavedView.fromJson(Map<String, dynamic> json) {
    final raw = json['filters'];
    final filters = <String, List<String>>{};
    if (raw is Map) {
      raw.forEach((key, value) {
        final values = value is List
            ? value.map((v) => '$v').toList(growable: false)
            : ['$value'];
        if (values.isNotEmpty) filters['$key'] = values;
      });
    }
    return SavedView(
      id: json['id']?.toString() ?? '',
      name: json['name']?.toString() ?? '',
      filters: filters,
    );
  }

  final String id;
  final String name;

  /// `{param: [value, ...]}`, the list's own query parameters.
  final Map<String, List<String>> filters;

  /// How many of this view's filter values a screen cannot apply, when it
  /// shows the parameters in [keys] and takes more than one value only for
  /// those in [multi]. Counted in values, not keys: three statuses on a
  /// screen that reads one is two it leaves out. The web counts the same way
  /// (`droppedValues` in `frontend/src/lib/v2/saved-views.js`).
  int dropped(Set<String> keys, Set<String> multi) {
    var count = 0;
    filters.forEach((key, values) {
      if (!keys.contains(key)) {
        count += values.length;
      } else if (!multi.contains(key) && values.length > 1) {
        count += values.length - 1;
      }
    });
    return count;
  }
}

/// A list's query, as `filterQuery` builds it, in the shape a saved view
/// stores: only the parameters in [keys], every value as text, blanks gone,
/// and one value for a parameter outside [multi].
Map<String, List<String>> savedViewFilters(
  Map<String, Object?> query,
  Set<String> keys, {
  Set<String> multi = const {},
}) {
  final out = <String, List<String>>{};
  query.forEach((key, value) {
    if (!keys.contains(key) || value == null) return;
    final values = (value is Iterable ? value : [value])
        .map((v) => '$v'.trim())
        .where((v) => v.isNotEmpty)
        .toList(growable: false);
    if (values.isEmpty) return;
    out[key] = multi.contains(key) ? values : values.sublist(0, 1);
  });
  return out;
}

class SavedViewsState {
  const SavedViewsState({this.views = const [], this.limit});

  final List<SavedView> views;

  /// How many views the API keeps per list, or null if it did not say.
  final int? limit;

  bool get full => limit != null && views.length >= limit!;
}

/// The caller's views for one list, and the three writes on them.
///
/// Each write answers `null` on success, or the sentence to show. The list is
/// read again after every write, so what is on screen is what was stored.
class SavedViewsNotifier extends AsyncNotifier<SavedViewsState> {
  SavedViewsNotifier(this.list);

  final SavedViewList list;
  final ApiService _api = ApiService();

  @override
  Future<SavedViewsState> build() => _fetch();

  Future<SavedViewsState> _fetch() async {
    final url = Uri.parse(
      ApiConfig.savedViews,
    ).replace(queryParameters: {'module': list.module}).toString();
    final response = await _api.get(url);
    if (!response.success || response.data == null) {
      throw Exception(response.message ?? 'Could not load saved views.');
    }
    final body = response.data!;
    final rows = body['saved_views'];
    return SavedViewsState(
      views: rows is List
          ? rows
                .whereType<Map<String, dynamic>>()
                .map(SavedView.fromJson)
                .toList(growable: false)
          : const [],
      limit: (body['limit'] as num?)?.toInt(),
    );
  }

  /// Read the list again. The rows already on screen stay until the answer
  /// arrives, rather than blanking the sheet between a write and its reload.
  Future<void> reload() async {
    state = await AsyncValue.guard(_fetch);
  }

  Future<String?> save(String name, Map<String, List<String>> filters) =>
      _write(
        _api.post(ApiConfig.savedViews, {
          'module': list.module,
          'name': name.trim(),
          'filters': filters,
        }),
        'Could not save the view.',
      );

  Future<String?> rename(String id, String name) => _write(
    _api.patch(ApiConfig.savedView(id), {'name': name.trim()}),
    'Could not rename the view.',
  );

  Future<String?> remove(String id) => _write(
    _api.delete(ApiConfig.savedView(id)),
    'Could not delete the view.',
  );

  Future<String?> _write(
    Future<ApiResponse<Map<String, dynamic>>> request,
    String fallback,
  ) async {
    final response = await request;
    if (!ref.mounted) return null;
    if (!response.success) {
      if (response.statusCode == 404) {
        await reload();
        return 'That view no longer exists.';
      }
      return firstSentence(response.data) ?? response.message ?? fallback;
    }
    await reload();
    return null;
  }
}

/// The first message in a DRF rejection. The saved-views API writes each as a
/// whole sentence, so the field name in front of it is left off, as on the web.
String? firstSentence(Map<String, dynamic>? body) {
  if (body == null || body.isEmpty) return null;
  final first = body.values.first;
  final text = first is List && first.isNotEmpty ? first.first : first;
  return text is String && text.trim().isNotEmpty ? text : null;
}

/// One per list while its sheet is open, so every opening reads fresh and no
/// view outlives a sign-out or an org switch.
final savedViewsProvider = AsyncNotifierProvider.autoDispose
    .family<SavedViewsNotifier, SavedViewsState, SavedViewList>(
      SavedViewsNotifier.new,
    );
