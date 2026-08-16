import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/theme/theme.dart';
import '../../data/models/escalation_policy.dart';
import '../../data/models/lookup_models.dart';
import '../../providers/lookup_provider.dart';

/// The result of the sheet: which request to send, and what to send.
///
/// Create and edit do not send the same body. `priority` exists only on a
/// create and `is_active` only on a create, so the two are built by separate
/// functions rather than one payload with holes in it.
typedef EscalationFormResult = Map<String, dynamic>;

/// Write or edit an escalation policy.
///
/// Returns the request body, or `null` if dismissed. [availablePriorities] is
/// the set a create may choose from: at most one policy exists per priority, so
/// offering a taken one would only produce a 400 the admin could have been
/// spared. It is ignored on an edit, where the priority is frozen.
Future<EscalationFormResult?> showEscalationPolicyFormSheet(
  BuildContext context, {
  EscalationPolicy? existing,
  List<String> availablePriorities = const [],
}) {
  return showModalBottomSheet<EscalationFormResult>(
    context: context,
    isScrollControlled: true,
    builder: (context) => _EscalationPolicyFormSheet(
      existing: existing,
      availablePriorities: availablePriorities,
    ),
  );
}

class _EscalationPolicyFormSheet extends ConsumerStatefulWidget {
  const _EscalationPolicyFormSheet({
    this.existing,
    required this.availablePriorities,
  });

  final EscalationPolicy? existing;
  final List<String> availablePriorities;

  @override
  ConsumerState<_EscalationPolicyFormSheet> createState() =>
      _EscalationPolicyFormSheetState();
}

class _EscalationPolicyFormSheetState
    extends ConsumerState<_EscalationPolicyFormSheet> {
  late String _priority;
  late String _firstResponseAction;
  late String _resolutionAction;

  /// `''` is "Nobody", which the payload turns into `null`.
  late String _firstResponseTargetId;
  late String _resolutionTargetId;
  late String _teamId;

  /// Blank means "no override", which falls back to the built-in default for
  /// this priority. Kept as text so an emptied field stays distinguishable
  /// from a zero the serializer would reject anyway.
  late final TextEditingController _firstResponseHours;
  late final TextEditingController _resolutionHours;
  String? _hoursError;

  bool get _isCreate => widget.existing == null;

  /// Targets already on the policy whom the picker cannot offer.
  ///
  /// `GetTeamsAndUsersView` returns `Profile.objects.filter(is_active=True)`
  /// while `first_response_target` / `resolution_target` carry whoever was
  /// chosen, deactivated or not, and the engine escalates to them with no
  /// active filter. Without an entry of their own, opening this form to change
  /// one unrelated field would drop the target to Nobody and stop breaches at
  /// this priority escalating to anybody.
  Map<String, String> get _offListNames => {
    for (final target in widget.existing?.deactivatedTargets ?? const [])
      target.id: target.displayName,
  };

  @override
  void initState() {
    super.initState();
    final policy = widget.existing;
    _priority =
        policy?.priority ??
        (widget.availablePriorities.isNotEmpty
            ? widget.availablePriorities.first
            : escalationPriorities.first);
    _firstResponseAction =
        policy?.firstResponseAction ?? escalationActionNotify;
    _resolutionAction = policy?.resolutionAction ?? escalationActionNotify;
    _firstResponseTargetId = policy?.firstResponseTarget?.id ?? '';
    _resolutionTargetId = policy?.resolutionTarget?.id ?? '';
    _teamId = policy?.notifyTeam?.id ?? '';
    _firstResponseHours = TextEditingController(
      text: policy?.firstResponseHours?.toString() ?? '',
    );
    _resolutionHours = TextEditingController(
      text: policy?.resolutionHours?.toString() ?? '',
    );
  }

  @override
  void dispose() {
    _firstResponseHours.dispose();
    _resolutionHours.dispose();
    super.dispose();
  }

  void _submit() {
    final first = parseSlaHours(_firstResponseHours.text);
    final resolution = parseSlaHours(_resolutionHours.text);
    final error = first.error ?? resolution.error;
    if (error != null) {
      setState(() => _hoursError = error);
      return;
    }
    Navigator.of(context).pop(
      _isCreate
          ? escalationCreatePayload(
              priority: _priority,
              firstResponseAction: _firstResponseAction,
              resolutionAction: _resolutionAction,
              firstResponseTargetId: _firstResponseTargetId,
              resolutionTargetId: _resolutionTargetId,
              notifyTeamId: _teamId,
              firstResponseHours: first.hours,
              resolutionHours: resolution.hours,
            )
          : escalationUpdatePayload(
              firstResponseAction: _firstResponseAction,
              resolutionAction: _resolutionAction,
              firstResponseTargetId: _firstResponseTargetId,
              resolutionTargetId: _resolutionTargetId,
              notifyTeamId: _teamId,
              firstResponseHours: first.hours,
              resolutionHours: resolution.hours,
            ),
    );
  }

  /// One hours field. Blank is legal and means the built-in default, which the
  /// hint names so an empty box never reads as "no SLA".
  Widget _hoursField({
    required String label,
    required TextEditingController controller,
    required EscalationHalf half,
  }) {
    return TextField(
      controller: controller,
      keyboardType: TextInputType.number,
      decoration: InputDecoration(
        labelText: label,
        border: const OutlineInputBorder(),
        isDense: true,
        hintText: '${defaultSlaHours(_priority, half)} (default)',
        helperText: 'Business hours. Blank uses the default.',
        helperMaxLines: 2,
      ),
      onChanged: (_) {
        if (_hoursError != null) setState(() => _hoursError = null);
      },
    );
  }

  @override
  Widget build(BuildContext context) {
    final people = ref.watch(usersProvider);
    final teams = ref.watch(teamsProvider);
    final offList = _offListNames;

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
              _isCreate
                  ? 'New escalation policy'
                  : 'Edit the ${widget.existing!.priority} policy',
              style: AppTypography.h3.copyWith(fontWeight: FontWeight.w600),
            ),
            const SizedBox(height: 16),
            if (_isCreate)
              DropdownButtonFormField<String>(
                initialValue: _priority,
                isExpanded: true,
                decoration: const InputDecoration(
                  labelText: 'Priority',
                  helperText: 'Fixed once the policy is saved',
                  border: OutlineInputBorder(),
                ),
                items: [
                  for (final priority in widget.availablePriorities)
                    DropdownMenuItem(value: priority, child: Text(priority)),
                ],
                onChanged: (v) => setState(() => _priority = v ?? _priority),
              )
            else
              // Not a disabled select. `EscalationPolicyDetailView.put` strips
              // `priority` from the body, so any control here would report a
              // change it did not make.
              Text(
                'Priority ${widget.existing!.priority}. One policy per '
                'priority, fixed after it is created.',
                style: AppTypography.caption.copyWith(
                  color: AppColors.textSecondary,
                ),
              ),
            const SizedBox(height: 20),
            const _SectionLabel('SLA targets'),
            const SizedBox(height: 8),
            _hoursField(
              label: 'First response (hours)',
              controller: _firstResponseHours,
              half: EscalationHalf.firstResponse,
            ),
            const SizedBox(height: 12),
            _hoursField(
              label: 'Resolution (hours)',
              controller: _resolutionHours,
              half: EscalationHalf.resolution,
            ),
            if (_hoursError != null) ...[
              const SizedBox(height: 8),
              Text(
                _hoursError!,
                style: AppTypography.caption.copyWith(
                  color: AppColors.danger600,
                ),
              ),
            ],
            const SizedBox(height: 8),
            Text(
              'Applies to tickets opened from now on, and to any ticket moved '
              'to this priority. Tickets already open keep the target they '
              'were given.',
              style: AppTypography.caption.copyWith(
                color: AppColors.textSecondary,
              ),
            ),
            const SizedBox(height: 20),
            _HalfFields(
              half: EscalationHalf.firstResponse,
              action: _firstResponseAction,
              targetId: _firstResponseTargetId,
              people: people,
              offList: offList,
              onAction: (v) => setState(() => _firstResponseAction = v),
              onTarget: (v) => setState(() => _firstResponseTargetId = v),
            ),
            const SizedBox(height: 20),
            _HalfFields(
              half: EscalationHalf.resolution,
              action: _resolutionAction,
              targetId: _resolutionTargetId,
              people: people,
              offList: offList,
              onAction: (v) => setState(() => _resolutionAction = v),
              onTarget: (v) => setState(() => _resolutionTargetId = v),
            ),
            const SizedBox(height: 20),
            const _SectionLabel('Also notify'),
            DropdownButtonFormField<String>(
              initialValue: teams.any((t) => t.id == _teamId) ? _teamId : '',
              isExpanded: true,
              decoration: const InputDecoration(
                labelText: 'Team',
                border: OutlineInputBorder(),
              ),
              items: [
                const DropdownMenuItem(value: '', child: Text('No team')),
                for (final team in teams)
                  DropdownMenuItem(value: team.id, child: Text(team.name)),
              ],
              onChanged: (v) => setState(() => _teamId = v ?? ''),
            ),
            const SizedBox(height: 6),
            Text(
              teams.isEmpty
                  ? 'No teams in this organization yet.'
                  : 'Copied in alongside the target, on halves that notify. A '
                        'team is never escalated to on its own.',
              style: AppTypography.caption.copyWith(
                color: AppColors.textSecondary,
              ),
            ),
            const SizedBox(height: 20),
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
                      child: Text(_isCreate ? 'Add policy' : 'Save changes'),
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

/// One half of the SLA: what to do, and who to do it to.
class _HalfFields extends StatelessWidget {
  const _HalfFields({
    required this.half,
    required this.action,
    required this.targetId,
    required this.people,
    required this.offList,
    required this.onAction,
    required this.onTarget,
  });

  final EscalationHalf half;
  final String action;
  final String targetId;
  final List<UserLookup> people;
  final Map<String, String> offList;
  final ValueChanged<String> onAction;
  final ValueChanged<String> onTarget;

  @override
  Widget build(BuildContext context) {
    final knownIds = {'', ...people.map((p) => p.id), ...offList.keys};
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        _SectionLabel(half.label),
        DropdownButtonFormField<String>(
          initialValue: action,
          isExpanded: true,
          decoration: const InputDecoration(
            labelText: 'Do what',
            border: OutlineInputBorder(),
          ),
          items: [
            for (final entry in escalationActionLabels.entries)
              DropdownMenuItem(value: entry.key, child: Text(entry.value)),
          ],
          onChanged: (v) => onAction(v ?? action),
        ),
        const SizedBox(height: 12),
        DropdownButtonFormField<String>(
          initialValue: knownIds.contains(targetId) ? targetId : '',
          isExpanded: true,
          decoration: const InputDecoration(
            labelText: 'To whom',
            border: OutlineInputBorder(),
          ),
          items: [
            const DropdownMenuItem(value: '', child: Text('Nobody')),
            for (final person in people)
              DropdownMenuItem(
                value: person.id,
                child: Text(person.displayName),
              ),
            // A deactivated target the picker cannot offer, kept so opening the
            // form does not silently empty the policy.
            for (final entry in offList.entries)
              if (entry.key != '' && !people.any((p) => p.id == entry.key))
                DropdownMenuItem(
                  value: entry.key,
                  child: Text('${entry.value} (deactivated)'),
                ),
          ],
          onChanged: (v) => onTarget(v ?? ''),
        ),
        if (targetId.isEmpty) ...[
          const SizedBox(height: 6),
          Text(
            escalationNoTargetHint,
            style: AppTypography.caption.copyWith(color: AppColors.warning600),
          ),
        ] else if (offList.containsKey(targetId)) ...[
          const SizedBox(height: 6),
          Text(
            'This account is no longer active. It stays set until you change '
            'it, and a breach sent here waits for somebody who cannot sign in.',
            style: AppTypography.caption.copyWith(color: AppColors.warning600),
          ),
        ] else if (!escalationActionNotifies(action)) ...[
          const SizedBox(height: 6),
          Text(
            'Reassigns the ticket. No email is sent, to them or to the team.',
            style: AppTypography.caption.copyWith(
              color: AppColors.textSecondary,
            ),
          ),
        ],
      ],
    );
  }
}

class _SectionLabel extends StatelessWidget {
  const _SectionLabel(this.text);

  final String text;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Text(
        text.toUpperCase(),
        style: AppTypography.overline.copyWith(
          color: AppColors.textSecondary,
          letterSpacing: 1.2,
        ),
      ),
    );
  }
}
