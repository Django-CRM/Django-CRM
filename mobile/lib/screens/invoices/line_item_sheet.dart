import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

import '../../core/theme/theme.dart';
import '../../data/models/product.dart';
import '../../providers/invoice_extras_provider.dart';
import 'invoice_format.dart';

/// One line being written, before it is sent.
///
/// Kept separate from `InvoiceLineItem` (the read model) because the two carry
/// different fields: the server computes `subtotal`, `tax_amount` and `total`
/// and never accepts them, so a draft that held those would invite sending
/// numbers the API ignores.
class LineItemDraft {
  const LineItemDraft({
    required this.name,
    this.description = '',
    this.quantity = 1,
    this.unitPrice = 0,
    this.productId,
    this.discountType = '',
    this.discountValue = 0,
  });

  final String name;
  final String description;
  final double quantity;
  final double unitPrice;

  /// The line's own discount. Only a line started from a deal carries one,
  /// copied from the deal line; the sheet keeps it when the line is edited.
  /// `PERCENTAGE` takes that share of the line, anything else a flat amount,
  /// the same reading as the server's `line_discount`.
  final String discountType;
  final double discountValue;

  /// Set when the line came from the catalogue. The name and price are copied
  /// rather than referenced, matching the server: `InvoiceLineItem`
  /// denormalises both so a later price change never rewrites this invoice.
  final String? productId;

  /// Quantity x unit price less the line's own discount: the server's
  /// `net_amount`, which is what the line adds to the subtotal.
  double get netAmount {
    final gross = quantity * unitPrice;
    return gross -
        (discountType == 'PERCENTAGE'
            ? gross * discountValue / 100
            : discountValue);
  }

  /// "less 10%" or "less €5.00", or null for a line with no discount.
  String? discountLabel(String symbol) {
    if (discountValue == 0) return null;
    if (discountType != 'PERCENTAGE') {
      return 'less ${money(discountValue, symbol)}';
    }
    final whole = discountValue.truncateToDouble() == discountValue;
    return 'less ${whole ? discountValue.toStringAsFixed(0) : discountValue}%';
  }

  String get quantityLabel {
    final whole = quantity.truncateToDouble() == quantity;
    return whole ? quantity.toStringAsFixed(0) : quantity.toString();
  }

  /// Exactly the keys `InvoiceLineItemCreateSerializer` accepts.
  Map<String, dynamic> toPayload({required int order}) {
    return {
      'name': name.trim(),
      'description': description.trim(),
      'quantity': quantity.toString(),
      'unit_price': unitPrice.toStringAsFixed(2),
      'order': order,
      if (productId != null) 'product': productId,
      if (discountValue != 0) ...{
        'discount_type': discountType,
        'discount_value': discountValue.toStringAsFixed(2),
      },
    };
  }
}

/// The editable list of lines shared by the invoice and estimate forms: a row
/// per line (tap to edit, x to remove) and an "Add a line" button, both
/// opening [showLineItemSheet].
///
/// The form owns the list; every change is reported through [onChanged] as a
/// new list, so the form's running total rebuilds from it.
class LineItemsSection extends StatelessWidget {
  const LineItemsSection({
    super.key,
    required this.items,
    required this.symbol,
    required this.emptyHint,
    required this.onChanged,
  });

  final List<LineItemDraft> items;
  final String symbol;
  final String emptyHint;
  final ValueChanged<List<LineItemDraft>> onChanged;

  Future<void> _edit(BuildContext context, int? index) async {
    final result = await showLineItemSheet(
      context,
      existing: index == null ? null : items[index],
      symbol: symbol,
    );
    if (result == null) return;
    final next = [...items];
    if (index == null) {
      next.add(result);
    } else {
      next[index] = result;
    }
    onChanged(next);
  }

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (items.isEmpty)
          Padding(
            padding: const EdgeInsets.only(bottom: 8),
            child: Text(
              emptyHint,
              style: AppTypography.caption.copyWith(
                color: AppColors.textSecondary,
              ),
            ),
          ),
        for (var i = 0; i < items.length; i++) _row(context, i),
        const SizedBox(height: 4),
        SizedBox(
          width: double.infinity,
          height: 44,
          child: OutlinedButton.icon(
            onPressed: () => _edit(context, null),
            icon: const Icon(LucideIcons.plus, size: 16),
            label: const Text('Add a line'),
          ),
        ),
      ],
    );
  }

  Widget _row(BuildContext context, int index) {
    final item = items[index];
    return InkWell(
      onTap: () => _edit(context, index),
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
                    [
                      '${item.quantityLabel} x ${money(item.unitPrice, symbol)}',
                      ?item.discountLabel(symbol),
                    ].join(', '),
                    style: AppTypography.caption.copyWith(
                      color: AppColors.textSecondary,
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(width: 8),
            Text(
              money(item.netAmount, symbol),
              style: AppTypography.caption.copyWith(
                color: AppColors.textPrimary,
              ),
            ),
            IconButton(
              icon: const Icon(LucideIcons.x, size: 16),
              tooltip: 'Remove this line',
              constraints: const BoxConstraints(minWidth: 44, minHeight: 44),
              onPressed: () => onChanged([...items]..removeAt(index)),
            ),
          ],
        ),
      ),
    );
  }
}

/// Add or edit one line.
///
/// A sheet rather than inline inputs: three number fields per row inside a
/// scrolling list at 390px means a keyboard covering the row being typed into.
Future<LineItemDraft?> showLineItemSheet(
  BuildContext context, {
  LineItemDraft? existing,
  required String symbol,
}) {
  return showModalBottomSheet<LineItemDraft>(
    context: context,
    isScrollControlled: true,
    builder: (context) => _LineItemSheet(existing: existing, symbol: symbol),
  );
}

class _LineItemSheet extends ConsumerStatefulWidget {
  const _LineItemSheet({this.existing, required this.symbol});

  final LineItemDraft? existing;
  final String symbol;

  @override
  ConsumerState<_LineItemSheet> createState() => _LineItemSheetState();
}

class _LineItemSheetState extends ConsumerState<_LineItemSheet> {
  late final TextEditingController _name;
  late final TextEditingController _description;
  late final TextEditingController _quantity;
  late final TextEditingController _unitPrice;
  String? _productId;
  String? _nameError;
  String? _quantityError;

  @override
  void initState() {
    super.initState();
    final e = widget.existing;
    _name = TextEditingController(text: e?.name ?? '');
    _description = TextEditingController(text: e?.description ?? '');
    _quantity = TextEditingController(text: e?.quantityLabel ?? '1');
    _unitPrice = TextEditingController(
      text: e == null ? '' : e.unitPrice.toStringAsFixed(2),
    );
    _productId = e?.productId;
  }

  @override
  void dispose() {
    _name.dispose();
    _description.dispose();
    _quantity.dispose();
    _unitPrice.dispose();
    super.dispose();
  }

  /// Fills the line from the catalogue, then lets it be edited.
  ///
  /// The product id travels with the line so the invoice records where the
  /// price came from, but the name and price are plain text from here on: the
  /// server denormalises both, so editing them does not touch the catalogue.
  void _useProduct(Product product) {
    setState(() {
      _productId = product.id;
      _name.text = product.name;
      _unitPrice.text = product.price.toStringAsFixed(2);
      if (_quantity.text.trim().isEmpty) _quantity.text = '1';
    });
  }

  void _submit() {
    final name = _name.text.trim();
    final quantity = double.tryParse(_quantity.text.trim());

    setState(() {
      _nameError = name.isEmpty ? 'Every line needs a description' : null;
      _quantityError = quantity == null
          ? 'Enter a quantity'
          : quantity <= 0
          // A zero or negative quantity contributes nothing or subtracts, and
          // an invoice line that reduces the bill is a credit note, not this.
          ? 'A quantity has to be more than zero'
          : null;
    });
    if (_nameError != null || _quantityError != null) return;

    Navigator.of(context).pop(
      LineItemDraft(
        name: name,
        description: _description.text.trim(),
        quantity: quantity!,
        unitPrice: double.tryParse(_unitPrice.text.trim()) ?? 0,
        productId: _productId,
        discountType: widget.existing?.discountType ?? '',
        discountValue: widget.existing?.discountValue ?? 0,
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final products = ref.watch(productsProvider).value ?? const <Product>[];
    // The line as it would be saved, discount included, for the preview.
    final preview = LineItemDraft(
      name: '',
      quantity: double.tryParse(_quantity.text.trim()) ?? 0,
      unitPrice: double.tryParse(_unitPrice.text.trim()) ?? 0,
      discountType: widget.existing?.discountType ?? '',
      discountValue: widget.existing?.discountValue ?? 0,
    );

    return Padding(
      padding: EdgeInsets.only(
        left: 16,
        right: 16,
        top: 16,
        bottom: MediaQuery.of(context).viewInsets.bottom + 16,
      ),
      child: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              widget.existing == null ? 'Add a line' : 'Edit the line',
              style: AppTypography.h3.copyWith(fontWeight: FontWeight.w600),
            ),
            const SizedBox(height: 12),
            // Only when the catalogue has something in it. An empty picker
            // above every line would suggest one is required, and it is not:
            // a free-text line is the ordinary case.
            if (products.isNotEmpty) ...[
              DropdownButtonFormField<String>(
                initialValue: null,
                isExpanded: true,
                decoration: const InputDecoration(
                  labelText: 'Start from the catalogue (optional)',
                  border: OutlineInputBorder(),
                ),
                items: [
                  for (final p in products.where((p) => p.isActive))
                    DropdownMenuItem(
                      value: p.id,
                      child: Text(
                        '${p.name} · ${money(p.price, widget.symbol)}',
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                ],
                onChanged: (id) {
                  final picked = products.where((p) => p.id == id).firstOrNull;
                  if (picked != null) _useProduct(picked);
                },
              ),
              const SizedBox(height: 12),
            ],
            TextField(
              controller: _name,
              textCapitalization: TextCapitalization.sentences,
              decoration: InputDecoration(
                labelText: 'Description',
                border: const OutlineInputBorder(),
                errorText: _nameError,
              ),
            ),
            const SizedBox(height: 12),
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Expanded(
                  child: TextField(
                    controller: _quantity,
                    keyboardType: const TextInputType.numberWithOptions(
                      decimal: true,
                    ),
                    inputFormatters: [
                      FilteringTextInputFormatter.allow(
                        RegExp(r'^\d*\.?\d{0,2}'),
                      ),
                    ],
                    onChanged: (_) => setState(() {}),
                    decoration: InputDecoration(
                      labelText: 'Quantity',
                      border: const OutlineInputBorder(),
                      errorText: _quantityError,
                    ),
                  ),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: TextField(
                    controller: _unitPrice,
                    keyboardType: const TextInputType.numberWithOptions(
                      decimal: true,
                    ),
                    inputFormatters: [
                      FilteringTextInputFormatter.allow(
                        RegExp(r'^\d*\.?\d{0,2}'),
                      ),
                    ],
                    onChanged: (_) => setState(() {}),
                    decoration: InputDecoration(
                      labelText: 'Unit price',
                      prefixText: widget.symbol,
                      border: const OutlineInputBorder(),
                    ),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 8),
            Align(
              alignment: Alignment.centerRight,
              child: Text(
                [
                  'Line total ${money(preview.netAmount, widget.symbol)}',
                  ?preview.discountLabel(widget.symbol),
                ].join(', '),
                style: AppTypography.caption.copyWith(
                  color: AppColors.textSecondary,
                ),
              ),
            ),
            const SizedBox(height: 12),
            SizedBox(
              width: double.infinity,
              height: 46,
              child: FilledButton(
                onPressed: _submit,
                child: Text(widget.existing == null ? 'Add line' : 'Save line'),
              ),
            ),
            const SizedBox(height: 8),
          ],
        ),
      ),
    );
  }
}
