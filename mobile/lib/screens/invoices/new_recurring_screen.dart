import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:intl/intl.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

import '../../core/theme/theme.dart';
import '../../data/models/deal.dart' show Currency;
import '../../data/models/lookup_models.dart';
import '../../data/models/recurring_invoice.dart';
import '../../providers/auth_provider.dart';
import '../../providers/invoice_extras_provider.dart';
import '../../providers/lookup_provider.dart';
import 'invoice_format.dart';
import 'line_item_sheet.dart';

/// Set up a billing schedule.
///
/// The same shape as the new-invoice form, since
/// `RecurringInvoiceCreateSerializer` takes the same required pair
/// (`account_id`, `contact_id`), the same contact-belongs-to-account rule and
/// the same nested `line_items`. It reuses the line builder rather than
/// growing a second one.
///
/// **One field the web exposes is deliberately not here.**
/// `next_generation_date` and `start_date` are separate columns and the web
/// offers both, which lets a schedule start in March while first billing
/// today: `check_recurring_invoices` only ever reads
/// `next_generation_date` and never consults `start_date`. This form asks when
/// billing starts once and sends that date as both, so the two cannot
/// disagree. Editing them apart stays possible on the web.
class NewRecurringScreen extends ConsumerStatefulWidget {
  const NewRecurringScreen({super.key});

  @override
  ConsumerState<NewRecurringScreen> createState() => _NewRecurringScreenState();
}

class _NewRecurringScreenState extends ConsumerState<NewRecurringScreen> {
  final _title = TextEditingController();
  final _customDays = TextEditingController();

  String? _accountId;
  String? _contactId;
  late String _currency;
  String _frequency = 'MONTHLY';
  String _paymentTerms = 'NET_30';
  DateTime _startDate = DateTime.now();
  DateTime? _endDate;
  bool _autoSend = false;

  final List<LineItemDraft> _items = [];
  bool _saving = false;
  String? _error;

  static const _terms = {
    'DUE_ON_RECEIPT': 'Due on receipt',
    'NET_15': 'Net 15',
    'NET_30': 'Net 30',
    'NET_45': 'Net 45',
    'NET_60': 'Net 60',
  };

  @override
  void initState() {
    super.initState();
    _currency =
        ref.read(selectedOrgProvider)?.defaultCurrency ?? Currency.usd.value;
  }

  @override
  void dispose() {
    _title.dispose();
    _customDays.dispose();
    super.dispose();
  }

  String get _symbol => Currency.fromString(_currency).symbol;

  double get _subtotal => _items.fold(0, (sum, item) => sum + item.netAmount);

  List<LineItemDraft> get _usableItems =>
      _items.where((i) => i.name.trim().isNotEmpty && i.quantity > 0).toList();

  /// The interval, or null when it is missing or not a positive number.
  int? get _customDaysValue {
    final parsed = int.tryParse(_customDays.text.trim());
    return (parsed == null || parsed < 1) ? null : parsed;
  }

  bool get _ready =>
      _accountId != null &&
      _contactId != null &&
      _title.text.trim().isNotEmpty &&
      _usableItems.isNotEmpty &&
      // A CUSTOM cadence with no interval is refused by the serializer, and
      // before that guard existed it was accepted and silently billed monthly.
      cadenceIsComplete(_frequency, _customDaysValue) &&
      !_saving;

  @override
  Widget build(BuildContext context) {
    final accounts = ref.watch(accountsLookupProvider);
    final contacts = ref.watch(contactsLookupProvider);

    return Scaffold(
      backgroundColor: AppColors.surfaceDim,
      appBar: AppBar(
        title: const Text('New schedule'),
        backgroundColor: AppColors.surface,
        elevation: 0,
        scrolledUnderElevation: 1,
      ),
      body: Column(
        children: [
          Expanded(
            child: ListView(
              padding: const EdgeInsets.only(bottom: 24),
              children: [
                if (_error != null)
                  Container(
                    width: double.infinity,
                    color: AppColors.danger50,
                    padding: const EdgeInsets.all(12),
                    child: Text(
                      _error!,
                      style: AppTypography.caption.copyWith(
                        color: AppColors.danger700,
                      ),
                    ),
                  ),
                _who(accounts, contacts),
                _cadence(),
                _lineItems(),
              ],
            ),
          ),
          _totalBar(),
        ],
      ),
    );
  }

  Widget _who(
    AsyncValue<List<AccountLookup>> accounts,
    AsyncValue<List<ContactLookup>> contacts,
  ) {
    final all = contacts.value ?? const <ContactLookup>[];
    final options = _accountId == null
        ? const <ContactLookup>[]
        : all.where((c) => c.billableFor(_accountId)).toList();

    return _card('Who to bill', [
      DropdownButtonFormField<String>(
        initialValue: _accountId,
        isExpanded: true,
        decoration: const InputDecoration(
          labelText: 'Account',
          border: OutlineInputBorder(),
        ),
        items: [
          for (final a in accounts.value ?? const <AccountLookup>[])
            DropdownMenuItem(value: a.id, child: Text(a.name)),
        ],
        onChanged: (value) => setState(() {
          _accountId = value;
          if (_contactId != null) {
            final kept = all.where((c) => c.id == _contactId).firstOrNull;
            if (kept == null || !kept.billableFor(value)) _contactId = null;
          }
        }),
      ),
      const SizedBox(height: 12),
      DropdownButtonFormField<String>(
        initialValue: _contactId,
        isExpanded: true,
        decoration: InputDecoration(
          labelText: 'Bill to',
          border: const OutlineInputBorder(),
          helperText: _accountId == null ? 'Pick an account first' : null,
        ),
        items: [
          for (final c in options)
            DropdownMenuItem(
              value: c.id,
              child: Text(
                c.accountName == null
                    ? c.fullName
                    : '${c.fullName} · ${c.accountName}',
                overflow: TextOverflow.ellipsis,
              ),
            ),
        ],
        onChanged: _accountId == null
            ? null
            : (value) => setState(() => _contactId = value),
      ),
      const SizedBox(height: 12),
      TextField(
        controller: _title,
        textCapitalization: TextCapitalization.sentences,
        onChanged: (_) => setState(() {}),
        decoration: const InputDecoration(
          labelText: 'What it is for',
          hintText: 'Managed hosting retainer',
          border: OutlineInputBorder(),
        ),
      ),
    ]);
  }

  Widget _cadence() {
    return _card('How often', [
      DropdownButtonFormField<String>(
        initialValue: _frequency,
        isExpanded: true,
        decoration: const InputDecoration(
          labelText: 'Frequency',
          border: OutlineInputBorder(),
        ),
        items: [
          for (final entry in recurringFrequencies.entries)
            DropdownMenuItem(value: entry.key, child: Text(entry.value)),
        ],
        onChanged: (value) => setState(() => _frequency = value ?? _frequency),
      ),
      if (_frequency == 'CUSTOM') ...[
        const SizedBox(height: 12),
        TextField(
          controller: _customDays,
          keyboardType: TextInputType.number,
          inputFormatters: [FilteringTextInputFormatter.digitsOnly],
          onChanged: (_) => setState(() {}),
          decoration: InputDecoration(
            labelText: 'Every how many days',
            border: const OutlineInputBorder(),
            // Not merely a hint. Without an interval the server used to accept
            // the schedule and bill monthly; it now refuses, and either way
            // this is the field that makes CUSTOM mean anything.
            errorText: _customDays.text.trim().isEmpty
                ? null
                : _customDaysValue == null
                ? 'Enter a number of days, at least 1'
                : null,
          ),
        ),
      ],
      const SizedBox(height: 12),
      _dateField(
        label: 'First invoice on',
        value: _startDate,
        onPick: (d) => setState(() => _startDate = d),
      ),
      const SizedBox(height: 12),
      _dateField(
        label: 'Stop after (optional)',
        value: _endDate,
        onPick: (d) => setState(() => _endDate = d),
        onClear: _endDate == null
            ? null
            : () => setState(() => _endDate = null),
      ),
      const SizedBox(height: 12),
      DropdownButtonFormField<String>(
        initialValue: _paymentTerms,
        isExpanded: true,
        decoration: const InputDecoration(
          labelText: 'Payment terms on each invoice',
          border: OutlineInputBorder(),
        ),
        items: [
          for (final entry in _terms.entries)
            DropdownMenuItem(value: entry.key, child: Text(entry.value)),
        ],
        onChanged: (value) =>
            setState(() => _paymentTerms = value ?? _paymentTerms),
      ),
      // Wrapped in its own Material: the card around it is a ColoredBox, and
      // a ListTile paints its ink splash on the nearest Material ancestor, so
      // without this Flutter asserts that the splash would be invisible.
      Material(
        color: AppColors.surface,
        child: SwitchListTile(
          contentPadding: EdgeInsets.zero,
          value: _autoSend,
          onChanged: (v) => setState(() => _autoSend = v),
          title: const Text('Email each invoice automatically'),
          subtitle: const Text(
            'Off means each generated invoice waits as a draft for you to send.',
          ),
        ),
      ),
    ]);
  }

  Widget _lineItems() {
    return _card('What to bill each time', [
      if (_items.isEmpty)
        Padding(
          padding: const EdgeInsets.only(bottom: 8),
          child: Text(
            'A schedule needs at least one line.',
            style: AppTypography.caption.copyWith(
              color: AppColors.textSecondary,
            ),
          ),
        ),
      for (var i = 0; i < _items.length; i++) _lineRow(i),
      const SizedBox(height: 4),
      SizedBox(
        width: double.infinity,
        height: 44,
        child: OutlinedButton.icon(
          onPressed: () => _editLine(null),
          icon: const Icon(LucideIcons.plus, size: 16),
          label: const Text('Add a line'),
        ),
      ),
    ]);
  }

  Widget _lineRow(int index) {
    final item = _items[index];
    return InkWell(
      onTap: () => _editLine(index),
      child: Container(
        decoration: BoxDecoration(
          border: Border(bottom: BorderSide(color: AppColors.gray200)),
        ),
        padding: const EdgeInsets.symmetric(vertical: 10),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    item.name.isEmpty ? 'Untitled line' : item.name,
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: AppTypography.caption.copyWith(
                      color: AppColors.textPrimary,
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                  const SizedBox(height: 2),
                  Text(
                    '${item.quantityLabel} x ${money(item.unitPrice, _symbol)}',
                    style: AppTypography.caption.copyWith(
                      color: AppColors.textSecondary,
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(width: 8),
            Text(
              money(item.netAmount, _symbol),
              style: AppTypography.caption.copyWith(
                color: AppColors.textPrimary,
              ),
            ),
            IconButton(
              icon: const Icon(LucideIcons.x, size: 16),
              tooltip: 'Remove this line',
              constraints: const BoxConstraints(minWidth: 44, minHeight: 44),
              onPressed: () => setState(() => _items.removeAt(index)),
            ),
          ],
        ),
      ),
    );
  }

  Future<void> _editLine(int? index) async {
    final result = await showLineItemSheet(
      context,
      existing: index == null ? null : _items[index],
      symbol: _symbol,
    );
    if (result == null || !mounted) return;
    setState(() {
      if (index == null) {
        _items.add(result);
      } else {
        _items[index] = result;
      }
    });
  }

  Widget _dateField({
    required String label,
    required DateTime? value,
    required ValueChanged<DateTime> onPick,
    VoidCallback? onClear,
  }) {
    return InkWell(
      onTap: () async {
        final picked = await showDatePicker(
          context: context,
          initialDate: value ?? DateTime.now(),
          firstDate: DateTime(2000),
          lastDate: DateTime(2100),
        );
        if (picked != null) onPick(picked);
      },
      child: InputDecorator(
        decoration: InputDecoration(
          labelText: label,
          border: const OutlineInputBorder(),
          suffixIcon: onClear == null
              ? null
              : IconButton(
                  icon: const Icon(LucideIcons.x, size: 16),
                  tooltip: 'Clear',
                  onPressed: onClear,
                ),
        ),
        child: Text(
          value == null
              ? 'Pick a date'
              : DateFormat('d MMM yyyy').format(value),
          style: AppTypography.body.copyWith(
            color: value == null
                ? AppColors.textSecondary
                : AppColors.textPrimary,
          ),
        ),
      ),
    );
  }

  Widget _totalBar() {
    return Container(
      decoration: BoxDecoration(
        color: AppColors.surface,
        border: Border(top: BorderSide(color: AppColors.gray200)),
      ),
      padding: EdgeInsets.only(
        left: 16,
        right: 16,
        top: 10,
        bottom: MediaQuery.of(context).viewInsets.bottom + 10,
      ),
      child: SafeArea(
        top: false,
        child: Row(
          children: [
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text(
                    money(_subtotal, _symbol),
                    style: AppTypography.h3.copyWith(
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                  Text(
                    _frequency == 'CUSTOM' && _customDaysValue != null
                        ? 'every $_customDaysValue days, before tax'
                        : '${(recurringFrequencies[_frequency] ?? '').toLowerCase()}, before tax',
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: AppTypography.caption.copyWith(
                      color: AppColors.textSecondary,
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(width: 12),
            SizedBox(
              height: 46,
              child: FilledButton(
                onPressed: _ready ? _submit : null,
                child: _saving
                    ? const SizedBox(
                        width: 18,
                        height: 18,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Text('Start schedule'),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Future<void> _submit() async {
    setState(() {
      _saving = true;
      _error = null;
    });

    final start = DateFormat('yyyy-MM-dd').format(_startDate);
    final payload = <String, dynamic>{
      'title': _title.text.trim(),
      'account_id': _accountId,
      'contact_id': _contactId,
      'frequency': _frequency,
      'start_date': start,
      // Sent as the same date on purpose. The generator reads only this field
      // and never `start_date`, so letting the two differ would let a schedule
      // that "starts in March" bill today.
      'next_generation_date': start,
      'payment_terms': _paymentTerms,
      'currency': _currency,
      'auto_send': _autoSend,
      'line_items': [
        for (var i = 0; i < _usableItems.length; i++)
          _usableItems[i].toPayload(order: i),
      ],
      if (_frequency == 'CUSTOM') 'custom_days': _customDaysValue,
      if (_endDate != null)
        'end_date': DateFormat('yyyy-MM-dd').format(_endDate!),
    };

    final error = await ref
        .read(recurringProvider.notifier)
        .createSchedule(payload);
    if (!mounted) return;
    setState(() => _saving = false);

    if (error != null) {
      setState(() => _error = error);
      return;
    }
    ScaffoldMessenger.of(
      context,
    ).showSnackBar(const SnackBar(content: Text('Schedule created')));
    context.pop();
  }

  Widget _card(String title, List<Widget> children) {
    return Container(
      margin: const EdgeInsets.only(top: 8),
      color: AppColors.surface,
      padding: const EdgeInsets.fromLTRB(16, 14, 16, 16),
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
          const SizedBox(height: 12),
          ...children,
        ],
      ),
    );
  }
}
