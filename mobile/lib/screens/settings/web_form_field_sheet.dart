import 'package:flutter/material.dart';

import '../../core/theme/theme.dart';
import '../../data/models/custom_field_definition.dart';
import '../../data/models/web_form.dart';

/// Add or edit one question on a web form.
///
/// Returns the row, or `null` if dismissed. Nothing is saved from here: the
/// caller holds the list and writes it back in one request, because the server
/// assigns `order` from list position and a per-row save would have to decide
/// what position meant mid-edit.
///
/// [leadFieldsInUse] are the Lead columns other rows already claim. Two rows
/// writing to the same column is not a database error, it is a form that asks
/// for an email address twice and keeps whichever answer the serializer read
/// last. The picker greys those out rather than leaving someone to find out.
Future<WebFormField?> showWebFormFieldSheet(
  BuildContext context, {
  WebFormField? existing,
  required List<CustomFieldDefinition> customFields,
  required Set<String> leadFieldsInUse,
}) {
  return showModalBottomSheet<WebFormField>(
    context: context,
    isScrollControlled: true,
    builder: (context) => _WebFormFieldSheet(
      existing: existing,
      customFields: customFields,
      leadFieldsInUse: leadFieldsInUse,
    ),
  );
}

class _WebFormFieldSheet extends StatefulWidget {
  const _WebFormFieldSheet({
    this.existing,
    required this.customFields,
    required this.leadFieldsInUse,
  });

  final WebFormField? existing;
  final List<CustomFieldDefinition> customFields;
  final Set<String> leadFieldsInUse;

  @override
  State<_WebFormFieldSheet> createState() => _WebFormFieldSheetState();
}

class _WebFormFieldSheetState extends State<_WebFormFieldSheet> {
  late final TextEditingController _label;
  late final TextEditingController _placeholder;
  late String _source;
  String _leadField = '';
  String? _customField;
  late bool _isRequired;
  String? _error;

  bool get _isCreate => widget.existing == null;

  @override
  void initState() {
    super.initState();
    final field = widget.existing;
    _label = TextEditingController(text: field?.label ?? '');
    _placeholder = TextEditingController(text: field?.placeholder ?? '');
    // A new row starts as a lead field either way. The source toggle is only
    // rendered when the org has defined custom fields for leads, so with none
    // defined this is the single reachable option rather than a default.
    _source = field?.source ?? WebFormField.sourceLead;
    _leadField = field?.leadField ?? '';
    _customField = field?.customField;
    _isRequired = field?.isRequired ?? false;
  }

  @override
  void dispose() {
    _label.dispose();
    _placeholder.dispose();
    super.dispose();
  }

  /// Whether this Lead column is already claimed by a different row.
  bool _taken(String value) =>
      widget.leadFieldsInUse.contains(value) &&
      value != widget.existing?.leadField;

  /// Follow the target with the label, unless somebody typed their own.
  /// Guessing is a convenience; overwriting a person's words is a correction,
  /// and this has no business making one.
  void _suggestLabel(String suggestion, String previousSuggestion) {
    final current = _label.text.trim();
    if (current.isEmpty || current == previousSuggestion) {
      _label.text = suggestion;
    }
  }

  void _submit() {
    final field = WebFormField(
      id: widget.existing?.id,
      source: _source,
      leadField: _source == WebFormField.sourceCustom ? '' : _leadField,
      customField: _source == WebFormField.sourceCustom ? _customField : null,
      label: _label.text.trim(),
      placeholder: _placeholder.text.trim(),
      isRequired: _isRequired,
    );

    // The same two rules `WebFormFieldSerializer` enforces, and behind it the
    // `web_form_field_exactly_one_target` constraint. Checking here is so the
    // person is told before the round trip, not instead of it.
    if (!field.isComplete) {
      setState(() {
        _error = field.label.isEmpty
            ? 'Give the field a label. It is what the visitor reads.'
            : 'Choose what this field writes into.';
      });
      return;
    }
    Navigator.of(context).pop(field);
  }

  @override
  Widget build(BuildContext context) {
    final hasCustomFields = widget.customFields.isNotEmpty;

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
              _isCreate ? 'Add a field' : 'Edit field',
              style: AppTypography.h3.copyWith(fontWeight: FontWeight.w600),
            ),
            const SizedBox(height: 16),

            if (hasCustomFields) ...[
              SegmentedButton<String>(
                segments: const [
                  ButtonSegment(
                    value: WebFormField.sourceLead,
                    label: Text('Lead field'),
                  ),
                  ButtonSegment(
                    value: WebFormField.sourceCustom,
                    label: Text('Custom field'),
                  ),
                ],
                selected: {_source},
                onSelectionChanged: (selection) => setState(() {
                  _source = selection.first;
                  // Clearing both is what keeps the exactly-one-target rule
                  // true: carrying the old target across would leave a row
                  // naming a Lead column and a custom definition at once.
                  _leadField = '';
                  _customField = null;
                }),
              ),
              const SizedBox(height: 12),
            ],

            if (_source == WebFormField.sourceCustom)
              DropdownButtonFormField<String>(
                initialValue: _customField,
                isExpanded: true,
                decoration: const InputDecoration(
                  labelText: 'Custom field',
                  border: OutlineInputBorder(),
                ),
                items: [
                  for (final definition in widget.customFields)
                    DropdownMenuItem(
                      value: definition.id,
                      child: Text(definition.label),
                    ),
                ],
                onChanged: (value) => setState(() {
                  final previous = widget.customFields
                      .where((d) => d.id == _customField)
                      .map((d) => d.label)
                      .join();
                  _customField = value;
                  final picked = widget.customFields
                      .where((d) => d.id == value)
                      .map((d) => d.label)
                      .join();
                  if (picked.isNotEmpty) _suggestLabel(picked, previous);
                }),
              )
            else
              DropdownButtonFormField<String>(
                initialValue: _leadField.isEmpty ? null : _leadField,
                isExpanded: true,
                decoration: const InputDecoration(
                  labelText: 'Writes into',
                  border: OutlineInputBorder(),
                  helperText: 'The field on the lead this answer lands in',
                ),
                items: [
                  for (final field in webFormLeadFields)
                    DropdownMenuItem(
                      value: field.value,
                      enabled: !_taken(field.value),
                      child: Text(
                        _taken(field.value)
                            ? '${field.label} (already on this form)'
                            : field.label,
                        style: _taken(field.value)
                            ? AppTypography.body.copyWith(
                                color: AppColors.textTertiary,
                              )
                            : null,
                      ),
                    ),
                ],
                onChanged: (value) => setState(() {
                  final previous = leadFieldLabel(_leadField);
                  _leadField = value ?? '';
                  _suggestLabel(leadFieldLabel(_leadField), previous);
                }),
              ),

            const SizedBox(height: 12),
            TextField(
              controller: _label,
              textCapitalization: TextCapitalization.sentences,
              maxLength: 255,
              decoration: const InputDecoration(
                labelText: 'Label',
                helperText: 'What the visitor sees above the box',
                border: OutlineInputBorder(),
                counterText: '',
              ),
            ),
            const SizedBox(height: 12),
            TextField(
              controller: _placeholder,
              textCapitalization: TextCapitalization.sentences,
              maxLength: 255,
              decoration: const InputDecoration(
                labelText: 'Placeholder',
                helperText: 'Optional. Shown inside the empty box',
                border: OutlineInputBorder(),
                counterText: '',
              ),
            ),
            const SizedBox(height: 4),
            SwitchListTile.adaptive(
              value: _isRequired,
              onChanged: (value) => setState(() => _isRequired = value),
              contentPadding: EdgeInsets.zero,
              title: const Text('Required'),
              subtitle: const Text(
                'The visitor cannot submit without answering',
              ),
            ),

            if (_error != null) ...[
              const SizedBox(height: 8),
              Text(
                _error!,
                style: AppTypography.caption.copyWith(
                  color: AppColors.danger600,
                ),
              ),
            ],

            const SizedBox(height: 16),
            Row(
              children: [
                Expanded(
                  child: SizedBox(
                    height: 48,
                    child: OutlinedButton(
                      onPressed: () => Navigator.of(context).pop(),
                      child: const Text('Cancel'),
                    ),
                  ),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: SizedBox(
                    height: 48,
                    child: FilledButton(
                      onPressed: _submit,
                      child: Text(_isCreate ? 'Add field' : 'Save field'),
                    ),
                  ),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}
