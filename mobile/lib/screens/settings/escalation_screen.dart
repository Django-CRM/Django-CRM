import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

import '../../core/theme/theme.dart';
import '../../data/models/escalation_policy.dart';
import '../../data/models/ticket.dart';
import '../../providers/auth_provider.dart';
import '../../providers/settings_provider.dart';
import '../../widgets/common/badge.dart';
import 'escalation_policy_form_sheet.dart';

/// What happens when a ticket blows its target.
///
/// **The question a row has to answer is not "is this configured" but "does
/// this fire".** At most one policy exists per priority, so every row here
/// looks configured; the model still allows a policy to sit in the list doing
/// nothing, and the breach count beside it says how much has gone past while it
/// did. That pairing is the whole page: "11 breaches, nobody told" on one line
/// rather than across two screens.
///
/// Reading is open to every member; every write is admin-only and the server
/// answers 403 to anyone else.
class EscalationScreen extends ConsumerWidget {
  const EscalationScreen({super.key});

  Future<void> _edit(
    BuildContext context,
    WidgetRef ref,
    EscalationPolicy? existing,
    List<String> available,
  ) async {
    final payload = await showEscalationPolicyFormSheet(
      context,
      existing: existing,
      availablePriorities: available,
    );
    if (payload == null || !context.mounted) return;
    final notifier = ref.read(escalationProvider.notifier);
    final error = existing == null
        ? await notifier.createPolicy(payload)
        : await notifier.updatePolicy(existing.id, payload);
    if (!context.mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(
          error ?? (existing == null ? 'Policy added' : 'Policy saved'),
        ),
      ),
    );
  }

  Future<void> _setActive(
    BuildContext context,
    WidgetRef ref,
    EscalationPolicy policy,
    bool active,
  ) async {
    final notifier = ref.read(escalationProvider.notifier);
    final error = active
        ? await notifier.activatePolicy(policy.id)
        : await notifier.deactivatePolicy(policy.id);
    if (!context.mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(
          error ?? (active ? 'Policy turned on' : 'Policy turned off'),
        ),
      ),
    );
  }

  Future<void> _delete(
    BuildContext context,
    WidgetRef ref,
    EscalationPolicy policy,
  ) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        // "Delete", not "Turn off". This one really does delete.
        title: Text('Delete the ${policy.priority} policy?'),
        content: const Text(escalationRemovalExplanation),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop(false),
            child: const Text('Keep it'),
          ),
          TextButton(
            onPressed: () => Navigator.of(context).pop(true),
            style: TextButton.styleFrom(foregroundColor: AppColors.danger600),
            child: const Text('Delete'),
          ),
        ],
      ),
    );
    if (confirmed != true || !context.mounted) return;
    final error = await ref
        .read(escalationProvider.notifier)
        .deletePolicy(policy.id);
    if (!context.mounted) return;
    ScaffoldMessenger.of(
      context,
    ).showSnackBar(SnackBar(content: Text(error ?? 'Policy deleted')));
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final async = ref.watch(escalationProvider);
    final isAdmin = ref.watch(isOrgAdminProvider);

    return Scaffold(
      backgroundColor: AppColors.surfaceDim,
      appBar: AppBar(
        title: const Text('Escalation'),
        backgroundColor: AppColors.surface,
        elevation: 0,
        scrolledUnderElevation: 1,
        actions: [
          // Hidden once all four priorities have a policy, rather than shown
          // disabled: a control that cannot be used and does not say why is the
          // dead end this screen exists to remove.
          if (isAdmin && (async.value?.unconfigured ?? const []).isNotEmpty)
            IconButton(
              icon: const Icon(LucideIcons.plus),
              tooltip: 'New policy',
              onPressed: () =>
                  _edit(context, ref, null, async.value!.unconfigured),
            ),
        ],
      ),
      body: async.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (_, _) => _ErrorState(
          onRetry: () => ref.read(escalationProvider.notifier).refresh(),
        ),
        data: (state) {
          if (state.policies.isEmpty) return _EmptyState(isAdmin: isAdmin);
          return RefreshIndicator(
            onRefresh: () => ref.read(escalationProvider.notifier).refresh(),
            child: ListView(
              padding: const EdgeInsets.only(bottom: 96),
              children: [
                _Summary(state: state),
                for (final policy in state.policies)
                  _PolicyCard(
                    policy: policy,
                    canEdit: isAdmin,
                    onEdit: () =>
                        _edit(context, ref, policy, state.unconfigured),
                    onTurnOff: () => _setActive(context, ref, policy, false),
                    onTurnOn: () => _setActive(context, ref, policy, true),
                    onDelete: () => _delete(context, ref, policy),
                  ),
                const _Footnote(),
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

  final EscalationState state;

  @override
  Widget build(BuildContext context) {
    final unconfigured = state.unconfigured;
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
              _Stat(value: '${state.policies.length}', label: 'of 4 set up'),
              _Stat(value: '${state.dead}', label: 'that never fire'),
            ],
          ),
          if (state.breachesGoingNowhere > 0) ...[
            const SizedBox(height: 14),
            _Warning(
              text:
                  '${state.breachesGoingNowhere} breaches in the last 30 days '
                  'told nobody. A policy that exists is not the same as a '
                  'policy that fires.',
            ),
          ],
          if (unconfigured.isNotEmpty) ...[
            const SizedBox(height: 12),
            Text(
              unconfigured.length == 1
                  ? '${unconfigured.first} has no policy, so breaches at that '
                        'priority escalate to nobody and are not counted here.'
                  : '${joinWithAnd(unconfigured)} have no policy, so breaches '
                        'at those priorities escalate to nobody and are not '
                        'counted here.',
              style: AppTypography.caption.copyWith(
                color: AppColors.textSecondary,
              ),
            ),
          ],
        ],
      ),
    );
  }
}

class _Warning extends StatelessWidget {
  const _Warning({required this.text});

  final String text;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: AppColors.warning50,
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: AppColors.warning200),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(LucideIcons.bellOff, size: 18, color: AppColors.warning600),
          const SizedBox(width: 10),
          Expanded(
            child: Text(
              text,
              style: AppTypography.caption.copyWith(
                color: AppColors.textPrimary,
              ),
            ),
          ),
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

class _PolicyCard extends StatelessWidget {
  const _PolicyCard({
    required this.policy,
    required this.canEdit,
    required this.onEdit,
    required this.onTurnOff,
    required this.onTurnOn,
    required this.onDelete,
  });

  final EscalationPolicy policy;
  final bool canEdit;
  final VoidCallback onEdit;
  final VoidCallback onTurnOff;
  final VoidCallback onTurnOn;
  final VoidCallback onDelete;

  @override
  Widget build(BuildContext context) {
    final priority = TicketPriority.fromString(policy.priority);
    return Container(
      color: AppColors.surface,
      margin: const EdgeInsets.only(bottom: 1),
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Wrap(
            spacing: 8,
            runSpacing: 6,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              StatusBadge(label: policy.priority, color: priority.color),
              if (!policy.isActive)
                StatusBadge(label: 'Turned off', color: AppColors.gray500),
              // The promise itself. Each number says whether it is this org's
              // choice or the built-in default, because an unlabelled "4h"
              // reads as a decision somebody made.
              Text(
                '${policy.firstResponseTargetLabel} reply · '
                '${policy.resolutionTargetLabel} resolve',
                style: AppTypography.caption.copyWith(
                  color: AppColors.textSecondary,
                ),
              ),
            ],
          ),
          const SizedBox(height: 12),
          // The halves stack rather than sitting side by side. Two columns at
          // 390px would give each outcome sentence about 20 characters, and the
          // sentence is the payload.
          for (final half in EscalationHalf.values) ...[
            _Half(policy: policy, half: half),
            if (half != EscalationHalf.values.last) const SizedBox(height: 14),
          ],
          if (canEdit) ...[
            const SizedBox(height: 12),
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
                  child: policy.isActive
                      ? OutlinedButton(
                          onPressed: onTurnOff,
                          child: const Text('Turn off'),
                        )
                      : OutlinedButton(
                          onPressed: onTurnOn,
                          child: const Text('Turn on'),
                        ),
                ),
                SizedBox(
                  height: 40,
                  child: OutlinedButton(
                    onPressed: onDelete,
                    style: OutlinedButton.styleFrom(
                      foregroundColor: AppColors.danger600,
                    ),
                    child: const Text('Delete'),
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

class _Half extends StatelessWidget {
  const _Half({required this.policy, required this.half});

  final EscalationPolicy policy;
  final EscalationHalf half;

  @override
  Widget build(BuildContext context) {
    final fires = policy.firesFor(half);
    final breaches = policy.breachesFor(half);
    final ignoredTeam = escalationTeamIgnoredNote(policy, half);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          half.label.toUpperCase(),
          style: AppTypography.overline.copyWith(
            color: AppColors.textSecondary,
            letterSpacing: 1.2,
          ),
        ),
        const SizedBox(height: 4),
        Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            if (!fires) ...[
              Padding(
                padding: const EdgeInsets.only(top: 2),
                child: Icon(
                  LucideIcons.triangleAlert,
                  size: 14,
                  color: AppColors.warning600,
                ),
              ),
              const SizedBox(width: 6),
            ],
            Expanded(
              child: Text(
                escalationOutcomeSentence(policy, half),
                style: AppTypography.body.copyWith(
                  color: fires
                      ? AppColors.textPrimary
                      : AppColors.textSecondary,
                ),
              ),
            ),
          ],
        ),
        if (ignoredTeam != null) ...[
          const SizedBox(height: 4),
          Text(
            ignoredTeam,
            style: AppTypography.caption.copyWith(
              color: AppColors.textSecondary,
            ),
          ),
        ],
        const SizedBox(height: 4),
        Text(
          fires || breaches == 0
              ? '$breaches in the last 30 days'
              : '$breaches in the last 30 days, none of them acted on',
          style: AppTypography.caption.copyWith(
            color: fires || breaches == 0
                ? AppColors.textTertiary
                : AppColors.warning600,
          ),
        ),
      ],
    );
  }
}

class _Footnote extends StatelessWidget {
  const _Footnote();

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 16, 16, 0),
      child: Text(
        'Targets are measured on business hours, so a breach counts working '
        'time only, and time spent waiting on the customer does not count at '
        'all. Editing a policy sets both the target and who hears about a '
        'breach. The counts cover tickets opened in the last 30 days.',
        style: AppTypography.caption.copyWith(color: AppColors.textTertiary),
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
            Icon(LucideIcons.bellOff, size: 40, color: AppColors.textTertiary),
            const SizedBox(height: 16),
            Text(
              'No escalation policies',
              style: AppTypography.h3,
              textAlign: TextAlign.center,
            ),
            const SizedBox(height: 8),
            Text(
              isAdmin
                  ? 'A policy decides what happens when a ticket misses its '
                        'first response or its resolution target. None are set, '
                        'so a breach escalates to nobody.'
                  : 'A policy decides what happens when a ticket misses its '
                        'first response or its resolution target. None are set, '
                        'so a breach escalates to nobody. An administrator sets '
                        'these up.',
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
              'Could not load the escalation policies',
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
