import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../config/api_config.dart';
import '../data/api_envelope.dart';
import '../data/models/account.dart';
import '../services/api_service.dart';

/// Accounts, the module rather than the picker.
///
/// `lookup_provider.dart` has fetched accounts for the deal form's picker for
/// a long time, which is why the endpoint and a four-field model already
/// existed. Nothing surfaced them as a screen. This is that half.
///
/// THE ENVELOPE IS THE ONLY SUBTLE PART. `/api/accounts/` publishes neither
/// `results` nor `accounts`; its rows sit two deep at
/// `active_accounts.open_accounts`, with the closed ones in a parallel
/// `closed_accounts.close_accounts`. Guessing wrong here does not raise, it
/// yields an empty list and a screen that says "No accounts found" in an org
/// with hundreds. That is exactly how the deal form's picker sat broken. The
/// paths are passed explicitly to `listFromEnvelope` for that reason.
class AccountsListData {
  const AccountsListData({
    this.accounts = const [],
    this.totalCount = 0,
    this.hasMore = false,
    this.offset = 0,
    this.showingClosed = false,
  });

  final List<Account> accounts;
  final int totalCount;
  final bool hasMore;
  final int offset;

  /// Which side of the list is on screen. The endpoint returns both in one
  /// response, so this is a display choice rather than a second request.
  final bool showingClosed;

  AccountsListData copyWith({
    List<Account>? accounts,
    int? totalCount,
    bool? hasMore,
    int? offset,
    bool? showingClosed,
  }) {
    return AccountsListData(
      accounts: accounts ?? this.accounts,
      totalCount: totalCount ?? this.totalCount,
      hasMore: hasMore ?? this.hasMore,
      offset: offset ?? this.offset,
      showingClosed: showingClosed ?? this.showingClosed,
    );
  }
}

const int _pageSize = 20;

class AccountsNotifier extends AsyncNotifier<AccountsListData> {
  final ApiService _api = ApiService();

  String _search = '';
  bool _showClosed = false;

  @override
  Future<AccountsListData> build() => _fetch(offset: 0);

  Future<void> refresh() async {
    state = const AsyncValue.loading();
    state = await AsyncValue.guard(() => _fetch(offset: 0));
  }

  Future<void> search(String term) async {
    _search = term.trim();
    await refresh();
  }

  Future<void> showClosed(bool value) async {
    _showClosed = value;
    await refresh();
  }

  /// Appends the next page. Errors here are swallowed into "no more pages"
  /// rather than replacing the list with an error state: the rows already on
  /// screen are still good, and blanking them because page three failed is a
  /// worse answer than a list that stops growing.
  Future<void> loadMore() async {
    final current = state.value;
    if (current == null || !current.hasMore || state.isLoading) return;
    try {
      final next = await _fetch(offset: current.offset + _pageSize);
      state = AsyncValue.data(
        next.copyWith(accounts: [...current.accounts, ...next.accounts]),
      );
    } catch (_) {
      state = AsyncValue.data(current.copyWith(hasMore: false));
    }
  }

  /// The list's filters as the API reads them, without paging. The list
  /// asks with these and so does its CSV export. The API answers in active
  /// and closed halves and this screen shows one; `is_active` names it, so
  /// the export, which has no halves, holds the same rows.
  Map<String, String> get filterQuery => {
    if (_search.isNotEmpty) 'search': _search,
    'is_active': _showClosed ? 'false' : 'true',
  };

  Future<AccountsListData> _fetch({required int offset}) async {
    final params = <String, String>{
      'limit': '$_pageSize',
      'offset': '$offset',
      ...filterQuery,
    };

    final url = Uri.parse(
      ApiConfig.accounts,
    ).replace(queryParameters: params).toString();
    final response = await _api.get(url);
    if (!response.success || response.data == null) {
      throw Exception(response.message ?? 'Failed to load accounts');
    }

    final body = response.data!;
    final rows = listFromEnvelope(body, [
      _showClosed
          ? 'closed_accounts.close_accounts'
          : 'active_accounts.open_accounts',
    ]);
    final total = _count(
      body,
      _showClosed ? 'closed_accounts' : 'active_accounts',
      _showClosed ? 'close_accounts_count' : 'open_accounts_count',
    );

    return AccountsListData(
      accounts: rows.map(Account.fromJson).toList(growable: false),
      totalCount: total,
      hasMore: offset + rows.length < total,
      offset: offset,
      showingClosed: _showClosed,
    );
  }

  int _count(Map<String, dynamic> body, String group, String key) {
    final node = body[group];
    if (node is Map && node[key] is int) return node[key] as int;
    return 0;
  }

  /// One account, with the relations and rollups the list rows do not carry.
  Future<Account?> getAccount(String id) async {
    final response = await _api.get('${ApiConfig.accounts}$id/');
    if (!response.success || response.data == null) return null;
    final body = response.data!;
    // The detail view wraps the record; the key has been stable but a bare
    // body is accepted too so a serializer change degrades to a miss rather
    // than a crash.
    final raw = body['account_obj'] ?? body['account'] ?? body;
    if (raw is! Map<String, dynamic>) return null;
    return Account.fromJson(raw);
  }

  /// Returns the new account's id, or an error message.
  Future<({String? id, String? error})> createAccount(
    Map<String, dynamic> payload,
  ) async {
    final response = await _api.post(ApiConfig.accounts, payload);
    if (!response.success) {
      return (id: null, error: _message(response));
    }
    await refresh();
    final id = response.data?['id']?.toString();
    return (id: id, error: null);
  }

  /// Returns null on success, a message otherwise.
  ///
  /// PATCH, never PUT. `AccountDetailView.put` is a full replace that clears
  /// `teams` whether or not the body mentions them, and this app has no teams
  /// control, so every edit made from the phone used to unlink every team on
  /// the account. PATCH touches a relation only when its key is present, which
  /// is what the web has always used.
  Future<String?> updateAccount(String id, Map<String, dynamic> payload) async {
    final response = await _api.patch('${ApiConfig.accounts}$id/', payload);
    if (!response.success) return _message(response);
    await refresh();
    return null;
  }

  Future<String?> deleteAccount(String id) async {
    final response = await _api.delete('${ApiConfig.accounts}$id/');
    if (!response.success) return _message(response);
    await refresh();
    return null;
  }

  /// Surfaces the server's own validation text where there is one.
  ///
  /// The create and update views answer 400 with `{"error": true, "errors":
  /// {...}}`. Falling back to the generic message would tell a user their save
  /// failed without telling them the name is too long, which is the one thing
  /// they can act on.
  String _message(dynamic response) {
    final errors = response.data?['errors'];
    if (errors is Map) {
      for (final value in errors.values) {
        if (value is List && value.isNotEmpty) return value.first.toString();
        if (value is String && value.trim().isNotEmpty) return value;
      }
    }
    final raw = response.message as String?;
    return (raw == null || raw.trim().isEmpty) ? 'Something went wrong' : raw;
  }
}

final accountsProvider =
    AsyncNotifierProvider<AccountsNotifier, AccountsListData>(
      AccountsNotifier.new,
    );

/// The rows alone, for widgets that do not care about paging state.
final accountsListProvider = Provider<List<Account>>((ref) {
  return ref.watch(accountsProvider).value?.accounts ?? const [];
});
