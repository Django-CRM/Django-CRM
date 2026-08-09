import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';
import '../../core/theme/theme.dart';

/// Floating Label Input Field
class FloatingLabelInput extends StatefulWidget {
  final String label;
  final String? hint;
  final TextEditingController? controller;
  final String? Function(String?)? validator;
  final TextInputType? keyboardType;
  final bool obscureText;
  final IconData? prefixIcon;
  final Widget? suffix;
  final int maxLines;
  final int? maxLength;
  final bool enabled;
  final ValueChanged<String>? onChanged;
  final VoidCallback? onTap;
  final bool readOnly;
  final List<TextInputFormatter>? inputFormatters;
  final TextCapitalization textCapitalization;
  final FocusNode? focusNode;
  final TextInputAction? textInputAction;
  final ValueChanged<String>? onSubmitted;

  const FloatingLabelInput({
    super.key,
    required this.label,
    this.hint,
    this.controller,
    this.validator,
    this.keyboardType,
    this.obscureText = false,
    this.prefixIcon,
    this.suffix,
    this.maxLines = 1,
    this.maxLength,
    this.enabled = true,
    this.onChanged,
    this.onTap,
    this.readOnly = false,
    this.inputFormatters,
    this.textCapitalization = TextCapitalization.none,
    this.focusNode,
    this.textInputAction,
    this.onSubmitted,
  });

  @override
  State<FloatingLabelInput> createState() => _FloatingLabelInputState();
}

class _FloatingLabelInputState extends State<FloatingLabelInput> {
  late FocusNode _focusNode;
  bool _obscureText = false;

  @override
  void initState() {
    super.initState();
    _focusNode = widget.focusNode ?? FocusNode();
    _obscureText = widget.obscureText;
  }

  @override
  void dispose() {
    if (widget.focusNode == null) {
      _focusNode.dispose();
    }
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return TextFormField(
      controller: widget.controller,
      focusNode: _focusNode,
      validator: widget.validator,
      keyboardType: widget.keyboardType,
      obscureText: _obscureText,
      maxLines: widget.obscureText ? 1 : widget.maxLines,
      maxLength: widget.maxLength,
      enabled: widget.enabled,
      onChanged: widget.onChanged,
      onTap: widget.onTap,
      readOnly: widget.readOnly,
      inputFormatters: widget.inputFormatters,
      textCapitalization: widget.textCapitalization,
      textInputAction: widget.textInputAction,
      onFieldSubmitted: widget.onSubmitted,
      style: AppTypography.body.copyWith(
        color: widget.enabled ? AppColors.textPrimary : AppColors.textDisabled,
      ),
      decoration: InputDecoration(
        labelText: widget.label,
        hintText: widget.hint,
        prefixIcon: widget.prefixIcon != null
            ? Icon(widget.prefixIcon, size: 20)
            : null,
        suffixIcon: widget.obscureText
            ? IconButton(
                icon: Icon(
                  _obscureText ? LucideIcons.eyeOff : LucideIcons.eye,
                  size: 20,
                  color: AppColors.gray400,
                ),
                onPressed: () => setState(() => _obscureText = !_obscureText),
              )
            : widget.suffix,
        counterText: '',
      ),
    );
  }
}

/// Search Input Field
class SearchInput extends StatefulWidget {
  final String? hint;
  final TextEditingController? controller;
  final ValueChanged<String>? onChanged;
  final VoidCallback? onClear;
  final bool autofocus;

  const SearchInput({
    super.key,
    this.hint = 'Search...',
    this.controller,
    this.onChanged,
    this.onClear,
    this.autofocus = false,
  });

  @override
  State<SearchInput> createState() => _SearchInputState();
}

class _SearchInputState extends State<SearchInput> {
  late TextEditingController _controller;
  bool _hasText = false;

  @override
  void initState() {
    super.initState();
    _controller = widget.controller ?? TextEditingController();
    _controller.addListener(_onTextChanged);
    _hasText = _controller.text.isNotEmpty;
  }

  @override
  void dispose() {
    if (widget.controller == null) {
      _controller.dispose();
    }
    super.dispose();
  }

  void _onTextChanged() {
    final hasText = _controller.text.isNotEmpty;
    if (hasText != _hasText) {
      setState(() => _hasText = hasText);
    }
  }

  void _clear() {
    _controller.clear();
    widget.onClear?.call();
    widget.onChanged?.call('');
  }

  @override
  Widget build(BuildContext context) {
    return Container(
      height: AppLayout.searchBarHeight,
      decoration: BoxDecoration(
        color: AppColors.gray100,
        borderRadius: AppLayout.borderRadiusMd,
      ),
      child: TextField(
        controller: _controller,
        autofocus: widget.autofocus,
        onChanged: widget.onChanged,
        style: AppTypography.body,
        decoration: InputDecoration(
          hintText: widget.hint,
          hintStyle: AppTypography.body.copyWith(color: AppColors.gray400),
          prefixIcon: const Icon(
            LucideIcons.search,
            size: 20,
            color: AppColors.gray400,
          ),
          suffixIcon: _hasText
              ? IconButton(
                  icon: const Icon(
                    LucideIcons.x,
                    size: 18,
                    color: AppColors.gray400,
                  ),
                  onPressed: _clear,
                )
              : null,
          border: InputBorder.none,
          enabledBorder: InputBorder.none,
          focusedBorder: InputBorder.none,
          contentPadding: const EdgeInsets.symmetric(
            horizontal: 16,
            vertical: 12,
          ),
        ),
      ),
    );
  }
}

/// Dropdown Select Field
class SelectField<T> extends StatelessWidget {
  final String label;
  final T? value;
  final List<DropdownMenuItem<T>> items;
  final ValueChanged<T?>? onChanged;
  final IconData? prefixIcon;
  final bool enabled;
  final String? Function(T?)? validator;

  const SelectField({
    super.key,
    required this.label,
    this.value,
    required this.items,
    this.onChanged,
    this.prefixIcon,
    this.enabled = true,
    this.validator,
  });

  @override
  Widget build(BuildContext context) {
    return DropdownButtonFormField<T>(
      initialValue: value,
      items: items,
      onChanged: enabled ? onChanged : null,
      validator: validator,
      decoration: InputDecoration(
        labelText: label,
        prefixIcon: prefixIcon != null ? Icon(prefixIcon, size: 20) : null,
      ),
      style: AppTypography.body,
      dropdownColor: AppColors.surface,
      icon: const Icon(LucideIcons.chevronDown, color: AppColors.gray400),
    );
  }
}

/// Text Area Field
class TextAreaField extends StatelessWidget {
  final String label;
  final String? hint;
  final TextEditingController? controller;
  final String? Function(String?)? validator;
  final int minLines;
  final int maxLines;
  final int? maxLength;
  final ValueChanged<String>? onChanged;

  const TextAreaField({
    super.key,
    required this.label,
    this.hint,
    this.controller,
    this.validator,
    this.minLines = 3,
    this.maxLines = 6,
    this.maxLength,
    this.onChanged,
  });

  @override
  Widget build(BuildContext context) {
    return TextFormField(
      controller: controller,
      validator: validator,
      minLines: minLines,
      maxLines: maxLines,
      maxLength: maxLength,
      onChanged: onChanged,
      style: AppTypography.body,
      decoration: InputDecoration(
        labelText: label,
        hintText: hint,
        alignLabelWithHint: true,
      ),
    );
  }
}
