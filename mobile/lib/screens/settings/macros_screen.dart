import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

import '../../core/theme/theme.dart';
import '../../data/models/macro.dart';
import '../../providers/auth_provider.dart';
import '../../providers/settings_provider.dart';
import '../../widgets/common/badge.dart';
import 'macro_form_sheet.dart';

/// Saved replies, the canned answers offered in a ticket's reply box.
///
/// **The one settings page every member can write to.** Anyone may keep their
/// own personal replies; only an admin may write one the whole organization
/// shares. Both rules are enforced by `_resolve_scope_and_owner` and
/// `MacroDetailView._get_writable`, and this screen mirrors them so nobody is
/// offered an action that answers 403.
///
/// Personal rows belonging to other people never arrive: `_visible_qs` filters
/// them out. The edit control still asks about ownership rather than assuming
/// it, because that is the rule the server checks.
class MacrosScreen extends ConsumerWidget {
  const MacrosScreen({super.key});

  Future<void> _edit(
    BuildContext context,
    WidgetRef ref,
    Macro? existing,
  ) async {
    final state = ref.read(macrosProvider).value;
    final payload = await showMacroFormSheet(
      context,
      existing: existing,
      canCreateOrg: ref.read(isOrgAdminProvider),
      placeholders: state?.placeholders ?? const [],
    );
    if (payload == null || !context.mounted) return;
    final notifier = ref.read(macrosProvider.notifier);
    final error = existing == null
        ? await notifier.createMacro(payload)
        : await notifier.updateMacro(existing.id, payload);
    if (!context.mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(
          error ?? (existing == null ? 'Reply saved' : 'Changes saved'),
        ),
      ),
    );
  }

  Future<void> _remove(BuildContext context, WidgetRef ref, Macro macro) async {
    // The dialog's words come from the row's scope, because the outcome does:
    // an org macro is turned off and a personal one is gone for good.
    final removal = macroRemoval(macro);
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(removal.title),
        content: Text(removal.detail),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop(false),
            child: const Text('Keep it'),
          ),
          TextButton(
            onPressed: () => Navigator.of(context).pop(true),
            style: TextButton.styleFrom(foregroundColor: AppColors.danger600),
            child: Text(removal.actionLabel),
          ),
        ],
      ),
    );
    if (confirmed != true || !context.mounted) return;
    final error = await ref.read(macrosProvider.notifier).removeMacro(macro.id);
    if (!context.mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(
          error ?? (removal.isPermanent ? 'Reply deleted' : 'Reply turned off'),
        ),
      ),
    );
  }

  Future<void> _turnOn(BuildContext context, WidgetRef ref, Macro macro) async {
    final error = await ref
        .read(macrosProvider.notifier)
        .activateMacro(macro.id);
    if (!context.mounted) return;
    ScaffoldMessenger.of(
      context,
    ).showSnackBar(SnackBar(content: Text(error ?? 'Reply turned on')));
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final async = ref.watch(macrosProvider);
    final isAdmin = ref.watch(isOrgAdminProvider);
    final myEmail = ref.watch(myEmailProvider);

    return Scaffold(
      backgroundColor: AppColors.surfaceDim,
      appBar: AppBar(
        title: const Text('Saved replies'),
        backgroundColor: AppColors.surface,
        elevation: 0,
        scrolledUnderElevation: 1,
        actions: [
          // No admin gate: every member may keep their own.
          IconButton(
            icon: const Icon(LucideIcons.plus),
            tooltip: 'New saved reply',
            onPressed: () => _edit(context, ref, null),
          ),
        ],
      ),
      body: async.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (_, _) => _ErrorState(
          onRetry: () => ref.read(macrosProvider.notifier).refresh(),
        ),
        data: (state) {
          if (state.macros.isEmpty) {
            return const _EmptyState();
          }
          return RefreshIndicator(
            onRefresh: () => ref.read(macrosProvider.notifier).refresh(),
            child: ListView(
              padding: const EdgeInsets.only(bottom: 96),
              children: [
                _Summary(state: state),
                for (final macro in state.macros)
                  _MacroRow(
                    macro: macro,
                    canWrite: canWriteMacro(
                      isAdmin: isAdmin,
                      scope: macro.scope,
                      ownerEmail: macro.ownerEmail,
                      myEmail: myEmail,
                    ),
                    onEdit: () => _edit(context, ref, macro),
                    onRemove: () => _remove(context, ref, macro),
                    onTurnOn: () => _turnOn(context, ref, macro),
                  ),
              ],
            ),
          );
        },
      ),
    );
  }
}

class _Summary extends StatelessWidget {
  const _Summary({required this.state});

  final MacrosState state;

  @override
  Widget build(BuildContext context) {
    return Container(
      color: AppColors.surface,
      padding: const EdgeInsets.fromLTRB(16, 14, 16, 14),
      margin: const EdgeInsets.only(bottom: 1),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Wrap(
            spacing: 20,
            runSpacing: 10,
            children: [
              _Stat(value: '${state.orgCount}', label: 'shared'),
              _Stat(value: '${state.personalCount}', label: 'just yours'),
              if (state.inactiveCount > 0)
                _Stat(value: '${state.inactiveCount}', label: 'turned off'),
            ],
          ),
          if (state.brokenCount > 0) ...[
            const SizedBox(height: 14),
            Container(
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
                    LucideIcons.triangleAlert,
                    size: 18,
                    color: AppColors.warning600,
                  ),
                  const SizedBox(width: 10),
                  Expanded(
                    child: Text(
                      state.brokenCount == 1
                          ? 'One reply uses a placeholder that is not '
                                'recognized. It goes out to the customer '
                                'exactly as written.'
                          : '${state.brokenCount} replies use a placeholder '
                                'that is not recognized. Those go out to the '
                                'customer exactly as written.',
                      style: AppTypography.caption.copyWith(
                        color: AppColors.textPrimary,
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ],
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

class _MacroRow extends StatelessWidget {
  const _MacroRow({
    required this.macro,
    required this.canWrite,
    required this.onEdit,
    required this.onRemove,
    required this.onTurnOn,
  });

  final Macro macro;
  final bool canWrite;
  final VoidCallback onEdit;
  final VoidCallback onRemove;
  final VoidCallback onTurnOn;

  @override
  Widget build(BuildContext context) {
    final broken = macro.unknownPlaceholders;
    return Container(
      color: AppColors.surface,
      margin: const EdgeInsets.only(bottom: 1),
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            macro.title,
            style: AppTypography.body.copyWith(
              fontWeight: FontWeight.w600,
              color: macro.isActive
                  ? AppColors.textPrimary
                  : AppColors.textSecondary,
            ),
          ),
          if (macro.hasBody) ...[
            const SizedBox(height: 4),
            Text(
              macro.body,
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
              style: AppTypography.caption.copyWith(
                color: AppColors.textSecondary,
              ),
            ),
          ],
          if (macroActionChips(macro).isNotEmpty) ...[
            const SizedBox(height: 4),
            Text(
              'On send: '
              '${macroActionChips(macro).map((c) => c.label).join(' · ')}',
              style: AppTypography.caption.copyWith(
                color: AppColors.textSecondary,
                fontWeight: FontWeight.w500,
              ),
            ),
          ],
          const SizedBox(height: 8),
          Wrap(
            spacing: 8,
            runSpacing: 6,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              StatusBadge(
                label: macro.scopeLabel,
                color: macro.isPersonal
                    ? AppColors.gray500
                    : AppColors.primary600,
              ),
              if (!macro.isActive)
                StatusBadge(label: 'Turned off', color: AppColors.gray500),
              Text(
                macro.usageCount == 0
                    ? 'not used yet'
                    : 'used ${macro.usageCount} time'
                          '${macro.usageCount == 1 ? '' : 's'}',
                style: AppTypography.caption.copyWith(
                  color: AppColors.textTertiary,
                ),
              ),
            ],
          ),
          if (broken.isNotEmpty) ...[
            const SizedBox(height: 8),
            Text(
              // Named, not counted: the author needs to know which token to
              // fix, and a typo like %custmer_name% is only obvious beside the
              // right spelling.
              broken.length == 1
                  ? '${broken.single} is not recognized and goes out as written'
                  : '${broken.join(', ')} are not recognized and go out as '
                        'written',
              style: AppTypography.caption.copyWith(
                color: AppColors.warning600,
              ),
            ),
          ],
          if (canWrite) ...[
            const SizedBox(height: 10),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                SizedBox(
                  height: 40,
                  child: OutlinedButton(
                    onPressed: onEdit,
                    child: const Text('Edit'),
                  ),
                ),
                SizedBox(
                  height: 40,
                  child: macro.isActive
                      ? OutlinedButton(
                          onPressed: onRemove,
                          style: OutlinedButton.styleFrom(
                            foregroundColor: AppColors.danger600,
                          ),
                          child: Text(macroRemoval(macro).actionLabel),
                        )
                      : OutlinedButton(
                          onPressed: onTurnOn,
                          child: const Text('Turn on'),
                        ),
                ),
              ],
            ),
          ],
        ],
      ),
    );
  }
}

class _EmptyState extends StatelessWidget {
  const _EmptyState();

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(
              LucideIcons.messageSquareQuote,
              size: 40,
              color: AppColors.textTertiary,
            ),
            const SizedBox(height: 16),
            Text(
              'No saved replies yet',
              style: AppTypography.h3,
              textAlign: TextAlign.center,
            ),
            const SizedBox(height: 8),
            Text(
              'Write the answer you keep typing once, then insert it into a '
              'ticket reply in two taps.',
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
              'Could not load the saved replies',
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
