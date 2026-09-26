import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../config/api_config.dart';
import '../data/models/invoice.dart';
import '../services/api_service.dart';

/// Invoices, the module.
///
/// `ApiConfig.invoices` has been declared since before there was a screen and
/// was called by nothing, which is why the parity tracker counted invoices as
/// wholly absent. This is the half that was missing.
///
/// The list envelope is the ordinary `{count, next, previous, results}` plus a
/// `totals` block, so no `listFromEnvelope` path guessing is needed here.
class InvoicesListData {
  const InvoicesListData({
    this.invoices = const [],
    this.totals = const InvoiceTotals(),
    this.hasMore = false,
    this.offset = 0,
    this.status,
  });

  final List<Invoice> invoices;
  final InvoiceTotals totals;
  final bool hasMore;
  final int offset;

  /// The active status filter, or null for all. Held here rather than only in
  /// the screen so a rebuilt widget shows the filter that is actually applied.
  final InvoiceStatus? status;

  int get totalCount => totals.count;

  InvoicesListData copyWith({
    List<Invoice>? invoices,
    InvoiceTotals? totals,
    bool? hasMore,
    int? offset,
    InvoiceStatus? status,
    bool clearStatus = false,
  }) {
    return InvoicesListData(
      invoices: invoices ?? this.invoices,
      totals: totals ?? this.totals,
      hasMore: hasMore ?? this.hasMore,
      offset: offset ?? this.offset,
      status: clearStatus ? null : status ?? this.status,
    );
  }
}

const int _pageSize = 20;

class InvoicesNotifier extends AsyncNotifier<InvoicesListData> {
  final ApiService _api = ApiService();

  String _search = '';
  InvoiceStatus? _status;

  @override
  Future<InvoicesListData> build() => _fetch(offset: 0);

  Future<void> refresh() async {
    state = const AsyncValue.loading();
    state = await AsyncValue.guard(() => _fetch(offset: 0));
  }

  Future<void> search(String term) async {
    _search = term.trim();
    await refresh();
  }

  Future<void> filterByStatus(InvoiceStatus? status) async {
    _status = status;
    await refresh();
  }

  /// Appends the next page. A failure here leaves the loaded rows alone and
  /// stops paging, rather than replacing a good list with an error screen
  /// because page three timed out.
  Future<void> loadMore() async {
    final current = state.value;
    if (current == null || !current.hasMore || state.isLoading) return;
    try {
      final next = await _fetch(offset: current.offset + _pageSize);
      state = AsyncValue.data(
        next.copyWith(invoices: [...current.invoices, ...next.invoices]),
      );
    } catch (_) {
      state = AsyncValue.data(current.copyWith(hasMore: false));
    }
  }

  /// The list's filters and order as the API reads them, without paging.
  /// The list asks with these and so does its CSV export.
  Map<String, String> get filterQuery => {
    // Oldest due first, matching the web list. The unpaid invoice that has
    // been owed longest is the one worth a phone call.
    'sort': 'due_date',
    if (_search.isNotEmpty) 'search': _search,
    if (_status != null) 'status': _status!.value,
  };

  Future<InvoicesListData> _fetch({required int offset}) async {
    final params = <String, String>{
      'limit': '$_pageSize',
      'offset': '$offset',
      ...filterQuery,
    };

    final url = Uri.parse(
      ApiConfig.invoices,
    ).replace(queryParameters: params).toString();
    final response = await _api.get(url);
    if (!response.success || response.data == null) {
      throw Exception(response.message ?? 'Failed to load invoices');
    }

    final body = response.data!;
    final rows = body['results'];
    final invoices = rows is List
        ? rows
              .whereType<Map<String, dynamic>>()
              .map(Invoice.fromJson)
              .toList(growable: false)
        : const <Invoice>[];
    final totals = body['totals'] is Map<String, dynamic>
        ? InvoiceTotals.fromJson(body['totals'] as Map<String, dynamic>)
        : const InvoiceTotals();

    return InvoicesListData(
      invoices: invoices,
      totals: totals,
      // `next` is the server's own answer about whether more exist. Comparing
      // offset against `totals.count` would be wrong here, because that count
      // ignores the active status filter by design.
      hasMore: body['next'] != null,
      offset: offset,
      status: _status,
    );
  }

  /// One invoice, with the line items, payments and terms the rows omit.
  Future<Invoice?> getInvoice(String id) async {
    if (id.isEmpty) return null;
    final response = await _api.get(ApiConfig.invoice(id));
    if (!response.success || response.data == null) return null;
    final body = response.data!;
    // The detail view wraps the record under `invoice`; a bare body is
    // accepted too so a serializer change degrades to a miss, not a crash.
    final raw = body['invoice'] ?? body;
    if (raw is! Map<String, dynamic>) return null;
    return Invoice.fromJson(raw);
  }

  /// Emails the invoice to its client. Returns null on success.
  Future<String?> send(String id) => _act(ApiConfig.invoiceSend(id));

  /// Records a payment against the invoice.
  ///
  /// [amount] null settles the whole outstanding balance, which is what the
  /// server does with an absent amount. A caller-supplied amount is passed
  /// through so the server can bound it: it rejects zero, negative, and
  /// anything above `amount_due`, and this client does not second-guess that
  /// arithmetic.
  Future<String?> recordPayment(
    String id, {
    double? amount,
    String method = 'OTHER',
    String reference = '',
  }) {
    return _act(
      ApiConfig.invoiceMarkPaid(id),
      body: {
        if (amount != null) 'amount': amount.toStringAsFixed(2),
        'payment_method': method,
        if (reference.trim().isNotEmpty) 'reference_number': reference.trim(),
      },
    );
  }

  Future<String?> cancel(String id) => _act(ApiConfig.invoiceCancel(id));

  /// Raises a draft invoice. Returns the new invoice's id, or the server's
  /// message.
  ///
  /// [payload] is passed through as the caller built it rather than being
  /// reshaped here, because `InvoiceCreateSerializer` is the only definition
  /// of what it accepts and a second copy of that shape would drift. Notably
  /// `status` is read-only server-side, so a draft is the only thing this can
  /// create even if a caller tried to set one.
  Future<({String? invoiceId, String? error})> createInvoice(
    Map<String, dynamic> payload,
  ) async {
    final response = await _api.post(ApiConfig.invoices, payload);
    if (!response.success) {
      return (invoiceId: null, error: _message(response));
    }
    await refresh();
    final invoice = response.data?['invoice'];
    final invoiceId = invoice is Map<String, dynamic>
        ? invoice['id']?.toString()
        : null;
    return (invoiceId: invoiceId, error: null);
  }

  /// Raises a Draft invoice from a won deal. The server decides whether the
  /// deal may be invoiced and says why not ("Opportunity has no products/line
  /// items to invoice"), so its message is returned as written.
  Future<({String? invoiceId, String? error})> createFromDeal(
    String dealId,
  ) async {
    final response = await _api.post(
      ApiConfig.invoiceFromDeal(dealId),
      const {},
    );
    if (!response.success) {
      return (invoiceId: null, error: _message(response));
    }
    await refresh();
    final invoice = response.data?['invoice'];
    final invoiceId = invoice is Map<String, dynamic>
        ? invoice['id']?.toString()
        : null;
    return (invoiceId: invoiceId, error: null);
  }

  /// POSTs an action and refreshes the list. Returns null on success, or the
  /// server's own message.
  ///
  /// Every one of these endpoints can legitimately refuse (a paid invoice
  /// cannot be sent, a cancelled one cannot be cancelled again), so the
  /// message matters more than the status code: "Cannot send a paid invoice"
  /// is the thing the user needs to read.
  Future<String?> _act(String url, {Map<String, dynamic>? body}) async {
    final response = await _api.post(url, body ?? const {});
    if (!response.success) return _message(response);
    await refresh();
    return null;
  }

  /// Surfaces the server's validation text where there is one.
  ///
  /// These views answer 400 two different ways: `{"error": true, "message":
  /// "..."}` for a refused transition and `{"error": true, "errors": {...}}`
  /// for a bad payment amount. Reading only one of them would drop half the
  /// useful messages.
  String _message(dynamic response) {
    final errors = response.data?['errors'];
    if (errors is Map) {
      for (final value in errors.values) {
        if (value is List && value.isNotEmpty) return value.first.toString();
        if (value is String && value.trim().isNotEmpty) return value;
      }
    }
    final detail = response.data?['message'];
    if (detail is String && detail.trim().isNotEmpty) return detail;
    final raw = response.message as String?;
    return (raw == null || raw.trim().isEmpty) ? 'Something went wrong' : raw;
  }
}

final invoicesProvider =
    AsyncNotifierProvider<InvoicesNotifier, InvoicesListData>(
      InvoicesNotifier.new,
    );

/// Invoices waiting on this person: drafts to send, plus anything overdue.
/// A count rather than an amount, for a badge.
final invoiceActionCountProvider = Provider<int>((ref) {
  return ref.watch(invoicesProvider).value?.totals.actionNeeded ?? 0;
});
