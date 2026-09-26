import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:intl/intl.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

import '../../core/theme/theme.dart';
import '../../data/models/invoice.dart';
import '../../providers/invoices_provider.dart';
import '../../widgets/common/badge.dart';
import 'invoice_format.dart';
import 'record_payment_sheet.dart';

/// One invoice: what is owed, what it is for, and what has been paid.
///
/// The list rows carry a count of line items and no payments at all, so this
/// always refetches rather than reading the row it was opened from.
class InvoiceDetailScreen extends ConsumerStatefulWidget {
  const InvoiceDetailScreen({super.key, required this.invoiceId});

  final String invoiceId;

  @override
  ConsumerState<InvoiceDetailScreen> createState() =>
      _InvoiceDetailScreenState();
}

class _InvoiceDetailScreenState extends ConsumerState<InvoiceDetailScreen> {
  Invoice? _invoice;
  bool _loading = true;
  bool _failed = false;

  /// True while a send, payment or cancel is in flight, so the buttons can be
  /// disabled. Without this a double tap sends the client two emails.
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _failed = false;
    });
    final invoice = await ref
        .read(invoicesProvider.notifier)
        .getInvoice(widget.invoiceId);
    if (!mounted) return;
    setState(() {
      _invoice = invoice;
      _loading = false;
      _failed = invoice == null;
    });
  }

  @override
  Widget build(BuildContext context) {
    final invoice = _invoice;
    return Scaffold(
      backgroundColor: AppColors.surfaceDim,
      appBar: AppBar(
        title: Text(invoice?.invoiceNumber ?? 'Invoice'),
        backgroundColor: AppColors.surface,
        elevation: 0,
        scrolledUnderElevation: 1,
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _failed || invoice == null
          ? _errorState()
          : RefreshIndicator(
              onRefresh: _load,
              child: ListView(
                padding: const EdgeInsets.only(bottom: 32),
                children: [
                  _header(invoice),
                  _amounts(invoice),
                  if (invoice.lineItems.isNotEmpty) _lineItems(invoice),
                  if (invoice.payments.isNotEmpty) _payments(invoice),
                  _actions(invoice),
                ],
              ),
            ),
    );
  }

  Widget _errorState() {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(LucideIcons.alertCircle, size: 40, color: AppColors.danger500),
            const SizedBox(height: 12),
            Text(
              'Could not load this invoice',
              textAlign: TextAlign.center,
              style: AppTypography.body.copyWith(color: AppColors.textPrimary),
            ),
            const SizedBox(height: 6),
            Text(
              'It may have been deleted, or it may belong to someone else.',
              textAlign: TextAlign.center,
              style: AppTypography.caption.copyWith(
                color: AppColors.textSecondary,
              ),
            ),
            const SizedBox(height: 16),
            FilledButton(onPressed: _load, child: const Text('Retry')),
          ],
        ),
      ),
    );
  }

  Widget _header(Invoice invoice) {
    final age = ageLabel(invoice);
    return Container(
      color: AppColors.surface,
      padding: const EdgeInsets.fromLTRB(16, 16, 16, 16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (invoice.title.isNotEmpty)
            Text(
              invoice.title,
              style: AppTypography.h3.copyWith(fontWeight: FontWeight.w600),
            ),
          const SizedBox(height: 8),
          Wrap(
            spacing: 8,
            runSpacing: 4,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              StatusBadge(
                label: invoice.statusLabel,
                color: invoice.statusColor,
              ),
              if (invoice.dueDate != null)
                Text(
                  'due ${DateFormat('d MMM yyyy').format(invoice.dueDate!)}',
                  style: AppTypography.caption.copyWith(
                    color: AppColors.textSecondary,
                  ),
                ),
              if (age != null)
                Text(
                  age,
                  style: AppTypography.caption.copyWith(
                    color: invoice.isLate
                        ? AppColors.danger600
                        : AppColors.textSecondary,
                    fontWeight: invoice.isLate ? FontWeight.w600 : null,
                  ),
                ),
            ],
          ),
          const SizedBox(height: 12),
          _field('Account', invoice.accountName),
          _field(
            'Bill to',
            invoice.contactName ??
                (invoice.clientName.isEmpty ? null : invoice.clientName),
          ),
          _field(
            'Email',
            invoice.clientEmail.isEmpty ? null : invoice.clientEmail,
          ),
          _field(
            'Issued',
            invoice.issueDate == null
                ? null
                : DateFormat('d MMM yyyy').format(invoice.issueDate!),
          ),
          _field('PO number', invoice.poNumber),
        ],
      ),
    );
  }

  Widget _field(String label, String? value) {
    if (value == null || value.trim().isEmpty) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.only(bottom: 6),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 92,
            child: Text(
              label,
              style: AppTypography.caption.copyWith(
                color: AppColors.textSecondary,
              ),
            ),
          ),
          Expanded(
            child: Text(
              value,
              style: AppTypography.caption.copyWith(
                color: AppColors.textPrimary,
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _amounts(Invoice invoice) {
    final symbol = invoice.currencyInfo.symbol;
    return Container(
      margin: const EdgeInsets.only(top: 8),
      color: AppColors.surface,
      padding: const EdgeInsets.fromLTRB(16, 14, 16, 14),
      child: Column(
        children: [
          _amountRow('Total', money(invoice.totalAmount, symbol)),
          if (invoice.amountPaid > 0)
            _amountRow('Paid', money(invoice.amountPaid, symbol)),
          const Divider(height: 20),
          _amountRow(
            'Due',
            money(invoice.amountDue, symbol),
            emphasis: true,
            tone: invoice.amountDue > 0 && invoice.isLate
                ? AppColors.danger600
                : null,
          ),
        ],
      ),
    );
  }

  Widget _amountRow(
    String label,
    String value, {
    bool emphasis = false,
    Color? tone,
  }) {
    final style = emphasis
        ? AppTypography.body.copyWith(fontWeight: FontWeight.w700, color: tone)
        : AppTypography.body.copyWith(color: AppColors.textPrimary);
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 2),
      child: Row(
        children: [
          Expanded(
            child: Text(
              label,
              style: AppTypography.caption.copyWith(
                color: AppColors.textSecondary,
              ),
            ),
          ),
          const SizedBox(width: 12),
          Text(value, style: style),
        ],
      ),
    );
  }

  Widget _lineItems(Invoice invoice) {
    final symbol = invoice.currencyInfo.symbol;
    return _section('What this is for', [
      for (final item in invoice.lineItems)
        Padding(
          padding: const EdgeInsets.only(bottom: 10),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      item.name.isEmpty ? 'Untitled line' : item.name,
                      style: AppTypography.caption.copyWith(
                        color: AppColors.textPrimary,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                    const SizedBox(height: 2),
                    Text(
                      [
                        '${item.quantityLabel} x ${money(item.unitPrice, symbol)}',
                        if (item.discountAmount > 0)
                          'less ${money(item.discountAmount, symbol)}',
                      ].join(', '),
                      style: AppTypography.caption.copyWith(
                        color: AppColors.textSecondary,
                      ),
                    ),
                  ],
                ),
              ),
              const SizedBox(width: 12),
              Text(
                money(item.amount, symbol),
                style: AppTypography.caption.copyWith(
                  color: AppColors.textPrimary,
                ),
              ),
            ],
          ),
        ),
    ]);
  }

  Widget _payments(Invoice invoice) {
    final symbol = invoice.currencyInfo.symbol;
    return _section('Payments', [
      for (final payment in invoice.payments)
        Padding(
          padding: const EdgeInsets.only(bottom: 10),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      payment.methodLabel,
                      style: AppTypography.caption.copyWith(
                        color: AppColors.textPrimary,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                    if (payment.paymentDate != null) ...[
                      const SizedBox(height: 2),
                      Text(
                        DateFormat('d MMM yyyy').format(payment.paymentDate!),
                        style: AppTypography.caption.copyWith(
                          color: AppColors.textSecondary,
                        ),
                      ),
                    ],
                    if (payment.referenceNumber?.isNotEmpty == true) ...[
                      const SizedBox(height: 2),
                      Text(
                        'ref ${payment.referenceNumber}',
                        style: AppTypography.caption.copyWith(
                          color: AppColors.textSecondary,
                        ),
                      ),
                    ],
                  ],
                ),
              ),
              const SizedBox(width: 12),
              Text(
                money(payment.amount, symbol),
                style: AppTypography.caption.copyWith(
                  color: AppColors.success600,
                  fontWeight: FontWeight.w600,
                ),
              ),
            ],
          ),
        ),
    ]);
  }

  Widget _section(String title, List<Widget> children) {
    return Container(
      margin: const EdgeInsets.only(top: 8),
      color: AppColors.surface,
      padding: const EdgeInsets.fromLTRB(16, 14, 16, 6),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            title,
            style: AppTypography.caption.copyWith(
              color: AppColors.textSecondary,
              fontWeight: FontWeight.w600,
            ),
          ),
          const SizedBox(height: 10),
          ...children,
        ],
      ),
    );
  }

  /// The actions this invoice can actually take.
  ///
  /// Each condition mirrors a refusal the API enforces, so the buttons that
  /// are missing are the calls that would have come back 400. The server is
  /// still the check that counts; hiding a button is a courtesy, not a guard.
  Widget _actions(Invoice invoice) {
    final buttons = <Widget>[
      if (invoice.canSend)
        _action(
          invoice.status == InvoiceStatus.overdue ? 'Send a reminder' : 'Send',
          LucideIcons.send,
          () => _run(
            () => ref.read(invoicesProvider.notifier).send(invoice.id),
            'Sent to ${invoice.clientEmail.isEmpty ? 'the client' : invoice.clientEmail}',
          ),
        ),
      if (invoice.canRecordPayment)
        _action(
          'Record a payment',
          LucideIcons.banknote,
          () => _recordPayment(invoice),
          primary: true,
        ),
      if (invoice.canCancel)
        _action(
          'Cancel invoice',
          LucideIcons.ban,
          () => _confirmCancel(invoice),
          destructive: true,
        ),
    ];

    if (buttons.isEmpty) return const SizedBox(height: 16);

    return Container(
      margin: const EdgeInsets.only(top: 8),
      color: AppColors.surface,
      padding: const EdgeInsets.fromLTRB(16, 14, 16, 16),
      child: Column(children: buttons),
    );
  }

  Widget _action(
    String label,
    IconData icon,
    VoidCallback onPressed, {
    bool primary = false,
    bool destructive = false,
  }) {
    final child = Row(
      mainAxisAlignment: MainAxisAlignment.center,
      children: [
        Icon(icon, size: 16),
        const SizedBox(width: 8),
        Flexible(
          child: Text(label, maxLines: 1, overflow: TextOverflow.ellipsis),
        ),
      ],
    );
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: SizedBox(
        width: double.infinity,
        height: 46,
        child: primary
            ? FilledButton(onPressed: _busy ? null : onPressed, child: child)
            : OutlinedButton(
                onPressed: _busy ? null : onPressed,
                style: destructive
                    ? OutlinedButton.styleFrom(
                        foregroundColor: AppColors.danger600,
                      )
                    : null,
                child: child,
              ),
      ),
    );
  }

  Future<void> _recordPayment(Invoice invoice) async {
    final result = await showRecordPaymentSheet(context, invoice);
    if (result == null || !mounted) return;
    await _run(
      () => ref
          .read(invoicesProvider.notifier)
          .recordPayment(
            invoice.id,
            amount: result.amount,
            method: result.method,
            reference: result.reference,
          ),
      'Payment recorded',
    );
  }

  Future<void> _confirmCancel(Invoice invoice) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Cancel this invoice?'),
        content: Text(
          'Invoice ${invoice.invoiceNumber} will be marked cancelled. '
          'It cannot be sent or paid afterwards, and this cannot be undone '
          'from the app.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop(false),
            child: const Text('Keep it'),
          ),
          TextButton(
            onPressed: () => Navigator.of(context).pop(true),
            style: TextButton.styleFrom(foregroundColor: AppColors.danger600),
            child: const Text('Cancel invoice'),
          ),
        ],
      ),
    );
    if (confirmed != true || !mounted) return;
    await _run(
      () => ref.read(invoicesProvider.notifier).cancel(invoice.id),
      'Invoice cancelled',
    );
  }

  /// Runs one write, then reloads.
  ///
  /// The reload is unconditional: a refused call still tells us the local copy
  /// is stale (someone else paid it, someone else cancelled it), and leaving
  /// the old state on screen next to an error is how a user tries the same
  /// doomed button twice.
  Future<void> _run(Future<String?> Function() action, String success) async {
    setState(() => _busy = true);
    final error = await action();
    if (!mounted) return;
    setState(() => _busy = false);
    ScaffoldMessenger.of(
      context,
    ).showSnackBar(SnackBar(content: Text(error ?? success)));
    await _load();
  }
}
