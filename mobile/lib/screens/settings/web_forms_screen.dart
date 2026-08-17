import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

import '../../core/theme/theme.dart';
import '../../data/models/web_form.dart';
import '../../providers/auth_provider.dart';
import '../../providers/web_forms_provider.dart';
import '../../routes/app_router.dart';
import '../../widgets/common/badge.dart';

/// Embeddable web forms, mirroring `/settings/web-forms` on the web.
///
/// **Read is open to every member; every write is admin-only.** The two halves
/// are not the same risk: a published form is an endpoint anyone on the
/// internet can post to, and every accepted post writes a lead into this org,
/// so creating one is closer to minting a credential than to editing a record.
/// Knowing which forms are live is ordinary operational knowledge, so a member
/// sees the list rather than a gate card.
///
/// The controls are hidden from a member as a courtesy, not as the boundary.
/// `is_org_admin(request.profile)` in `webforms/views.py` is the boundary, and
/// the provider returns its 403 message rather than swallowing it.
class WebFormsScreen extends ConsumerWidget {
  const WebFormsScreen({super.key});

  Future<void> _create(BuildContext context, WidgetRef ref) async {
    final name = await showDialog<String>(
      context: context,
      builder: (context) => const _NameDialog(),
    );
    if (name == null || name.trim().isEmpty || !context.mounted) return;

    try {
      final id = await ref
          .read(webFormsProvider.notifier)
          .createWebForm(name.trim());
      if (!context.mounted) return;
      // Straight into the editor. A form with no fields collects nothing, so
      // the list is never where anyone wants to land after creating one.
      if (id.isNotEmpty) context.push(AppRoutes.settingsWebForm(id));
    } catch (error) {
      if (!context.mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('$error'.replaceFirst('Exception: ', ''))),
      );
    }
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final async = ref.watch(webFormsProvider);
    final isAdmin = ref.watch(isOrgAdminProvider);

    return Scaffold(
      backgroundColor: AppColors.surfaceDim,
      appBar: AppBar(
        title: const Text('Web forms'),
        backgroundColor: AppColors.surface,
        elevation: 0,
        scrolledUnderElevation: 1,
      ),
      floatingActionButton: isAdmin
          ? FloatingActionButton(
              onPressed: () => _create(context, ref),
              tooltip: 'New web form',
              child: const Icon(LucideIcons.plus),
            )
          : null,
      body: async.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (_, _) => _ErrorState(
          onRetry: () => ref.read(webFormsProvider.notifier).refresh(),
        ),
        data: (state) {
          if (state.forms.isEmpty) {
            return _EmptyState(isAdmin: isAdmin);
          }
          return RefreshIndicator(
            onRefresh: () => ref.read(webFormsProvider.notifier).refresh(),
            child: ListView(
              padding: const EdgeInsets.only(bottom: 96),
              children: [
                _Summary(totals: state.totals),
                for (final form in state.forms)
                  _FormRow(
                    form: form,
                    onOpen: () =>
                        context.push(AppRoutes.settingsWebForm(form.id)),
                  ),
                if (state.truncated)
                  Padding(
                    padding: const EdgeInsets.fromLTRB(16, 14, 16, 0),
                    child: Text(
                      'Showing the ${state.forms.length} most recent of '
                      '${state.totals.count}.',
                      style: AppTypography.caption.copyWith(
                        color: AppColors.textTertiary,
                      ),
                    ),
                  ),
                const _SecurityNote(),
              ],
            ),
          );
        },
      ),
    );
  }
}

class _NameDialog extends StatefulWidget {
  const _NameDialog();

  @override
  State<_NameDialog> createState() => _NameDialogState();
}

class _NameDialogState extends State<_NameDialog> {
  final _controller = TextEditingController();

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: const Text('New web form'),
      content: TextField(
        controller: _controller,
        autofocus: true,
        textCapitalization: TextCapitalization.sentences,
        maxLength: 255,
        decoration: const InputDecoration(
          labelText: 'Name',
          helperText: 'Internal only. The visitor never sees it',
          border: OutlineInputBorder(),
          counterText: '',
        ),
        onSubmitted: (value) => Navigator.of(context).pop(value),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(context).pop(),
          child: const Text('Cancel'),
        ),
        TextButton(
          onPressed: () => Navigator.of(context).pop(_controller.text),
          child: const Text('Create and add fields'),
        ),
      ],
    );
  }
}

/// The org-wide figures, read from the server's `totals` rather than counted
/// off the page. The list is paginated, so counting rows would be right until
/// the eleventh form and quietly wrong afterwards.
class _Summary extends StatelessWidget {
  const _Summary({required this.totals});

  final WebFormTotals totals;

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
          _Stat(value: '${totals.published}', label: 'published'),
          if (totals.drafts > 0)
            _Stat(value: '${totals.drafts}', label: 'drafts'),
          _Stat(value: '${totals.submissions30d}', label: 'leads, 30 days'),
          if (totals.spam30d > 0)
            _Stat(value: '${totals.spam30d}', label: 'spam blocked'),
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

class _FormRow extends StatelessWidget {
  const _FormRow({required this.form, required this.onOpen});

  final WebForm form;
  final VoidCallback onOpen;

  @override
  Widget build(BuildContext context) {
    // Published, embedded, and silent for the whole window. The usual cause is
    // the snippet having been taken off the page it was pasted onto, which
    // nothing else here would ever tell you.
    final quiet = form.isPublished && form.submissionCount == 0;

    return InkWell(
      onTap: onOpen,
      child: Container(
        color: AppColors.surface,
        margin: const EdgeInsets.only(bottom: 1),
        padding: const EdgeInsets.fromLTRB(16, 12, 16, 12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              form.name,
              style: AppTypography.body.copyWith(fontWeight: FontWeight.w600),
            ),
            const SizedBox(height: 8),
            Wrap(
              spacing: 8,
              runSpacing: 6,
              crossAxisAlignment: WrapCrossAlignment.center,
              children: [
                StatusBadge(
                  label: form.isPublished ? 'Published' : 'Draft',
                  color: form.isPublished
                      ? AppColors.success600
                      : AppColors.gray500,
                ),
                Text(
                  '${form.fieldCount} field'
                  '${form.fieldCount == 1 ? '' : 's'}',
                  style: AppTypography.caption.copyWith(
                    color: AppColors.textTertiary,
                  ),
                ),
                Text(
                  form.submissionCount == 1
                      ? '1 submission'
                      : '${form.submissionCount} submissions',
                  style: AppTypography.caption.copyWith(
                    color: AppColors.textTertiary,
                  ),
                ),
              ],
            ),
            if (quiet) ...[
              const SizedBox(height: 6),
              Text(
                'Live but silent. Nothing has been submitted.',
                style: AppTypography.caption.copyWith(
                  color: AppColors.warning600,
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

class _SecurityNote extends StatelessWidget {
  const _SecurityNote();

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 20, 16, 0),
      child: Container(
        padding: const EdgeInsets.all(12),
        decoration: BoxDecoration(
          color: AppColors.warning50,
          borderRadius: BorderRadius.circular(8),
          border: Border.all(color: AppColors.warning200),
        ),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Icon(
              LucideIcons.shieldAlert,
              size: 18,
              color: AppColors.warning600,
            ),
            const SizedBox(width: 10),
            Expanded(
              child: Text(
                'A published form accepts posts from anyone. It has to: the '
                'whole point is that a stranger can fill it in without an '
                'account. A hidden honeypot field, per-address and per-form '
                'rate limits, and disposable-address rejection are always on. '
                'Unpublish a form the moment you take its snippet off your '
                'site.',
                style: AppTypography.caption.copyWith(
                  color: AppColors.textPrimary,
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _EmptyState extends StatelessWidget {
  const _EmptyState({required this.isAdmin});

  final bool isAdmin;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(
              LucideIcons.clipboardList,
              size: 40,
              color: AppColors.textTertiary,
            ),
            const SizedBox(height: 16),
            Text(
              'No web forms yet',
              style: AppTypography.h3,
              textAlign: TextAlign.center,
            ),
            const SizedBox(height: 8),
            Text(
              isAdmin
                  ? 'A web form is a page you embed on your own site. What '
                        'people fill in becomes a lead here, with no login and '
                        'no copy-pasting.'
                  : 'Nobody has built a web form for this organization yet. '
                        'An administrator can create one.',
              style: AppTypography.body.copyWith(
                color: AppColors.textSecondary,
              ),
              textAlign: TextAlign.center,
            ),
          ],
        ),
      ),
    );
  }
}

class _ErrorState extends StatelessWidget {
  const _ErrorState({required this.onRetry});

  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(
              LucideIcons.triangleAlert,
              size: 40,
              color: AppColors.textTertiary,
            ),
            const SizedBox(height: 16),
            Text(
              'Could not load the web forms',
              style: AppTypography.body,
              textAlign: TextAlign.center,
            ),
            const SizedBox(height: 16),
            OutlinedButton(onPressed: onRetry, child: const Text('Try again')),
          ],
        ),
      ),
    );
  }
}
