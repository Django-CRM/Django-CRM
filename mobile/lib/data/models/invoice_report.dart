/// The invoice reports, from `/invoices/reports/dashboard/` and
/// `/invoices/reports/aging/`.
///
/// **Both endpoints are admin-only.** `_forbid_non_admin_reports` answers 403
/// to everyone else, so a non-admin opening this screen gets a refusal from
/// the server rather than an empty report, and the screen says so.
///
/// Every invoice carries its own currency and there are no exchange rates, so
/// the server never adds two currencies together: each money figure arrives as
/// a `by_currency` list, and it is kept here as one entry per currency code.
class InvoiceDashboard {
  const InvoiceDashboard({
    this.money = const {},
    this.invoiceCount = 0,
    this.averageDaysToPay = 0,
    this.statusCounts = const {},
    this.estimatesPending = 0,
    this.estimatesAccepted = 0,
    this.estimatesDeclined = 0,
  });

  /// The money figures, keyed by currency code.
  final Map<String, DashboardMoney> money;
  final int invoiceCount;

  /// Issue date to payment date, in whole days, over paid invoices carrying
  /// both dates. Zero when nothing has been paid yet, which is why the screen
  /// hides it rather than printing "0 days to pay" for a new org.
  final int averageDaysToPay;

  final Map<String, int> statusCounts;
  final int estimatesPending;
  final int estimatesAccepted;
  final int estimatesDeclined;

  factory InvoiceDashboard.fromJson(Map<String, dynamic> json) {
    final estimates = _map(json['estimates']);
    final counts = _map(json['status_counts']);
    final summary = _byCurrency(json['summary']);
    final overdue = _byCurrency(json['overdue']);
    final recent = _byCurrency(json['recent_activity']);
    return InvoiceDashboard(
      money: {
        for (final code in {...summary.keys, ...overdue.keys, ...recent.keys})
          code: DashboardMoney(
            totalInvoiced: _num(summary[code]?['total_invoiced']),
            totalPaid: _num(summary[code]?['total_paid']),
            totalDue: _num(summary[code]?['total_due']),
            overdueCount: overdue[code]?['count'] as int? ?? 0,
            overdueAmount: _num(overdue[code]?['amount']),
            revenue30d: _num(recent[code]?['revenue_30d']),
            invoiced30d: _num(recent[code]?['invoiced_30d']),
          ),
      },
      invoiceCount: json['invoice_count'] as int? ?? 0,
      averageDaysToPay: json['average_days_to_pay'] as int? ?? 0,
      statusCounts: {
        for (final entry in counts.entries)
          if (entry.value is int) entry.key: entry.value as int,
      },
      estimatesPending: estimates['pending'] as int? ?? 0,
      estimatesAccepted: estimates['accepted'] as int? ?? 0,
      estimatesDeclined: estimates['declined'] as int? ?? 0,
    );
  }
}

/// The dashboard's money in one currency.
class DashboardMoney {
  const DashboardMoney({
    this.totalInvoiced = 0,
    this.totalPaid = 0,
    this.totalDue = 0,
    this.overdueCount = 0,
    this.overdueAmount = 0,
    this.revenue30d = 0,
    this.invoiced30d = 0,
  });

  final double totalInvoiced;
  final double totalPaid;
  final double totalDue;
  final int overdueCount;
  final double overdueAmount;
  final double revenue30d;
  final double invoiced30d;
}

/// One ageing bucket. The server caps `invoices` at ten per bucket while
/// `count` and `amount` cover the whole bucket, so the two disagree on purpose
/// and the screen must not present the listed rows as the total.
class AgingBucket {
  const AgingBucket({required this.label, this.count = 0, this.amount = 0});

  final String label;
  final int count;
  final double amount;

  factory AgingBucket.fromJson(String label, dynamic node) {
    final map = _map(node);
    return AgingBucket(
      label: label,
      count: map['count'] as int? ?? 0,
      amount: _num(map['amount']),
    );
  }
}

/// Accounts receivable ageing in one currency, oldest money first.
class AgingReport {
  const AgingReport({
    this.buckets = const [],
    this.total = 0,
    this.overdue = 0,
  });

  final List<AgingBucket> buckets;

  /// The server's own figure over every unpaid invoice, not a sum of the
  /// buckets above. They should agree, and when they do not the server is
  /// right: it counted the whole queryset while a bucket list can be capped.
  final double total;

  /// Past the due date only, so `total` minus what is merely not yet due.
  final double overdue;

  static const _buckets = {
    'current': 'Not yet due',
    '1_30_days': '1 to 30 days',
    '31_60_days': '31 to 60 days',
    '61_90_days': '61 to 90 days',
    'over_90_days': 'Over 90 days',
  };

  /// One report per currency code the response has unpaid money in.
  static Map<String, AgingReport> byCurrencyFromJson(
    Map<String, dynamic> json,
  ) {
    final total = _byCurrency(json['total']);
    final overdue = _byCurrency(json['overdue']);
    final buckets = {
      for (final key in _buckets.keys) key: _byCurrency(json[key]),
    };
    return {
      for (final code in total.keys)
        code: AgingReport(
          buckets: [
            for (final MapEntry(:key, :value) in _buckets.entries)
              AgingBucket.fromJson(value, buckets[key]![code]),
          ],
          total: _num(total[code]?['amount']),
          overdue: _num(overdue[code]?['amount']),
        ),
    };
  }

  /// Every bucket at zero, for a currency with nothing unpaid.
  static final empty = AgingReport(
    buckets: [for (final label in _buckets.values) AgingBucket(label: label)],
  );
}

Map<String, dynamic> _map(dynamic value) =>
    value is Map<String, dynamic> ? value : const {};

double _num(dynamic value) {
  if (value is num) return value.toDouble();
  return double.tryParse(value?.toString() ?? '') ?? 0;
}

/// A report block's `by_currency` rows, keyed by currency code.
///
/// A server older than the per-currency reports sends only the plain figures;
/// those are kept under an empty code, which the screen labels with the org's
/// own currency, as it always did.
Map<String, Map<String, dynamic>> _byCurrency(dynamic block) {
  final map = _map(block);
  final rows = map['by_currency'];
  if (rows is! List) return map.isEmpty ? const {} : {'': map};
  return {
    for (final row in rows.whereType<Map<String, dynamic>>())
      if (row['currency'] is String) row['currency'] as String: row,
  };
}
