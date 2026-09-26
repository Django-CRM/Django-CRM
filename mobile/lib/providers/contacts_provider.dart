import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../config/api_config.dart';
import '../data/api_envelope.dart';
import '../data/models/contact.dart';
import '../services/api_service.dart';

/// Contacts, the module rather than the picker.
///
/// `/api/contacts/` does publish `results`, which is why the deal form's
/// contact picker has always worked while the account one beside it sat empty.
/// `contact_obj_list` carries the same rows under a second key, so both are
/// passed to `listFromEnvelope` with the real one first.
class ContactsListData {
  const ContactsListData({
    this.contacts = const [],
    this.totalCount = 0,
    this.hasMore = false,
    this.offset = 0,
  });

  final List<Contact> contacts;
  final int totalCount;
  final bool hasMore;
  final int offset;

  ContactsListData copyWith({
    List<Contact>? contacts,
    int? totalCount,
    bool? hasMore,
    int? offset,
  }) {
    return ContactsListData(
      contacts: contacts ?? this.contacts,
      totalCount: totalCount ?? this.totalCount,
      hasMore: hasMore ?? this.hasMore,
      offset: offset ?? this.offset,
    );
  }
}

const int _pageSize = 20;

class ContactsNotifier extends AsyncNotifier<ContactsListData> {
  final ApiService _api = ApiService();

  String _search = '';

  @override
  Future<ContactsListData> build() => _fetch(offset: 0);

  Future<void> refresh() async {
    state = const AsyncValue.loading();
    state = await AsyncValue.guard(() => _fetch(offset: 0));
  }

  Future<void> search(String term) async {
    _search = term.trim();
    await refresh();
  }

  /// A failed page keeps the rows already on screen and stops growing, rather
  /// than blanking a working list because page three timed out.
  Future<void> loadMore() async {
    final current = state.value;
    if (current == null || !current.hasMore || state.isLoading) return;
    try {
      final next = await _fetch(offset: current.offset + _pageSize);
      state = AsyncValue.data(
        next.copyWith(contacts: [...current.contacts, ...next.contacts]),
      );
    } catch (_) {
      state = AsyncValue.data(current.copyWith(hasMore: false));
    }
  }

  /// The list's filters as the API reads them, without paging. The list
  /// asks with these and so does its CSV export. Active and inactive contacts
  /// both, as the list shows them.
  Map<String, String> get filterQuery => {
    if (_search.isNotEmpty) 'search': _search,
  };

  Future<ContactsListData> _fetch({required int offset}) async {
    final params = <String, String>{
      'limit': '$_pageSize',
      'offset': '$offset',
      ...filterQuery,
    };

    final url = Uri.parse(
      ApiConfig.contacts,
    ).replace(queryParameters: params).toString();
    final response = await _api.get(url);
    if (!response.success || response.data == null) {
      throw Exception(response.message ?? 'Failed to load contacts');
    }

    final body = response.data!;
    final rows = listFromEnvelope(body, const ['results', 'contact_obj_list']);
    final total = body['contacts_count'] is int
        ? body['contacts_count'] as int
        : (body['count'] is int ? body['count'] as int : rows.length);

    return ContactsListData(
      contacts: rows.map(Contact.fromJson).toList(growable: false),
      totalCount: total,
      hasMore: offset + rows.length < total,
      offset: offset,
    );
  }

  Future<Contact?> getContact(String id) async {
    final response = await _api.get('${ApiConfig.contacts}$id/');
    if (!response.success || response.data == null) return null;
    final raw = response.data!['contact_obj'] ?? response.data!;
    if (raw is! Map<String, dynamic>) return null;
    return Contact.fromJson(raw);
  }

  Future<({String? id, String? error})> createContact(
    Map<String, dynamic> payload,
  ) async {
    final response = await _api.post(ApiConfig.contacts, payload);
    if (!response.success) {
      return (id: null, error: _message(response));
    }
    await refresh();
    return (id: response.data?['id']?.toString(), error: null);
  }

  /// PATCH, never PUT. `ContactDetailView.put` clears `teams`, `assigned_to`
  /// and `tags` whether or not the body mentions them, and the contact form
  /// sends none of the three, so every edit made from the phone used to strip
  /// them. PATCH touches a relation only when its key is present.
  Future<String?> updateContact(String id, Map<String, dynamic> payload) async {
    final response = await _api.patch('${ApiConfig.contacts}$id/', payload);
    if (!response.success) return _message(response);
    await refresh();
    return null;
  }

  Future<String?> deleteContact(String id) async {
    final response = await _api.delete('${ApiConfig.contacts}$id/');
    if (!response.success) return _message(response);
    await refresh();
    return null;
  }

  /// Surfaces the server's own validation text where there is one.
  ///
  /// "Contact already exists with this email" is the message that matters
  /// here: it is the one rejection a user can actually act on, and burying it
  /// under a generic failure would leave them retyping the same address.
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

final contactsProvider =
    AsyncNotifierProvider<ContactsNotifier, ContactsListData>(
      ContactsNotifier.new,
    );

final contactsListProvider = Provider<List<Contact>>((ref) {
  return ref.watch(contactsProvider).value?.contacts ?? const [];
});
