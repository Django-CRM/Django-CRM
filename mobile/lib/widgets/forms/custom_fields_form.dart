import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

import '../../core/theme/theme.dart';
import '../../data/models/custom_field_definition.dart';
import '../../providers/lookup_provider.dart';
import '../../services/org_date.dart';
import '../common/common.dart';

/// Schema-driven custom-fields editor.
///
/// Loads the per-org `CustomFieldDefinition` rows for the given `targetModel`
/// and renders one input per definition. The current value map is held by the
/// parent; we only call `onChanged` so the parent decides when to persist.
class CustomFieldsForm extends ConsumerWidget {
  final String targetModel;
  final Map<String, dynamic> values;
  final ValueChanged<Map<String, dynamic>> onChanged;

  const CustomFieldsForm({
    super.key,
    required this.targetModel,
    required this.values,
    required this.onChanged,
  });

  void _setValue(String key, dynamic value) {
    final next = Map<String, dynamic>.from(values);
    if (value == null || (value is String && value.isEmpty)) {
      next.remove(key);
    } else {
      next[key] = value;
    }
    onChanged(next);
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final asyncDefs = ref.watch(customFieldDefinitionsProvider(targetModel));

    return asyncDefs.when(
      loading: () => const Padding(
        padding: EdgeInsets.symmetric(vertical: 12),
        child: Center(child: CircularProgressIndicator(strokeWidth: 2)),
      ),
      error: (err, _) => Text(
        'Failed to load custom fields',
        style: AppTypography.caption.copyWith(color: AppColors.danger500),
      ),
      data: (defs) {
        if (defs.isEmpty) return const SizedBox.shrink();
        return Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            for (final def in defs) ...[
              _CustomFieldInput(
                definition: def,
                value: values[def.key],
                onChanged: (v) => _setValue(def.key, v),
              ),
              const SizedBox(height: 16),
            ],
          ],
        );
      },
    );
  }
}

class _CustomFieldInput extends StatelessWidget {
  final CustomFieldDefinition definition;
  final dynamic value;
  final ValueChanged<dynamic> onChanged;

  const _CustomFieldInput({
    required this.definition,
    required this.value,
    required this.onChanged,
  });

  @override
  Widget build(BuildContext context) {
    switch (definition.fieldType) {
      case CustomFieldType.text:
        return _textInput(maxLines: 1);
      case CustomFieldType.textarea:
        return _textInput(maxLines: 4);
      case CustomFieldType.number:
        return _numberInput();
      case CustomFieldType.dropdown:
        return _dropdownInput(context);
      case CustomFieldType.date:
        return _dateInput(context);
      case CustomFieldType.checkbox:
        return _checkboxInput();
    }
  }

  String _label() =>
      definition.isRequired ? '${definition.label} *' : definition.label;

  Widget _textInput({required int maxLines}) {
    return FloatingLabelInput(
      label: _label(),
      controller: TextEditingController(text: value?.toString() ?? ''),
      maxLines: maxLines,
      onChanged: onChanged,
    );
  }

  Widget _numberInput() {
    return FloatingLabelInput(
      label: _label(),
      controller: TextEditingController(text: value?.toString() ?? ''),
      keyboardType: const TextInputType.numberWithOptions(decimal: true),
      inputFormatters: [FilteringTextInputFormatter.allow(RegExp(r'[0-9.\-]'))],
      onChanged: (v) {
        if (v.isEmpty) {
          onChanged(null);
          return;
        }
        final n = num.tryParse(v);
        onChanged(n ?? v);
      },
    );
  }

  Widget _dropdownInput(BuildContext context) {
    final selectedLabel = definition.options
        .where((o) => o.value == value?.toString())
        .map((o) => o.label)
        .firstOrNull;
    return _DropdownField(
      label: _label(),
      valueLabel: selectedLabel ?? 'Select…',
      hasValue: selectedLabel != null,
      onTap: () => _showDropdownSheet(context),
    );
  }

  void _showDropdownSheet(BuildContext context) {
    showModalBottomSheet(
      context: context,
      backgroundColor: AppColors.surface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (_) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Container(
              margin: const EdgeInsets.only(top: 12),
              width: 40,
              height: 4,
              decoration: BoxDecoration(
                color: AppColors.gray300,
                borderRadius: BorderRadius.circular(2),
              ),
            ),
            Padding(
              padding: const EdgeInsets.all(16),
              child: Text(definition.label, style: AppTypography.h3),
            ),
            if (!definition.isRequired && value != null)
              InkWell(
                onTap: () {
                  onChanged(null);
                  Navigator.pop(context);
                },
                child: Padding(
                  padding: const EdgeInsets.symmetric(
                    horizontal: 16,
                    vertical: 14,
                  ),
                  child: Row(
                    children: [
                      Icon(LucideIcons.x, size: 18, color: AppColors.gray500),
                      const SizedBox(width: 12),
                      Text(
                        'Clear',
                        style: AppTypography.body.copyWith(
                          color: AppColors.gray600,
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            for (final opt in definition.options)
              InkWell(
                onTap: () {
                  onChanged(opt.value);
                  Navigator.pop(context);
                },
                child: Padding(
                  padding: const EdgeInsets.symmetric(
                    horizontal: 16,
                    vertical: 14,
                  ),
                  child: Row(
                    children: [
                      Expanded(
                        child: Text(
                          opt.label,
                          style: AppTypography.body.copyWith(
                            fontWeight: opt.value == value?.toString()
                                ? FontWeight.w600
                                : FontWeight.normal,
                            color: opt.value == value?.toString()
                                ? AppColors.primary600
                                : AppColors.textPrimary,
                          ),
                        ),
                      ),
                      if (opt.value == value?.toString())
                        Icon(
                          LucideIcons.check,
                          size: 20,
                          color: AppColors.primary600,
                        ),
                    ],
                  ),
                ),
              ),
            const SizedBox(height: 16),
          ],
        ),
      ),
    );
  }

  Widget _dateInput(BuildContext context) {
    DateTime? parsed;
    if (value is String && (value as String).isNotEmpty) {
      parsed = DateTime.tryParse(value as String);
    }
    final display = parsed == null
        ? 'Select date'
        : '${parsed.year.toString().padLeft(4, '0')}-'
              '${parsed.month.toString().padLeft(2, '0')}-'
              '${parsed.day.toString().padLeft(2, '0')}';
    return _DropdownField(
      label: _label(),
      valueLabel: display,
      hasValue: parsed != null,
      icon: LucideIcons.calendar,
      onTap: () async {
        final picked = await showDatePicker(
          context: context,
          initialDate: parsed ?? orgToday(),
          firstDate: DateTime(2000),
          lastDate: DateTime(2100),
        );
        if (picked != null) {
          final iso =
              '${picked.year.toString().padLeft(4, '0')}-'
              '${picked.month.toString().padLeft(2, '0')}-'
              '${picked.day.toString().padLeft(2, '0')}';
          onChanged(iso);
        }
      },
    );
  }

  Widget _checkboxInput() {
    final checked = value == true || value == 'true' || value == 1;
    return InkWell(
      onTap: () => onChanged(!checked),
      borderRadius: BorderRadius.circular(8),
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 4),
        child: Row(
          children: [
            Checkbox(value: checked, onChanged: (v) => onChanged(v ?? false)),
            Expanded(child: Text(_label(), style: AppTypography.body)),
          ],
        ),
      ),
    );
  }
}

class _DropdownField extends StatelessWidget {
  final String label;
  final String valueLabel;
  final bool hasValue;
  final IconData? icon;
  final VoidCallback onTap;

  const _DropdownField({
    required this.label,
    required this.valueLabel,
    required this.hasValue,
    required this.onTap,
    this.icon,
  });

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          label,
          style: AppTypography.caption.copyWith(color: AppColors.textSecondary),
        ),
        const SizedBox(height: 8),
        GestureDetector(
          onTap: onTap,
          child: Container(
            width: double.infinity,
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 16),
            decoration: BoxDecoration(
              color: AppColors.gray50,
              borderRadius: BorderRadius.circular(12),
              border: Border.all(color: AppColors.border),
            ),
            child: Row(
              children: [
                if (icon != null) ...[
                  Icon(icon, size: 18, color: AppColors.textSecondary),
                  const SizedBox(width: 12),
                ],
                Expanded(
                  child: Text(
                    valueLabel,
                    style: AppTypography.body.copyWith(
                      color: hasValue
                          ? AppColors.textPrimary
                          : AppColors.textSecondary,
                    ),
                  ),
                ),
                Icon(
                  LucideIcons.chevronDown,
                  size: 20,
                  color: AppColors.textSecondary,
                ),
              ],
            ),
          ),
        ),
      ],
    );
  }
}
