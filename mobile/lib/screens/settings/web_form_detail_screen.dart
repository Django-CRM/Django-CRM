import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

import '../../core/theme/theme.dart';
import '../../data/models/custom_field_definition.dart';
import '../../data/models/web_form.dart';
import '../../providers/auth_provider.dart';
import '../../providers/lookup_provider.dart';
import '../../providers/settings_provider.dart';
import '../../providers/web_forms_provider.dart';
import '../../widgets/common/badge.dart';
import 'web_form_field_sheet.dart';

/// One web form: the editor, mirroring `/settings/web-forms/[id]` on the web.
///
/// FIVE SECTIONS, ONE SAVE
/// Fields, Behaviour, Spam, Embed and Activity. The first three save together,
/// because a per-section save would mean three requests, three failure states,
/// and an ordering question nobody asked.
///
/// REORDERING WITHOUT A DRAG GESTURE
/// `ReorderableListView` provides the drag; the up/down buttons beside it
/// provide the same move without one. Both go through [_move], so they cannot
/// disagree. The drag handle is an explicit `ReorderableDragStartListener`
/// rather than a long-press on the row: a child's own gesture recognizer wins
/// the arena against an enclosing drag, so a row that is itself the drag target
/// stops being draggable the moment anything inside it handles a press. Tapping
/// a row opens its edit sheet, which is exactly that situation.
///
/// PUBLISHING IS NOT A SWITCH
/// It has its own endpoint, which validates the source state and the form's
/// shape. `is_published` is read-only on the update serializer, so a switch
/// bound to it would look like it worked and do nothing.
///
/// THE CAPTCHA SECRET IS WRITE-ONLY
/// The API never returns it, so the box is always empty and empty means
/// "unchanged". Sending a blank would wipe a working secret on any unrelated
/// save, and Turnstile fails closed, so nothing would show until the next
/// visitor was refused.
class WebFormDetailScreen extends ConsumerStatefulWidget {
  const WebFormDetailScreen({super.key, required this.formId});

  final String formId;

  @override
  ConsumerState<WebFormDetailScreen> createState() =>
      _WebFormDetailScreenState();
}

class _WebFormDetailScreenState extends ConsumerState<WebFormDetailScreen> {
  /// The editable copy, seeded from the server once and owned here from then
  /// on. Null until the first load resolves.
  WebForm? _draft;
  String _loadedId = '';

  final _name = TextEditingController();
  final _submitLabel = TextEditingController();
  final _successMessage = TextEditingController();
  final _redirectUrl = TextEditingController();
  final _origins = TextEditingController();
  final _captchaSiteKey = TextEditingController();
  final _captchaSecret = TextEditingController();

  bool _saving = false;

  @override
  void dispose() {
    _name.dispose();
    _submitLabel.dispose();
    _successMessage.dispose();
    _redirectUrl.dispose();
    _origins.dispose();
    _captchaSiteKey.dispose();
    _captchaSecret.dispose();
    super.dispose();
  }

  /// Copy the server's form into the editable state.
  ///
  /// Guarded on the id so an ordinary refresh (after a publish, say) does not
  /// discard what somebody is halfway through typing.
  void _seed(WebForm form) {
    if (_loadedId == form.id) return;
    _loadedId = form.id;
    _draft = form;
    _name.text = form.name;
    _submitLabel.text = form.submitButtonLabel;
    _successMessage.text = form.successMessageText;
    _redirectUrl.text = form.redirectUrl;
    _origins.text = form.allowedOrigins.join('\n');
    _captchaSiteKey.text = form.captchaSiteKey;
    // Deliberately not seeded: there is nothing to seed it with, and an empty
    // box is what "leave the stored one alone" looks like.
    _captchaSecret.clear();
  }

  /// Move the row at [oldIndex] to [newIndex].
  ///
  /// `newIndex` is the position the row ends up at, not an insertion point:
  /// this is wired to `onReorderItem`, which already adjusts for the row being
  /// lifted out. The older `onReorder` did not, and the off-by-one that
  /// followed is why a downward drag used to land a row short of where it was
  /// dropped. The up/down buttons call straight into this with the index they
  /// mean, so both paths agree.
  void _move(int oldIndex, int newIndex) {
    final draft = _draft;
    if (draft == null) return;
    if (newIndex < 0 ||
        newIndex >= draft.fields.length ||
        newIndex == oldIndex) {
      return;
    }
    final fields = [...draft.fields];
    final moved = fields.removeAt(oldIndex);
    fields.insert(newIndex, moved);
    setState(() => _draft = draft.copyWith(fields: fields));
  }

  Future<void> _editField(
    WebFormField? existing,
    List<CustomFieldDefinition> customFields,
  ) async {
    final draft = _draft;
    if (draft == null) return;

    final inUse = {
      for (final field in draft.fields)
        if (!field.isCustom && field.leadField.isNotEmpty) field.leadField,
    };

    final result = await showWebFormFieldSheet(
      context,
      existing: existing,
      customFields: customFields,
      leadFieldsInUse: inUse,
    );
    if (result == null) return;

    final fields = [...draft.fields];
    final at = existing == null ? -1 : fields.indexOf(existing);
    if (at >= 0) {
      fields[at] = result;
    } else {
      fields.add(result);
    }
    setState(() => _draft = draft.copyWith(fields: fields));
  }

  void _removeField(WebFormField field) {
    final draft = _draft;
    if (draft == null) return;
    final fields = [...draft.fields]..remove(field);
    setState(() => _draft = draft.copyWith(fields: fields));
  }

  List<String> _readOrigins() {
    return _origins.text
        .split('\n')
        .map((line) => line.trim())
        .where((line) => line.isNotEmpty)
        .toList(growable: false);
  }

  Future<void> _save() async {
    final draft = _draft;
    if (draft == null) return;

    final payload = draft
        .copyWith(
          name: _name.text.trim(),
          submitButtonLabel: _submitLabel.text.trim(),
          successMessageText: _successMessage.text,
          redirectUrl: _redirectUrl.text.trim(),
          allowedOrigins: _readOrigins(),
          captchaSiteKey: _captchaSiteKey.text.trim(),
        )
        .toJson(captchaSecret: _captchaSecret.text);

    setState(() => _saving = true);
    final error = await ref
        .read(webFormsProvider.notifier)
        .updateWebForm(draft.id, payload);
    if (!mounted) return;
    setState(() => _saving = false);

    if (error == null) {
      // Re-read so the editor shows what was stored rather than what was sent.
      _loadedId = '';
      ref.invalidate(webFormDetailProvider(draft.id));
      _captchaSecret.clear();
    }
    if (!mounted) return;
    ScaffoldMessenger.of(
      context,
    ).showSnackBar(SnackBar(content: Text(error ?? 'Changes saved')));
  }

  Future<void> _togglePublished(WebForm form) async {
    final notifier = ref.read(webFormsProvider.notifier);

    if (form.isPublished) {
      final confirmed = await showDialog<bool>(
        context: context,
        builder: (context) => AlertDialog(
          title: const Text('Unpublish this form?'),
          content: const Text(
            'It stops accepting submissions immediately. The embed stays on '
            'your site and starts refusing people.',
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.of(context).pop(false),
              child: const Text('Keep it live'),
            ),
            TextButton(
              onPressed: () => Navigator.of(context).pop(true),
              style: TextButton.styleFrom(foregroundColor: AppColors.danger600),
              child: const Text('Unpublish it'),
            ),
          ],
        ),
      );
      if (confirmed != true) return;
      final error = await notifier.unpublish(form.id);
      if (!mounted) return;
      _afterStateChange(form.id, error, 'Form unpublished');
      return;
    }

    final error = await notifier.publish(form.id);
    if (!mounted) return;
    _afterStateChange(form.id, error, 'Form published');
  }

  void _afterStateChange(String id, String? error, String success) {
    if (error == null) {
      _loadedId = '';
      ref.invalidate(webFormDetailProvider(id));
    }
    ScaffoldMessenger.of(
      context,
    ).showSnackBar(SnackBar(content: Text(error ?? success)));
  }

  Future<void> _delete(WebForm form) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Delete this form?'),
        content: const Text(
          'The form and its submission history go for good. Leads it already '
          'created stay where they are. Any embed still on your site will '
          'stop working.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop(false),
            child: const Text('Keep it'),
          ),
          TextButton(
            onPressed: () => Navigator.of(context).pop(true),
            style: TextButton.styleFrom(foregroundColor: AppColors.danger600),
            child: const Text('Delete permanently'),
          ),
        ],
      ),
    );
    if (confirmed != true || !mounted) return;

    final error = await ref
        .read(webFormsProvider.notifier)
        .removeWebForm(form.id);
    if (!mounted) return;
    if (error == null) {
      context.pop();
      return;
    }
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(error)));
  }

  Future<void> _copy(String text, String what) async {
    await Clipboard.setData(ClipboardData(text: text));
    if (!mounted) return;
    ScaffoldMessenger.of(
      context,
    ).showSnackBar(SnackBar(content: Text('$what copied')));
  }

  @override
  Widget build(BuildContext context) {
    final async = ref.watch(webFormDetailProvider(widget.formId));
    final isAdmin = ref.watch(isOrgAdminProvider);
    final profiles = ref.watch(usersProvider);
    final tags = ref.watch(tagsProvider);
    final customFields =
        (ref.watch(customFieldsProvider).value?.fields ??
                const <CustomFieldDefinition>[])
            .where((d) => d.targetModel == 'Lead' && d.isActive)
            .toList(growable: false);

    return async.when(
      loading: () =>
          const Scaffold(body: Center(child: CircularProgressIndicator())),
      error: (error, _) => Scaffold(
        appBar: AppBar(title: const Text('Web form')),
        body: Center(
          child: Padding(
            padding: const EdgeInsets.all(32),
            child: Text(
              '$error'.replaceFirst('Exception: ', ''),
              style: AppTypography.body,
              textAlign: TextAlign.center,
            ),
          ),
        ),
      ),
      data: (detail) {
        _seed(detail.form);
        final draft = _draft!;
        final blocker = draft.publishBlocker;

        return Scaffold(
          backgroundColor: AppColors.surfaceDim,
          appBar: AppBar(
            title: Text(detail.form.name),
            backgroundColor: AppColors.surface,
            elevation: 0,
            scrolledUnderElevation: 1,
            actions: [
              if (isAdmin)
                IconButton(
                  icon: const Icon(LucideIcons.trash2),
                  tooltip: 'Delete form',
                  onPressed: () => _delete(detail.form),
                ),
            ],
          ),
          bottomNavigationBar: isAdmin
              ? SafeArea(
                  child: Padding(
                    padding: const EdgeInsets.fromLTRB(16, 8, 16, 8),
                    child: SizedBox(
                      height: 48,
                      child: FilledButton(
                        onPressed: _saving ? null : _save,
                        child: Text(_saving ? 'Saving…' : 'Save changes'),
                      ),
                    ),
                  ),
                )
              : null,
          body: ListView(
            padding: const EdgeInsets.only(bottom: 32),
            children: [
              _StatusCard(
                form: detail.form,
                isAdmin: isAdmin,
                blocker: blocker,
                onToggle: () => _togglePublished(detail.form),
              ),

              _SectionHeader(
                'Fields',
                subtitle:
                    'What a visitor is asked, in the order they are asked it. '
                    'An email field is required before the form can be '
                    'published.',
              ),
              _FieldList(
                fields: draft.fields,
                isAdmin: isAdmin,
                onReorder: _move,
                onEdit: (field) => _editField(field, customFields),
                onRemove: _removeField,
              ),
              if (isAdmin)
                Padding(
                  padding: const EdgeInsets.fromLTRB(16, 10, 16, 0),
                  child: SizedBox(
                    height: 44,
                    child: OutlinedButton.icon(
                      onPressed: () => _editField(null, customFields),
                      icon: const Icon(LucideIcons.plus, size: 16),
                      label: const Text('Add a field'),
                    ),
                  ),
                ),

              _SectionHeader(
                'Behaviour',
                subtitle:
                    'What the visitor sees after they submit, and where the '
                    'lead lands.',
              ),
              _Panel(
                children: [
                  _Text(
                    controller: _name,
                    label: 'Name',
                    helper: 'Internal only. The visitor never sees it',
                    enabled: isAdmin,
                  ),
                  _Text(
                    controller: _submitLabel,
                    label: 'Submit button',
                    enabled: isAdmin,
                    maxLength: 64,
                  ),
                  DropdownButtonFormField<String>(
                    initialValue: draft.successMode,
                    isExpanded: true,
                    decoration: const InputDecoration(
                      labelText: 'After a successful submission',
                      border: OutlineInputBorder(),
                    ),
                    items: const [
                      DropdownMenuItem(
                        value: WebForm.successMessage,
                        child: Text('Show a message'),
                      ),
                      DropdownMenuItem(
                        value: WebForm.successRedirect,
                        child: Text('Redirect to a URL'),
                      ),
                    ],
                    onChanged: isAdmin
                        ? (value) => setState(
                            () => _draft = draft.copyWith(successMode: value),
                          )
                        : null,
                  ),
                  if (draft.redirectsOnSuccess)
                    _Text(
                      controller: _redirectUrl,
                      label: 'Redirect URL',
                      helper:
                          'http or https only. The embed navigates the '
                          'visitor here',
                      enabled: isAdmin,
                      keyboardType: TextInputType.url,
                    )
                  else
                    _Text(
                      controller: _successMessage,
                      label: 'Success message',
                      enabled: isAdmin,
                      minLines: 2,
                      maxLines: 4,
                    ),
                  DropdownButtonFormField<String?>(
                    initialValue: draft.assignTo,
                    isExpanded: true,
                    decoration: const InputDecoration(
                      labelText: 'Assign new leads to',
                      border: OutlineInputBorder(),
                    ),
                    items: [
                      const DropdownMenuItem(
                        value: null,
                        child: Text('Nobody'),
                      ),
                      for (final profile in profiles)
                        DropdownMenuItem(
                          value: profile.id,
                          child: Text(profile.displayName),
                        ),
                    ],
                    onChanged: isAdmin
                        ? (value) => setState(
                            () => _draft = value == null
                                ? draft.copyWith(clearAssignTo: true)
                                : draft.copyWith(assignTo: value),
                          )
                        : null,
                  ),
                  _MultiPick(
                    label: 'Email these people on each lead',
                    empty: 'Nobody. No notification is sent',
                    options: [
                      for (final profile in profiles)
                        (id: profile.id, name: profile.displayName),
                    ],
                    selected: draft.notifyProfiles,
                    enabled: isAdmin,
                    onChanged: (ids) => setState(
                      () => _draft = draft.copyWith(notifyProfiles: ids),
                    ),
                  ),
                  _MultiPick(
                    label: 'Tag every lead with',
                    empty: 'No tags',
                    options: [
                      for (final tag in tags) (id: tag.id, name: tag.name),
                    ],
                    selected: draft.tags,
                    enabled: isAdmin,
                    onChanged: (ids) =>
                        setState(() => _draft = draft.copyWith(tags: ids)),
                  ),
                ],
              ),

              _SectionHeader(
                'Spam',
                subtitle:
                    'A hidden honeypot field, a per-address rate limit and a '
                    'per-form one are always on and are not configurable. '
                    'These are the parts you choose.',
              ),
              _Panel(
                children: [
                  _Text(
                    controller: _origins,
                    label: 'Allowed origins',
                    helper:
                        'One per line, scheme and host only. Leave empty and '
                        'the iframe embed works anywhere. The script embed '
                        'needs the site listed here',
                    enabled: isAdmin,
                    minLines: 2,
                    maxLines: 5,
                  ),
                  // Its own Material. `_Panel` paints the surface colour, and
                  // a ListTile draws its ink splash on the nearest Material
                  // ancestor, so without this the row is tappable and looks
                  // inert.
                  Material(
                    color: Colors.transparent,
                    child: SwitchListTile.adaptive(
                      value: draft.rejectDisposableEmail,
                      onChanged: isAdmin
                          ? (value) => setState(
                              () => _draft = draft.copyWith(
                                rejectDisposableEmail: value,
                              ),
                            )
                          : null,
                      contentPadding: EdgeInsets.zero,
                      title: const Text('Reject throwaway email addresses'),
                    ),
                  ),
                  DropdownButtonFormField<String>(
                    initialValue: draft.captchaProvider,
                    isExpanded: true,
                    decoration: const InputDecoration(
                      labelText: 'Challenge',
                      border: OutlineInputBorder(),
                    ),
                    items: const [
                      DropdownMenuItem(
                        value: WebForm.captchaNone,
                        child: Text('None'),
                      ),
                      DropdownMenuItem(
                        value: WebForm.captchaTurnstile,
                        child: Text('Cloudflare Turnstile'),
                      ),
                    ],
                    onChanged: isAdmin
                        ? (value) => setState(
                            () =>
                                _draft = draft.copyWith(captchaProvider: value),
                          )
                        : null,
                  ),
                  if (draft.usesTurnstile) ...[
                    _Text(
                      controller: _captchaSiteKey,
                      label: 'Turnstile site key',
                      enabled: isAdmin,
                    ),
                    _Text(
                      controller: _captchaSecret,
                      label: 'Turnstile secret',
                      hint: detail.form.hasCaptchaSecret
                          ? 'Stored. Leave blank to keep it'
                          : 'Paste the secret from Cloudflare',
                      helper: detail.form.hasCaptchaSecret
                          ? 'Never shown again once saved. Leaving this blank '
                                'keeps what is stored'
                          : 'No secret stored yet. Verification fails closed, '
                                'so publishing with Turnstile on and no secret '
                                'would refuse every submission',
                      enabled: isAdmin,
                      obscure: true,
                    ),
                  ],
                ],
              ),

              _SectionHeader(
                'Embed',
                subtitle:
                    'Paste one of these into your own site. Both are built by '
                    'the server, because they need the API address.',
              ),
              _Snippet(
                title: 'iframe',
                note: 'Works anywhere, no origin list needed.',
                code: detail.form.embedHtml,
                onCopy: () => _copy(detail.form.embedHtml, 'iframe snippet'),
              ),
              _Snippet(
                title: 'script',
                note: 'Inherits your site styling.',
                code: detail.form.embedJs,
                warning: detail.form.allowedOrigins.isEmpty
                    ? 'This one will not work yet. Add the site origin under '
                          'Spam first: the browser blocks a cross-origin POST '
                          'unless we permit that origin, and this form permits '
                          'none.'
                    : null,
                onCopy: () => _copy(detail.form.embedJs, 'script snippet'),
              ),

              _SectionHeader(
                'Activity',
                subtitle:
                    'The last 30 days. A view is counted when the embed '
                    'loads, whether or not anyone fills it in.',
              ),
              if (detail.analytics != null)
                _AnalyticsRow(analytics: detail.analytics!),
              _Submissions(
                submissions: detail.submissions,
                total: detail.submissionCount,
              ),
            ],
          ),
        );
      },
    );
  }
}

class _StatusCard extends StatelessWidget {
  const _StatusCard({
    required this.form,
    required this.isAdmin,
    required this.blocker,
    required this.onToggle,
  });

  final WebForm form;
  final bool isAdmin;
  final String? blocker;
  final VoidCallback onToggle;

  @override
  Widget build(BuildContext context) {
    return Container(
      color: AppColors.surface,
      padding: const EdgeInsets.fromLTRB(16, 14, 16, 14),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              StatusBadge(
                label: form.isPublished ? 'Published' : 'Draft',
                color: form.isPublished
                    ? AppColors.success600
                    : AppColors.gray500,
              ),
              const SizedBox(width: 10),
              Expanded(
                child: Text(
                  form.isPublished
                      ? 'Accepting submissions from anyone with the embed.'
                      : 'Collecting nothing until it is published.',
                  style: AppTypography.caption.copyWith(
                    color: AppColors.textSecondary,
                  ),
                ),
              ),
            ],
          ),
          if (isAdmin) ...[
            const SizedBox(height: 12),
            SizedBox(
              height: 44,
              child: OutlinedButton(
                // Disabled with the reason printed below rather than hidden:
                // "why can I not publish this" is the question the screen
                // exists to answer.
                onPressed: form.isPublished || blocker == null
                    ? onToggle
                    : null,
                style: form.isPublished
                    ? OutlinedButton.styleFrom(
                        foregroundColor: AppColors.danger600,
                      )
                    : null,
                child: Text(form.isPublished ? 'Unpublish' : 'Publish'),
              ),
            ),
            if (!form.isPublished && blocker != null) ...[
              const SizedBox(height: 8),
              Text(
                blocker!,
                style: AppTypography.caption.copyWith(
                  color: AppColors.warning600,
                ),
              ),
            ],
          ],
        ],
      ),
    );
  }
}

class _FieldList extends StatelessWidget {
  const _FieldList({
    required this.fields,
    required this.isAdmin,
    required this.onReorder,
    required this.onEdit,
    required this.onRemove,
  });

  final List<WebFormField> fields;
  final bool isAdmin;
  final void Function(int, int) onReorder;
  final void Function(WebFormField) onEdit;
  final void Function(WebFormField) onRemove;

  @override
  Widget build(BuildContext context) {
    if (fields.isEmpty) {
      return Container(
        color: AppColors.surface,
        padding: const EdgeInsets.all(16),
        child: Text(
          'No fields yet. A form with no fields collects nothing.',
          style: AppTypography.caption.copyWith(color: AppColors.textSecondary),
        ),
      );
    }

    return ReorderableListView.builder(
      shrinkWrap: true,
      // The enclosing ListView already scrolls. Two scrollables sharing one
      // gesture is the other way a drag here stops working.
      physics: const NeverScrollableScrollPhysics(),
      // No default handles: the whole row would become the drag target, and
      // the row's own tap-to-edit would then win the gesture arena and kill
      // the drag. The explicit listener below is the handle instead.
      buildDefaultDragHandles: false,
      itemCount: fields.length,
      // `onReorderItem`, not `onReorder`. It hands over the index the row ends
      // up at rather than an insertion point, so the buttons and the drag can
      // pass the same numbers to the same function.
      onReorderItem: onReorder,
      itemBuilder: (context, index) {
        final field = fields[index];
        return _FieldRow(
          key: ValueKey('${field.id ?? ''}-${field.label}-$index'),
          field: field,
          index: index,
          total: fields.length,
          isAdmin: isAdmin,
          onEdit: () => onEdit(field),
          onRemove: () => onRemove(field),
          onMoveUp: index == 0 ? null : () => onReorder(index, index - 1),
          onMoveDown: index == fields.length - 1
              ? null
              : () => onReorder(index, index + 1),
        );
      },
    );
  }
}

class _FieldRow extends StatelessWidget {
  const _FieldRow({
    super.key,
    required this.field,
    required this.index,
    required this.total,
    required this.isAdmin,
    required this.onEdit,
    required this.onRemove,
    required this.onMoveUp,
    required this.onMoveDown,
  });

  final WebFormField field;
  final int index;
  final int total;
  final bool isAdmin;
  final VoidCallback onEdit;
  final VoidCallback onRemove;
  final VoidCallback? onMoveUp;
  final VoidCallback? onMoveDown;

  @override
  Widget build(BuildContext context) {
    final target = field.isCustom
        ? 'Custom field'
        : leadFieldLabel(field.leadField);

    return Container(
      color: AppColors.surface,
      margin: const EdgeInsets.only(bottom: 1),
      padding: const EdgeInsets.fromLTRB(8, 8, 8, 8),
      child: Row(
        children: [
          if (isAdmin)
            // An explicit handle, not a long-press on the row. A child's own
            // recognizer wins the gesture arena against an enclosing drag, so
            // the row's tap-to-edit would silently kill a row-level drag.
            ReorderableDragStartListener(
              index: index,
              child: Container(
                width: 44,
                height: 44,
                alignment: Alignment.center,
                child: Icon(
                  LucideIcons.gripVertical,
                  size: 18,
                  color: AppColors.textTertiary,
                ),
              ),
            ),
          Expanded(
            child: InkWell(
              onTap: isAdmin ? onEdit : null,
              child: Padding(
                padding: const EdgeInsets.symmetric(vertical: 6, horizontal: 4),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      field.displayLabel,
                      style: AppTypography.body.copyWith(
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                    const SizedBox(height: 2),
                    Text(
                      field.isRequired ? '$target · required' : target,
                      style: AppTypography.caption.copyWith(
                        color: AppColors.textTertiary,
                      ),
                    ),
                    if (!field.isComplete) ...[
                      const SizedBox(height: 2),
                      Text(
                        'Incomplete. It needs a label and a target.',
                        style: AppTypography.caption.copyWith(
                          color: AppColors.warning600,
                        ),
                      ),
                    ],
                  ],
                ),
              ),
            ),
          ),
          if (isAdmin) ...[
            // The same move the drag makes, without needing a drag to land.
            // Both call `onReorder`, so there is one implementation.
            _IconAction(
              icon: LucideIcons.chevronUp,
              tooltip: 'Move ${field.displayLabel} up',
              onPressed: onMoveUp,
            ),
            _IconAction(
              icon: LucideIcons.chevronDown,
              tooltip: 'Move ${field.displayLabel} down',
              onPressed: onMoveDown,
            ),
            _IconAction(
              icon: LucideIcons.trash2,
              tooltip: 'Remove ${field.displayLabel}',
              onPressed: onRemove,
            ),
          ],
        ],
      ),
    );
  }
}

/// 44 logical pixels square, because the pointer here is a fingertip and an
/// icon-only button has nothing to pad around.
class _IconAction extends StatelessWidget {
  const _IconAction({
    required this.icon,
    required this.tooltip,
    required this.onPressed,
  });

  final IconData icon;
  final String tooltip;
  final VoidCallback? onPressed;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: 44,
      height: 44,
      child: IconButton(
        icon: Icon(icon, size: 18),
        tooltip: tooltip,
        padding: EdgeInsets.zero,
        color: AppColors.textSecondary,
        disabledColor: AppColors.gray300,
        onPressed: onPressed,
      ),
    );
  }
}

class _SectionHeader extends StatelessWidget {
  const _SectionHeader(this.title, {this.subtitle});

  final String title;
  final String? subtitle;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 24, 16, 8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            title.toUpperCase(),
            style: AppTypography.overline.copyWith(
              color: AppColors.textSecondary,
              letterSpacing: 1.2,
            ),
          ),
          if (subtitle != null) ...[
            const SizedBox(height: 4),
            Text(
              subtitle!,
              style: AppTypography.caption.copyWith(
                color: AppColors.textTertiary,
              ),
            ),
          ],
        ],
      ),
    );
  }
}

class _Panel extends StatelessWidget {
  const _Panel({required this.children});

  final List<Widget> children;

  @override
  Widget build(BuildContext context) {
    return Container(
      color: AppColors.surface,
      padding: const EdgeInsets.fromLTRB(16, 14, 16, 14),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          for (var i = 0; i < children.length; i++) ...[
            if (i > 0) const SizedBox(height: 14),
            children[i],
          ],
        ],
      ),
    );
  }
}

class _Text extends StatelessWidget {
  const _Text({
    required this.controller,
    required this.label,
    this.helper,
    this.hint,
    this.enabled = true,
    this.minLines,
    this.maxLines = 1,
    this.maxLength,
    this.obscure = false,
    this.keyboardType,
  });

  final TextEditingController controller;
  final String label;
  final String? helper;
  final String? hint;
  final bool enabled;
  final int? minLines;
  final int maxLines;
  final int? maxLength;
  final bool obscure;
  final TextInputType? keyboardType;

  @override
  Widget build(BuildContext context) {
    return TextField(
      controller: controller,
      enabled: enabled,
      obscureText: obscure,
      minLines: minLines,
      maxLines: obscure ? 1 : maxLines,
      maxLength: maxLength,
      keyboardType: keyboardType,
      decoration: InputDecoration(
        labelText: label,
        helperText: helper,
        helperMaxLines: 4,
        hintText: hint,
        border: const OutlineInputBorder(),
        counterText: '',
      ),
    );
  }
}

/// A multi-select rendered as chips.
///
/// A Material multi-select dropdown does not exist, and a dialog for two or
/// three names is more taps than the choice is worth.
class _MultiPick extends StatelessWidget {
  const _MultiPick({
    required this.label,
    required this.empty,
    required this.options,
    required this.selected,
    required this.enabled,
    required this.onChanged,
  });

  final String label;
  final String empty;
  final List<({String id, String name})> options;
  final List<String> selected;
  final bool enabled;
  final void Function(List<String>) onChanged;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          label,
          style: AppTypography.caption.copyWith(color: AppColors.textSecondary),
        ),
        const SizedBox(height: 6),
        if (options.isEmpty)
          Text(
            empty,
            style: AppTypography.caption.copyWith(
              color: AppColors.textTertiary,
            ),
          )
        else
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              for (final option in options)
                FilterChip(
                  label: Text(option.name),
                  selected: selected.contains(option.id),
                  onSelected: enabled
                      ? (isSelected) {
                          final next = [...selected];
                          if (isSelected) {
                            next.add(option.id);
                          } else {
                            next.remove(option.id);
                          }
                          onChanged(next);
                        }
                      : null,
                ),
            ],
          ),
      ],
    );
  }
}

class _Snippet extends StatelessWidget {
  const _Snippet({
    required this.title,
    required this.note,
    required this.code,
    required this.onCopy,
    this.warning,
  });

  final String title;
  final String note;
  final String code;
  final String? warning;
  final VoidCallback onCopy;

  @override
  Widget build(BuildContext context) {
    return Container(
      color: AppColors.surface,
      margin: const EdgeInsets.only(bottom: 1),
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      title,
                      style: AppTypography.body.copyWith(
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                    Text(
                      note,
                      style: AppTypography.caption.copyWith(
                        color: AppColors.textTertiary,
                      ),
                    ),
                  ],
                ),
              ),
              SizedBox(
                height: 44,
                child: OutlinedButton.icon(
                  onPressed: onCopy,
                  icon: const Icon(LucideIcons.copy, size: 15),
                  label: const Text('Copy'),
                ),
              ),
            ],
          ),
          const SizedBox(height: 10),
          // Scrolls inside its own box. Without this a long absolute URL
          // widens the whole screen and every section inherits a sideways
          // swipe.
          Container(
            width: double.infinity,
            decoration: BoxDecoration(
              color: AppColors.gray100,
              borderRadius: BorderRadius.circular(8),
              border: Border.all(color: AppColors.gray200),
            ),
            padding: const EdgeInsets.all(10),
            child: SingleChildScrollView(
              scrollDirection: Axis.horizontal,
              child: Text(
                code,
                style: AppTypography.caption.copyWith(
                  fontFamily: 'monospace',
                  color: AppColors.textSecondary,
                ),
              ),
            ),
          ),
          if (warning != null) ...[
            const SizedBox(height: 8),
            Text(
              warning!,
              style: AppTypography.caption.copyWith(
                color: AppColors.warning600,
              ),
            ),
          ],
        ],
      ),
    );
  }
}

class _AnalyticsRow extends StatelessWidget {
  const _AnalyticsRow({required this.analytics});

  final WebFormAnalytics analytics;

  @override
  Widget build(BuildContext context) {
    return Container(
      color: AppColors.surface,
      padding: const EdgeInsets.fromLTRB(16, 14, 16, 14),
      margin: const EdgeInsets.only(bottom: 1),
      child: Wrap(
        spacing: 20,
        runSpacing: 10,
        children: [
          _Stat(value: '${analytics.views}', label: 'views'),
          _Stat(value: '${analytics.submissions}', label: 'leads'),
          _Stat(
            // Guarded rather than computed blind: a brand new form has zero
            // views, and this is the first thing its screen renders.
            value: analytics.views == 0
                ? '-'
                : '${(analytics.conversionRate * 100).round()}%',
            label: 'conversion',
          ),
          _Stat(value: '${analytics.spam}', label: 'spam blocked'),
        ],
      ),
    );
  }
}

class _Stat extends StatelessWidget {
  const _Stat({required this.value, required this.label});

  final String value;
  final String label;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: [
        Text(
          value,
          style: AppTypography.h2.copyWith(fontWeight: FontWeight.w600),
        ),
        Text(
          label,
          style: AppTypography.caption.copyWith(color: AppColors.textSecondary),
        ),
      ],
    );
  }
}

class _Submissions extends StatelessWidget {
  const _Submissions({required this.submissions, required this.total});

  final List<WebFormSubmission> submissions;
  final int total;

  @override
  Widget build(BuildContext context) {
    if (submissions.isEmpty) {
      return Container(
        color: AppColors.surface,
        padding: const EdgeInsets.all(16),
        child: Text(
          'Nothing submitted yet. Rejected attempts would be listed here too, '
          'so an empty list means nobody has reached the form at all.',
          style: AppTypography.caption.copyWith(color: AppColors.textSecondary),
        ),
      );
    }

    return Column(
      children: [
        for (final submission in submissions)
          Container(
            color: AppColors.surface,
            margin: const EdgeInsets.only(bottom: 1),
            padding: const EdgeInsets.fromLTRB(16, 12, 16, 12),
            child: Row(
              children: [
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        submission.leadName ?? submission.statusLabel,
                        style: AppTypography.body.copyWith(
                          fontWeight: FontWeight.w600,
                        ),
                      ),
                      const SizedBox(height: 2),
                      Text(
                        submission.referer.isEmpty
                            ? submission.statusLabel
                            : '${submission.statusLabel} · '
                                  '${submission.referer}',
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: AppTypography.caption.copyWith(
                          color: AppColors.textTertiary,
                        ),
                      ),
                    ],
                  ),
                ),
                StatusBadge(
                  label: submission.isAccepted ? 'Lead' : 'Refused',
                  color: submission.isAccepted
                      ? AppColors.success600
                      : AppColors.gray500,
                ),
              ],
            ),
          ),
        if (total > submissions.length)
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 12, 16, 0),
            child: Align(
              alignment: Alignment.centerLeft,
              child: Text(
                'Showing the ${submissions.length} most recent of $total.',
                style: AppTypography.caption.copyWith(
                  color: AppColors.textTertiary,
                ),
              ),
            ),
          ),
      ],
    );
  }
}
