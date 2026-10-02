import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

import '../../core/theme/theme.dart';
import '../../data/models/invoice_template.dart';
import '../../providers/invoice_extras_provider.dart';
import '../../services/attachment_upload.dart';
import '../../widgets/forms/unsaved_changes.dart';

/// Create or edit a PDF template. One screen for both, the way the documents,
/// accounts and contacts forms already work here.
///
/// **What this form does not touch.** `template_html` and `template_css` are
/// org-authored markup that WeasyPrint renders into a PDF server-side. They are
/// not read here, not shown here and not sent here, so they stay exactly as the
/// web editor left them: `PUT /invoices/templates/<id>/` is partial, and a key
/// a payload omits is a key the serializer never sees. That is what makes a
/// template form on a phone safe, and it is why the screen says where the
/// layout is edited rather than pretending the layout is all there is.
///
/// The seven fields it does own are ordinary form input, which is what made
/// this worth building: a name, two brand colours, a logo, and the boilerplate
/// text every new invoice starts from.
///
/// `is_default` appears on create and not on edit. It is a singleton the model
/// swaps inside a transaction, so an off switch riding along with an unrelated
/// save would demote the org's current default and leave it with none. The list
/// screen owns it. The web's two forms split it the same way.
///
/// Every write is admin-only server-side. The list hides the way in from
/// everyone else, and this screen shows the 403's own message to anyone who
/// arrives at the route directly.
class InvoiceTemplateFormScreen extends ConsumerStatefulWidget {
  const InvoiceTemplateFormScreen({super.key, this.templateId});

  /// Null creates. An id edits, and is resolved against the catalogue rather
  /// than fetched: every field this form writes is already on the list row, so
  /// there is nothing a detail call would add.
  final String? templateId;

  bool get isEdit => templateId != null;

  @override
  ConsumerState<InvoiceTemplateFormScreen> createState() =>
      _InvoiceTemplateFormScreenState();
}

class _InvoiceTemplateFormScreenState
    extends ConsumerState<InvoiceTemplateFormScreen> {
  final _name = TextEditingController();
  final _primary = TextEditingController(
    text: InvoiceTemplate.defaultPrimaryColor,
  );
  final _secondary = TextEditingController(
    text: InvoiceTemplate.defaultSecondaryColor,
  );
  final _notes = TextEditingController();
  final _terms = TextEditingController();
  final _footer = TextEditingController();

  bool _isDefault = false;
  PlatformFile? _logo;
  bool _hadLogo = false;

  bool _prefilled = false;
  bool _dirty = false;
  bool _saving = false;
  String? _error;
  String? _nameError;
  String? _primaryError;
  String? _secondaryError;

  @override
  void initState() {
    super.initState();
    for (final c in [_name, _primary, _secondary, _notes, _terms, _footer]) {
      c.addListener(_markDirty);
    }
  }

  /// A form that opens already dirty prompts on the way out of a screen nobody
  /// edited, so the prefill below clears the flag after filling the fields.
  void _markDirty() {
    if (!_dirty) _dirty = true;
  }

  @override
  void dispose() {
    for (final c in [_name, _primary, _secondary, _notes, _terms, _footer]) {
      c.dispose();
    }
    super.dispose();
  }

  void _prefill(InvoiceTemplate t) {
    _name.text = t.name;
    _primary.text = t.primaryColor ?? InvoiceTemplate.defaultPrimaryColor;
    _secondary.text = t.secondaryColor ?? InvoiceTemplate.defaultSecondaryColor;
    _notes.text = t.defaultNotes;
    _terms.text = t.defaultTerms;
    _footer.text = t.footerText;
    _hadLogo = t.hasLogo;
    _prefilled = true;
    _dirty = false;
  }

  InvoiceTemplate? _find(List<InvoiceTemplate>? all) {
    if (all == null) return null;
    for (final t in all) {
      if (t.id == widget.templateId) return t;
    }
    return null;
  }

  Future<void> _pickLogo() async {
    // Reuses the attachment picker for its path and size checks, narrowed to
    // images because `logo` is an ImageField: Pillow refuses anything else
    // server-side, and a 400 after an upload is a poor way to learn that.
    final result = await selectAttachment(
      pickFile: () => FilePicker.pickFile(type: FileType.image),
    );
    if (result.cancelled || !mounted) return;
    if (result.error != null) {
      setState(() => _error = result.error);
      return;
    }
    setState(() {
      _logo = result.file;
      _dirty = true;
      _error = null;
    });
  }

  Future<void> _save() async {
    final name = _name.text.trim();
    final primary = _primary.text.trim();
    final secondary = _secondary.text.trim();

    setState(() {
      _nameError = name.isEmpty ? 'Give the template a name' : null;
      // Mirrors `_validate_hex_color` in the serializer. Both colours are
      // substituted into the PDF stylesheet, so a value a renderer cannot parse
      // prints every invoice in the org with a broken accent. The server
      // refuses it too; this saves the round trip.
      _primaryError = InvoiceTemplateDraft.isHexColor(primary)
          ? null
          : 'Use a six digit hex colour, for example #3B82F6';
      _secondaryError = InvoiceTemplateDraft.isHexColor(secondary)
          ? null
          : 'Use a six digit hex colour, for example #1E40AF';
      // The button sits below the fields on a 390px screen, so a tap that only
      // lights an `errorText` several hundred pixels up the form reads as a
      // tap that did nothing. This puts a line where the thumb already is.
      _error =
          _nameError == null && _primaryError == null && _secondaryError == null
          ? null
          : 'Check the highlighted fields above.';
    });
    if (_error != null) return;

    final draft = InvoiceTemplateDraft(
      name: name,
      primaryColor: primary,
      secondaryColor: secondary,
      defaultNotes: _notes.text.trim(),
      defaultTerms: _terms.text.trim(),
      footerText: _footer.text.trim(),
      // Null on an edit: the list owns the default, and sending `false` here
      // would demote whichever template currently holds it.
      isDefault: widget.isEdit ? null : _isDefault,
    );

    setState(() {
      _saving = true;
      _error = null;
    });

    final notifier = ref.read(invoiceTemplatesProvider.notifier);
    final error = widget.isEdit
        ? await notifier.updateTemplate(
            widget.templateId!,
            draft,
            logoPath: _logo?.path,
          )
        : await notifier.createTemplate(draft, logoPath: _logo?.path);

    if (_logo != null) await clearAttachmentPickerCache();
    if (!mounted) return;
    setState(() => _saving = false);

    if (error != null) {
      setState(() => _error = error);
      return;
    }
    _dirty = false;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(widget.isEdit ? 'Template saved' : 'Template created'),
      ),
    );
    context.pop();
  }

  @override
  Widget build(BuildContext context) {
    final async = ref.watch(invoiceTemplatesProvider);

    if (widget.isEdit && !_prefilled) {
      final existing = _find(async.value);
      if (existing != null) {
        _prefill(existing);
      } else if (async.isLoading) {
        return _shell(const Center(child: CircularProgressIndicator()));
      } else {
        return _shell(
          Center(
            child: Padding(
              padding: const EdgeInsets.all(24),
              child: Text(
                'That template is no longer in the catalogue.',
                textAlign: TextAlign.center,
                style: AppTypography.body.copyWith(
                  color: AppColors.textSecondary,
                ),
              ),
            ),
          ),
        );
      }
    }

    return UnsavedChangesGuard(
      hasUnsavedChanges: () => _dirty,
      isSaving: _saving,
      child: _shell(
        ListView(
          padding: const EdgeInsets.only(bottom: 32),
          children: [
            _card('Name', [
              TextField(
                controller: _name,
                textCapitalization: TextCapitalization.sentences,
                maxLength: 100,
                decoration: InputDecoration(
                  labelText: 'Template name',
                  border: const OutlineInputBorder(),
                  errorText: _nameError,
                ),
              ),
            ]),
            _card('Brand colours', [
              _ColourField(
                label: 'Primary',
                controller: _primary,
                errorText: _primaryError,
                onPicked: (hex) => setState(() {
                  _primary.text = hex;
                  _primaryError = null;
                }),
              ),
              const SizedBox(height: 20),
              _ColourField(
                label: 'Secondary',
                controller: _secondary,
                errorText: _secondaryError,
                onPicked: (hex) => setState(() {
                  _secondary.text = hex;
                  _secondaryError = null;
                }),
              ),
            ]),
            _card('Logo', [_logoRow()]),
            _card('Boilerplate', [
              TextField(
                controller: _notes,
                textCapitalization: TextCapitalization.sentences,
                minLines: 2,
                maxLines: 4,
                decoration: const InputDecoration(
                  labelText: 'Default notes',
                  hintText: 'Thanks for your business.',
                  border: OutlineInputBorder(),
                ),
              ),
              const SizedBox(height: 12),
              TextField(
                controller: _terms,
                textCapitalization: TextCapitalization.sentences,
                minLines: 2,
                maxLines: 4,
                decoration: const InputDecoration(
                  labelText: 'Default terms',
                  hintText: 'Payment due within 30 days.',
                  border: OutlineInputBorder(),
                ),
              ),
              const SizedBox(height: 12),
              TextField(
                controller: _footer,
                textCapitalization: TextCapitalization.sentences,
                minLines: 1,
                maxLines: 3,
                decoration: const InputDecoration(
                  labelText: 'Footer text',
                  border: OutlineInputBorder(),
                ),
              ),
              const SizedBox(height: 4),
              Text(
                'New invoices start from these. Changing them here does not '
                'rewrite invoices already raised.',
                style: AppTypography.caption.copyWith(
                  color: AppColors.textSecondary,
                ),
              ),
            ]),
            if (!widget.isEdit)
              _card('Default template', [
                // Its own Material, because `_card` paints a background: a
                // ListTile draws its splash on the nearest Material ancestor,
                // so without this the switch has no touch feedback at all.
                // Flutter asserts on the arrangement, which is how the 390px
                // render test caught it.
                Material(
                  color: AppColors.surface,
                  child: SwitchListTile(
                    contentPadding: EdgeInsets.zero,
                    value: _isDefault,
                    onChanged: (v) => setState(() {
                      _isDefault = v;
                      _dirty = true;
                    }),
                    title: const Text('Use this for new invoices'),
                    subtitle: const Text(
                      'Only one template can be the default. Turning this on '
                      'replaces whichever holds it now.',
                    ),
                  ),
                ),
              ]),
            _card('Layout', [
              Text(
                'The PDF layout is HTML and CSS, and it is edited on the web. '
                'Saving here changes the fields above and leaves the layout '
                'as it is.',
                style: AppTypography.caption.copyWith(
                  color: AppColors.textSecondary,
                ),
              ),
            ]),
            if (_error != null)
              Padding(
                padding: const EdgeInsets.fromLTRB(16, 16, 16, 0),
                child: Text(
                  _error!,
                  style: AppTypography.caption.copyWith(
                    color: AppColors.danger600,
                  ),
                ),
              ),
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 20, 16, 0),
              child: SizedBox(
                height: 48,
                child: FilledButton(
                  onPressed: _saving ? null : _save,
                  child: Text(
                    _saving
                        ? 'Saving...'
                        : widget.isEdit
                        ? 'Save template'
                        : 'Create template',
                  ),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _shell(Widget body) {
    return Scaffold(
      backgroundColor: AppColors.surfaceDim,
      appBar: AppBar(
        title: Text(widget.isEdit ? 'Edit template' : 'New template'),
        backgroundColor: AppColors.surface,
        elevation: 0,
        scrolledUnderElevation: 1,
        leading: IconButton(
          icon: const Icon(LucideIcons.chevronLeft),
          tooltip: 'Back',
          onPressed: _saving
              ? null
              : () => leaveForm(context, hasUnsavedChanges: _dirty),
        ),
      ),
      body: body,
    );
  }

  Widget _logoRow() {
    final picked = _logo;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          picked != null
              ? picked.name
              : _hadLogo
              ? 'A logo is already set. Choosing one replaces it.'
              : 'No logo. Invoices print with the org name instead.',
          style: AppTypography.caption.copyWith(color: AppColors.textSecondary),
        ),
        const SizedBox(height: 10),
        SizedBox(
          height: 44,
          child: OutlinedButton.icon(
            onPressed: _saving ? null : _pickLogo,
            icon: const Icon(LucideIcons.image, size: 18),
            label: Text(
              picked != null || _hadLogo ? 'Replace logo' : 'Add logo',
            ),
          ),
        ),
      ],
    );
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
              fontWeight: FontWeight.w600,
              color: AppColors.textSecondary,
            ),
          ),
          const SizedBox(height: 12),
          ...children,
        ],
      ),
    );
  }
}

/// A hex colour, picked from swatches or typed.
///
/// Swatches first because this is a phone: typing `#3B82F6` on a soft keyboard
/// to change a brand colour is the kind of thing that gets a feature called
/// unusable. The field stays editable for an exact brand value the palette does
/// not carry, and both routes end at the same `#RRGGBB` string the API takes.
class _ColourField extends StatelessWidget {
  const _ColourField({
    required this.label,
    required this.controller,
    required this.onPicked,
    this.errorText,
  });

  final String label;
  final TextEditingController controller;
  final ValueChanged<String> onPicked;
  final String? errorText;

  /// The app palette, plus the neutrals an invoice actually tends to use.
  static const List<String> presets = [
    '#3B82F6',
    '#1E40AF',
    '#16A34A',
    '#F97316',
    '#DC2626',
    '#7C3AED',
    '#0F172A',
    '#4B5563',
  ];

  @override
  Widget build(BuildContext context) {
    // The whole field rebuilds on every keystroke, swatches included. With only
    // the input inside the builder, typing a hex by hand changed the preview
    // and left the matching swatch unringed until something else rebuilt.
    return ListenableBuilder(
      listenable: controller,
      builder: (context, _) {
        final typed = controller.text.trim().toUpperCase();
        return Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            TextField(
              controller: controller,
              autocorrect: false,
              decoration: InputDecoration(
                labelText: label,
                border: const OutlineInputBorder(),
                errorText: errorText,
                prefixIcon: Padding(
                  padding: const EdgeInsets.fromLTRB(12, 10, 8, 10),
                  child: Container(
                    width: 22,
                    decoration: BoxDecoration(
                      color: swatchColor(typed) ?? AppColors.gray200,
                      borderRadius: BorderRadius.circular(4),
                      border: Border.all(color: AppColors.gray300),
                    ),
                  ),
                ),
                prefixIconConstraints: const BoxConstraints(minWidth: 0),
              ),
            ),
            const SizedBox(height: 8),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                for (final hex in presets)
                  _Swatch(
                    hex: hex,
                    selected: typed == hex,
                    onTap: () => onPicked(hex),
                  ),
              ],
            ),
          ],
        );
      },
    );
  }
}

class _Swatch extends StatelessWidget {
  const _Swatch({
    required this.hex,
    required this.selected,
    required this.onTap,
  });

  final String hex;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Semantics(
      selected: selected,
      button: true,
      label: hex,
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(8),
        // 44 square, the minimum comfortable tap target, rather than sizing
        // the box to the swatch it draws.
        child: SizedBox(
          width: 44,
          height: 44,
          child: Center(
            child: Container(
              width: 30,
              height: 30,
              decoration: BoxDecoration(
                color: swatchColor(hex) ?? AppColors.gray200,
                borderRadius: BorderRadius.circular(6),
                border: Border.all(
                  color: selected ? AppColors.textPrimary : AppColors.gray300,
                  width: selected ? 2.5 : 1,
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

/// `#RRGGBB` to a colour, or null for anything else.
///
/// Null rather than a throw or a guessed colour: the value is org-authored free
/// text that reaches here mid-typing, so "not a colour yet" is the ordinary
/// case and not an error worth a crash.
Color? swatchColor(String? hex) {
  final value = (hex ?? '').replaceAll('#', '').trim();
  if (value.length != 6) return null;
  final parsed = int.tryParse(value, radix: 16);
  return parsed == null ? null : Color(0xFF000000 | parsed);
}
