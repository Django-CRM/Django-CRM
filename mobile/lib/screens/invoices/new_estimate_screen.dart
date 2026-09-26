import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:intl/intl.dart';

import '../../core/theme/theme.dart';
import '../../data/models/deal.dart' show Currency, Deal;
import '../../data/models/lookup_models.dart';
import '../../providers/auth_provider.dart';
import '../../providers/invoice_extras_provider.dart';
import '../../providers/lookup_provider.dart';
import 'invoice_format.dart';
import 'line_item_sheet.dart';

/// Raise an estimate, blank or from a deal.
///
/// The invoice form's shape with an estimate's fields: a validity date instead
/// of payment terms, an optional deal link, and terms beside the notes. The
/// line list is the invoice form's own [LineItemsSection].
///
/// Started from a deal ([fromDeal]) it begins with that deal's account,
/// currency, name, first contact and lines, all still editable. A prefilled
/// account or contact the pickers do not offer counts as unset, so the form
/// never submits an id the person cannot see on screen, and the API checks the
/// account, contact and deal against what this caller may open anyway.
///
/// Only what `EstimateCreateSerializer` accepts is sent. The number, public
/// token, status (always Draft), org, creator and every total are the server's.
class NewEstimateScreen extends ConsumerStatefulWidget {
  const NewEstimateScreen({super.key, this.fromDeal});

  final Deal? fromDeal;

  @override
  ConsumerState<NewEstimateScreen> createState() => _NewEstimateScreenState();
}

class _NewEstimateScreenState extends ConsumerState<NewEstimateScreen> {
  final _title = TextEditingController();
  final _notes = TextEditingController();
  final _terms = TextEditingController();

  String? _accountId;
  String? _contactId;

  /// The deal's contacts, in its order, until the person picks one. The deal
  /// can carry contacts this caller may not open, so the first of these the
  /// picker offers is used rather than simply the first.
  List<String> _dealContactIds = const [];
  String? _dealId;
  late String _currency;
  DateTime _issueDate = DateUtils.dateOnly(DateTime.now());
  DateTime? _expiryDate = DateUtils.dateOnly(
    DateTime.now(),
  ).add(const Duration(days: 30));

  final List<LineItemDraft> _items = [];
  bool _saving = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    final deal = widget.fromDeal;
    _currency =
        deal?.currency.value ??
        ref.read(selectedOrgProvider)?.defaultCurrency ??
        Currency.usd.value;
    if (deal != null) {
      _accountId = deal.accountId;
      _dealContactIds = deal.contactIds;
      _dealId = deal.id;
      _title.text = 'Estimate for ${deal.title}';
      _items.addAll([
        for (final p in deal.products)
          if (p.name.trim().isNotEmpty && p.quantity > 0)
            LineItemDraft(
              name: p.name,
              quantity: p.quantity,
              unitPrice: p.unitPrice,
              // The price the deal was agreed at, not the list price.
              discountType: p.discountType,
              discountValue: p.discountValue,
            ),
      ]);
    }
  }

  @override
  void dispose() {
    _title.dispose();
    _notes.dispose();
    _terms.dispose();
    super.dispose();
  }

  String get _symbol => Currency.fromString(_currency).symbol;

  double get _subtotal => _items.fold(0, (sum, item) => sum + item.netAmount);

  bool get _datesBackwards =>
      _expiryDate != null && _expiryDate!.isBefore(_issueDate);

  /// The account, only if the picker offers it.
  String? _account(List<AccountLookup> accounts) =>
      accounts.any((a) => a.id == _accountId) ? _accountId : null;

  /// The contact, only if the picker offers it for the current account.
  String? _contact(List<ContactLookup> options) {
    bool offered(String? id) => options.any((c) => c.id == id);
    if (offered(_contactId)) return _contactId;
    return _dealContactIds.where(offered).firstOrNull;
  }

  /// The deals to offer. The one this form started from is always among them,
  /// since the caller just had it open, even if the lookup's page missed it.
  List<EntityLookup> _dealOptions(List<EntityLookup> deals) {
    final deal = widget.fromDeal;
    if (deal == null || deals.any((d) => d.id == deal.id)) return deals;
    return [EntityLookup(id: deal.id, label: deal.title), ...deals];
  }

  @override
  Widget build(BuildContext context) {
    final accounts = ref.watch(accountsLookupProvider).value ?? const [];
    final contacts = ref.watch(contactsLookupProvider).value ?? const [];
    final deals = _dealOptions(
      ref.watch(opportunityEntityOptionsProvider).value ?? const [],
    );

    final accountId = _account(accounts);
    final contactOptions = accountId == null
        ? const <ContactLookup>[]
        : contacts.where((c) => c.billableFor(accountId)).toList();
    final contactId = _contact(contactOptions);
    final dealId = deals.any((d) => d.id == _dealId) ? _dealId : null;

    final usable = _items
        .where((i) => i.name.trim().isNotEmpty && i.quantity > 0)
        .toList();
    final ready =
        accountId != null &&
        contactId != null &&
        _title.text.trim().isNotEmpty &&
        usable.isNotEmpty &&
        !_datesBackwards &&
        !_saving;

    return Scaffold(
      backgroundColor: AppColors.surfaceDim,
      appBar: AppBar(
        title: const Text('New estimate'),
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
                _card('Who and when', [
                  DropdownButtonFormField<String>(
                    initialValue: accountId,
                    isExpanded: true,
                    decoration: const InputDecoration(
                      labelText: 'Account',
                      border: OutlineInputBorder(),
                    ),
                    items: [
                      for (final a in accounts)
                        DropdownMenuItem(value: a.id, child: Text(a.name)),
                    ],
                    onChanged: (value) => setState(() => _accountId = value),
                  ),
                  const SizedBox(height: 12),
                  DropdownButtonFormField<String>(
                    initialValue: contactId,
                    isExpanded: true,
                    decoration: InputDecoration(
                      labelText: 'Quote to',
                      border: const OutlineInputBorder(),
                      helperText: accountId == null
                          ? 'Pick an account first'
                          : null,
                    ),
                    items: [
                      for (final c in contactOptions)
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
                    onChanged: accountId == null
                        ? null
                        : (value) => setState(() {
                            _contactId = value;
                            _dealContactIds = const [];
                          }),
                  ),
                  const SizedBox(height: 12),
                  DropdownButtonFormField<String>(
                    initialValue: dealId,
                    isExpanded: true,
                    decoration: const InputDecoration(
                      labelText: 'Deal (optional)',
                      border: OutlineInputBorder(),
                    ),
                    items: [
                      const DropdownMenuItem<String>(child: Text('No deal')),
                      for (final d in deals)
                        DropdownMenuItem(
                          value: d.id,
                          child: Text(d.label, overflow: TextOverflow.ellipsis),
                        ),
                    ],
                    onChanged: (value) => setState(() => _dealId = value),
                  ),
                  const SizedBox(height: 12),
                  TextField(
                    controller: _title,
                    textCapitalization: TextCapitalization.sentences,
                    onChanged: (_) => setState(() {}),
                    decoration: const InputDecoration(
                      labelText: 'What it is for',
                      hintText: 'Website rebuild',
                      border: OutlineInputBorder(),
                    ),
                  ),
                  const SizedBox(height: 12),
                  Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Expanded(
                        child: _dateField(
                          label: 'Issued',
                          value: _issueDate,
                          onPick: (d) => setState(() => _issueDate = d),
                        ),
                      ),
                      const SizedBox(width: 8),
                      Expanded(
                        child: _dateField(
                          label: 'Valid until',
                          value: _expiryDate,
                          error: _datesBackwards ? 'Before it is issued' : null,
                          onPick: (d) => setState(() => _expiryDate = d),
                        ),
                      ),
                    ],
                  ),
                ]),
                _card('What you are quoting', [
                  LineItemsSection(
                    items: _items,
                    symbol: _symbol,
                    emptyHint: 'An estimate needs at least one line.',
                    onChanged: (items) => setState(() {
                      _items
                        ..clear()
                        ..addAll(items);
                    }),
                  ),
                ]),
                _card('Anything else', [
                  DropdownButtonFormField<String>(
                    initialValue: _currency,
                    isExpanded: true,
                    decoration: const InputDecoration(
                      labelText: 'Currency',
                      border: OutlineInputBorder(),
                    ),
                    items: [
                      for (final c in Currency.values)
                        DropdownMenuItem(value: c.value, child: Text(c.label)),
                    ],
                    onChanged: (value) =>
                        setState(() => _currency = value ?? _currency),
                  ),
                  const SizedBox(height: 12),
                  TextField(
                    controller: _notes,
                    maxLines: 3,
                    textCapitalization: TextCapitalization.sentences,
                    decoration: const InputDecoration(
                      labelText: 'Notes for the client (optional)',
                      border: OutlineInputBorder(),
                    ),
                  ),
                  const SizedBox(height: 12),
                  TextField(
                    controller: _terms,
                    maxLines: 3,
                    textCapitalization: TextCapitalization.sentences,
                    decoration: const InputDecoration(
                      labelText: 'Terms (optional)',
                      border: OutlineInputBorder(),
                    ),
                  ),
                ]),
              ],
            ),
          ),
          _totalBar(
            ready
                ? () => _submit(
                    accountId: accountId,
                    contactId: contactId,
                    dealId: dealId,
                    lines: usable,
                  )
                : null,
          ),
        ],
      ),
    );
  }

  Widget _dateField({
    required String label,
    required DateTime? value,
    required ValueChanged<DateTime> onPick,
    String? error,
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
          errorText: error,
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

  Widget _totalBar(VoidCallback? onSubmit) {
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
                    // A deal's line discounts are already in; tax and the
                    // estimate's own discount are not.
                    'before tax and any overall discount',
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
                onPressed: onSubmit,
                child: _saving
                    ? const SizedBox(
                        width: 18,
                        height: 18,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Text('Create draft'),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Future<void> _submit({
    required String accountId,
    required String contactId,
    required String? dealId,
    required List<LineItemDraft> lines,
  }) async {
    setState(() {
      _saving = true;
      _error = null;
    });

    final format = DateFormat('yyyy-MM-dd');
    final payload = <String, dynamic>{
      'account_id': accountId,
      'contact_id': contactId,
      'title': _title.text.trim(),
      'currency': _currency,
      'issue_date': format.format(_issueDate),
      if (_expiryDate != null) 'expiry_date': format.format(_expiryDate!),
      'opportunity_id': ?dealId,
      'line_items': [
        for (var i = 0; i < lines.length; i++) lines[i].toPayload(order: i),
      ],
      if (_notes.text.trim().isNotEmpty) 'notes': _notes.text.trim(),
      if (_terms.text.trim().isNotEmpty) 'terms': _terms.text.trim(),
    };

    final error = await ref.read(estimatesProvider.notifier).create(payload);
    if (!mounted) return;
    setState(() => _saving = false);

    if (error != null) {
      setState(() => _error = error);
      return;
    }
    ScaffoldMessenger.of(
      context,
    ).showSnackBar(const SnackBar(content: Text('Draft estimate created')));
    // Back to where it was started: the estimates list (which has just
    // refreshed and shows the draft with its Send button) or the deal.
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
