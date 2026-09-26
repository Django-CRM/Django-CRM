import 'package:flutter/material.dart';

import '../../core/theme/app_colors.dart';
import 'deal.dart' show Currency;

/// The eight values `invoices.models.INVOICE_STATUS` accepts.
///
/// Stored exactly as the server spells them, underscore and all, because the
/// `?status=` filter matches on the stored value. `Partially_Paid` is the one
/// that reads wrong to a human, so the label is separate from the value.
///
/// Carries its own colour, as `LeadStatus` and `TicketStatus` do, so the
/// existing `StatusBadge` can draw it without a second palette. Red is money
/// at risk, green is settled, grey is not yet in play.
enum InvoiceStatus {
  draft('Draft', 'Draft', AppColors.gray500),
  sent('Sent', 'Sent', AppColors.primary600),
  viewed('Viewed', 'Viewed', AppColors.primary600),
  paid('Paid', 'Paid', AppColors.success600),
  partiallyPaid('Partially_Paid', 'Partially paid', AppColors.warning600),
  overdue('Overdue', 'Overdue', AppColors.danger600),
  pending('Pending', 'Pending', AppColors.warning600),
  cancelled('Cancelled', 'Cancelled', AppColors.gray500);

  const InvoiceStatus(this.value, this.label, this.color);

  final String value;
  final String label;
  final Color color;

  /// An unknown status keeps its server spelling rather than collapsing to a
  /// default. A new status added to the backend should look unfamiliar here,
  /// not silently render as "Draft".
  static InvoiceStatus? fromString(String? raw) {
    if (raw == null) return null;
    for (final status in InvoiceStatus.values) {
      if (status.value == raw) return status;
    }
    return null;
  }
}

/// Money is settled on these, so the due date stops meaning anything.
const _settled = {InvoiceStatus.paid, InvoiceStatus.cancelled};

/// One invoice.
///
/// Built from two serializers that overlap but do not match. The list
/// (`InvoiceListSerializer`) sends `account` as a bare uuid with the name
/// beside it in `account_name`; the detail (`InvoiceSerializer`) nests the
/// whole account object and drops `account_name` entirely. `fromJson` reads
/// both, so a row and a detail produce the same shape and `accountName` is
/// never the literal text of a map.
class Invoice {
  const Invoice({
    required this.id,
    required this.invoiceNumber,
    this.title = '',
    this.status,
    this.rawStatus = '',
    this.accountId,
    this.accountName,
    this.contactName,
    this.clientName = '',
    this.clientEmail = '',
    this.issueDate,
    this.dueDate,
    this.totalAmount = 0,
    this.amountPaid = 0,
    this.amountDue = 0,
    this.currency,
    this.isOverdue = false,
    this.lineItemsCount = 0,
    this.lineItems = const [],
    this.payments = const [],
    this.notes,
    this.terms,
    this.poNumber,
  });

  final String id;
  final String invoiceNumber;
  final String title;

  /// Null when the server sent a status this build does not know.
  final InvoiceStatus? status;

  /// What the server actually said, kept so an unknown status can still be
  /// shown and still round-trips through the `?status=` filter.
  final String rawStatus;

  final String? accountId;
  final String? accountName;
  final String? contactName;
  final String clientName;
  final String clientEmail;
  final DateTime? issueDate;
  final DateTime? dueDate;

  /// Amounts arrive as decimal strings, so they are parsed rather than cast.
  final double totalAmount;
  final double amountPaid;
  final double amountDue;

  /// The invoice's own currency, not the org's. An org can bill in more than
  /// one, and the rate on the invoice is the one that was agreed.
  final String? currency;

  final bool isOverdue;

  /// The list sends a count; the detail sends the items themselves.
  final int lineItemsCount;
  final List<InvoiceLineItem> lineItems;
  final List<Payment> payments;

  final String? notes;
  final String? terms;
  final String? poNumber;

  String get statusLabel => status?.label ?? rawStatus;

  /// Grey for a status this build does not recognise: unfamiliar rather than
  /// wrong, since colouring an unknown status green would be a claim.
  Color get statusColor => status?.color ?? AppColors.gray500;

  Currency get currencyInfo => Currency.fromString(currency);

  bool get isSettled => status != null && _settled.contains(status);

  /// How many line items to claim. The detail carries the rows themselves and
  /// the list carries only a count, so neither field alone is right on both.
  int get itemCount => lineItems.isNotEmpty ? lineItems.length : lineItemsCount;

  /// Whole days past the due date, negative while there is still time.
  ///
  /// Null on a settled invoice and on one with no due date, because there is
  /// no honest answer in either case: "4 days late" against a paid invoice is
  /// simply wrong, and it was on screen in v1.
  int? get daysLate {
    final due = dueDate;
    if (due == null || isSettled) return null;
    final today = DateTime.now();
    return DateTime(
      today.year,
      today.month,
      today.day,
    ).difference(DateTime(due.year, due.month, due.day)).inDays;
  }

  bool get isLate => (daysLate ?? -1) > 0;

  /// Whether the send button should be offered.
  ///
  /// Deliberately narrower than the API, which refuses only Paid and
  /// Cancelled and would therefore allow a resend of anything else. The web
  /// list offers Send on a draft and Send reminder on an overdue invoice and
  /// nothing else, and this mirrors that rather than inventing a third rule.
  /// The server is still the check that counts.
  bool get canSend =>
      status == InvoiceStatus.draft || status == InvoiceStatus.overdue;

  /// Whether to offer recording a payment.
  ///
  /// There has to be a balance to settle, and a cancelled invoice is not owed.
  /// Both halves mirror a refusal the API enforces: `PaymentCreateSerializer`
  /// rejects a payment against a cancelled invoice outright, and rejects any
  /// amount above `amount_due`, which on a paid invoice is every amount.
  bool get canRecordPayment =>
      amountDue > 0 &&
      status != InvoiceStatus.cancelled &&
      status != InvoiceStatus.paid;

  /// Cancel is refused on an already-cancelled invoice and on a paid one.
  bool get canCancel =>
      status != null &&
      status != InvoiceStatus.cancelled &&
      status != InvoiceStatus.paid;

  factory Invoice.fromJson(Map<String, dynamic> json) {
    final account = json['account'];
    final contact = json['contact'];
    return Invoice(
      id: json['id']?.toString() ?? '',
      invoiceNumber: json['invoice_number']?.toString() ?? '',
      title: json['invoice_title'] as String? ?? '',
      status: InvoiceStatus.fromString(json['status'] as String?),
      rawStatus: json['status']?.toString() ?? '',
      accountId: account is Map<String, dynamic>
          ? account['id']?.toString()
          : account?.toString(),
      accountName: account is Map<String, dynamic>
          ? account['name'] as String?
          : json['account_name'] as String?,
      contactName: contact is Map<String, dynamic>
          ? _personName(contact)
          : json['contact_name'] as String?,
      clientName: json['client_name'] as String? ?? '',
      clientEmail: json['client_email'] as String? ?? '',
      issueDate: _date(json['issue_date']),
      dueDate: _date(json['due_date']),
      totalAmount: _amount(json['total_amount']),
      amountPaid: _amount(json['amount_paid']),
      amountDue: _amount(json['amount_due']),
      currency: json['currency'] as String?,
      isOverdue: json['is_overdue'] as bool? ?? false,
      lineItemsCount: json['line_items_count'] as int? ?? 0,
      lineItems: _rows(
        json['line_items'],
      ).map(InvoiceLineItem.fromJson).toList(),
      payments: _rows(json['payments']).map(Payment.fromJson).toList(),
      notes: json['notes'] as String?,
      terms: json['terms'] as String?,
      poNumber: json['po_number'] as String?,
    );
  }
}

/// One billed line.
class InvoiceLineItem {
  const InvoiceLineItem({
    required this.id,
    this.name = '',
    this.description,
    this.quantity = 0,
    this.unitPrice = 0,
    this.discountAmount = 0,
    this.amount = 0,
  });

  final String id;
  final String name;
  final String? description;
  final double quantity;
  final double unitPrice;

  /// What the line's own discount took off quantity x unit price.
  final double discountAmount;

  /// The server's `net_amount`: quantity x unit price less the line's own
  /// discount, which is what the lines add up to in the subtotal.
  final double amount;

  /// Trailing zeroes dropped, so 2 units reads "2" and 1.5 hours reads "1.5".
  String get quantityLabel {
    final whole = quantity.truncateToDouble() == quantity;
    return whole ? quantity.toStringAsFixed(0) : quantity.toString();
  }

  factory InvoiceLineItem.fromJson(Map<String, dynamic> json) {
    return InvoiceLineItem(
      id: json['id']?.toString() ?? '',
      // `name` is the line's own text and `product_name` is the catalogue
      // entry behind it. A free-text line has no product, so name comes first.
      name: (json['name'] as String?)?.trim().isNotEmpty == true
          ? json['name'] as String
          : json['product_name'] as String? ?? '',
      description: json['description'] as String?,
      quantity: _amount(json['quantity']),
      unitPrice: _amount(json['unit_price']),
      discountAmount: _amount(json['discount_amount']),
      // A server before 1.10.0 sends no `net_amount`; its `total` was the
      // figure shown then.
      amount: _amount(json['net_amount'] ?? json['total']),
    );
  }
}

/// A payment already recorded against an invoice.
class Payment {
  const Payment({
    required this.id,
    this.amount = 0,
    this.paymentDate,
    this.method = '',
    this.referenceNumber,
    this.notes,
  });

  final String id;
  final double amount;
  final DateTime? paymentDate;
  final String method;
  final String? referenceNumber;
  final String? notes;

  String get methodLabel => paymentMethods[method] ?? method;

  factory Payment.fromJson(Map<String, dynamic> json) {
    return Payment(
      id: json['id']?.toString() ?? '',
      amount: _amount(json['amount']),
      paymentDate: _date(json['payment_date']),
      method: json['payment_method']?.toString() ?? '',
      referenceNumber: json['reference_number'] as String?,
      notes: json['notes'] as String?,
    );
  }
}

/// `invoices.models.PAYMENT_METHODS`, value to label.
const Map<String, String> paymentMethods = {
  'CASH': 'Cash',
  'CHECK': 'Check',
  'CREDIT_CARD': 'Credit card',
  'BANK_TRANSFER': 'Bank transfer',
  'PAYPAL': 'PayPal',
  'STRIPE': 'Stripe',
  'OTHER': 'Other',
};

/// The list header figures.
///
/// The API computes these over everything the caller can see, NOT over the
/// active filter and not over the loaded page. Labelling them as describing
/// the filtered list would be false, which is why the screen does not.
///
/// Every invoice carries its own currency and there are no exchange rates, so
/// the server never adds two currencies together: the amounts arrive as a
/// `by_currency` list and are kept here as one [InvoiceMoneyTotals] per
/// currency code. The counts have no currency and stay single figures.
class InvoiceTotals {
  const InvoiceTotals({
    this.count = 0,
    this.money = const {},
    this.actionNeeded = 0,
  });

  final int count;

  /// The amounts, keyed by currency code. Empty when nothing is visible. A
  /// server older than the per-currency totals sends only the plain figures,
  /// which are kept under an empty code.
  final Map<String, InvoiceMoneyTotals> money;

  /// A count, not an amount: drafts to send plus anything overdue to chase.
  final int actionNeeded;

  factory InvoiceTotals.fromJson(Map<String, dynamic> json) {
    final rows = json['by_currency'];
    return InvoiceTotals(
      count: json['count'] as int? ?? 0,
      money: rows is List
          ? {
              for (final row in rows.whereType<Map<String, dynamic>>())
                if (row['currency'] is String)
                  row['currency'] as String: InvoiceMoneyTotals.fromJson(row),
            }
          : {'': InvoiceMoneyTotals.fromJson(json)},
      actionNeeded: json['action_needed'] as int? ?? 0,
    );
  }
}

/// The list header's amounts in one currency.
class InvoiceMoneyTotals {
  const InvoiceMoneyTotals({
    this.outstanding = 0,
    this.overdue = 0,
    this.dueThisMonth = 0,
    this.paidThisQuarter = 0,
    this.draft = 0,
  });

  final double outstanding;
  final double overdue;
  final double dueThisMonth;
  final double paidThisQuarter;
  final double draft;

  factory InvoiceMoneyTotals.fromJson(Map<String, dynamic> json) {
    return InvoiceMoneyTotals(
      outstanding: _amount(json['outstanding']),
      overdue: _amount(json['overdue']),
      dueThisMonth: _amount(json['due_this_month']),
      paidThisQuarter: _amount(json['paid_this_quarter']),
      draft: _amount(json['draft']),
    );
  }
}

String? _personName(Map<String, dynamic> contact) {
  final parts = [
    contact['first_name'],
    contact['last_name'],
  ].map((p) => p?.toString().trim() ?? '').where((p) => p.isNotEmpty);
  final name = parts.join(' ');
  return name.isEmpty ? null : name;
}

List<Map<String, dynamic>> _rows(dynamic value) {
  if (value is! List) return const [];
  return value.whereType<Map<String, dynamic>>().toList();
}

/// Amounts cross the wire as decimal strings ("1250.00"), so `as double` would
/// throw on every one of them.
double _amount(dynamic value) {
  if (value is num) return value.toDouble();
  return double.tryParse(value?.toString() ?? '') ?? 0;
}

/// A YYYY-MM-DD date as a local calendar date.
///
/// Parsed with an explicit midnight so the value is local rather than UTC.
/// Reading a UTC midnight anywhere behind Greenwich lands on the day before,
/// which would report an invoice due today as a day overdue.
DateTime? _date(dynamic value) {
  final text = value?.toString().trim() ?? '';
  if (text.isEmpty || text == 'null') return null;
  if (RegExp(r'^\d{4}-\d{2}-\d{2}$').hasMatch(text)) {
    return DateTime.tryParse('${text}T00:00:00');
  }
  return DateTime.tryParse(text)?.toLocal();
}
