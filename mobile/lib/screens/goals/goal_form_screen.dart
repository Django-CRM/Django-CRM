import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

import '../../core/theme/theme.dart';
import '../../data/models/lookup_models.dart';
import '../../data/models/sales_goal.dart';
import '../../providers/auth_provider.dart';
import '../../providers/goals_provider.dart';
import '../../providers/lookup_provider.dart';
import '../../widgets/forms/unsaved_changes.dart';

/// Create or edit a sales goal.
///
/// Admin-only, and the gate is checked before the form is drawn rather than on
/// submit: `SalesGoalListView.post` and `SalesGoalDetailView.put`/`delete` all
/// answer 403 to a member, so drawing the form for one would be an invitation
/// to fill in a page that cannot save. The server is what refuses; this only
/// avoids offering.
///
/// One target picker, not two. The API has separate `assigned_to` and `team`
/// FKs and permits both at once, but a goal belonging to a person and a team
/// simultaneously has no meaning in either client's reading of progress, so the
/// picker offers one choice and [goalTargetFields] decodes it into exactly one.
class GoalFormScreen extends ConsumerStatefulWidget {
  const GoalFormScreen({super.key, this.goalId});

  /// Null when creating.
  final String? goalId;

  bool get isEditing => goalId != null;

  @override
  ConsumerState<GoalFormScreen> createState() => _GoalFormScreenState();
}

class _GoalFormScreenState extends ConsumerState<GoalFormScreen> {
  final TextEditingController _name = TextEditingController();
  final TextEditingController _target = TextEditingController();

  String _goalType = 'REVENUE';
  String _periodType = 'MONTHLY';
  String _periodStart = '';
  String _periodEnd = '';
  String _targetOwner = 'org';
  bool _isActive = true;

  bool _saving = false;
  bool _dirty = false;
  bool _loaded = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    if (!widget.isEditing) {
      // A month starting today is the shape most goals take, and a form that
      // opens with both dates blank makes somebody type what they were going to
      // pick anyway. Changed freely; nothing depends on the default.
      final now = DateTime.now();
      _periodStart = goalToday(now);
      _periodEnd = goalToday(DateTime(now.year, now.month + 1, now.day));
      _loaded = true;
    }
    _name.addListener(_markDirty);
    _target.addListener(_markDirty);
  }

  @override
  void dispose() {
    _name.dispose();
    _target.dispose();
    super.dispose();
  }

  void _markDirty() {
    if (!_dirty && _loaded) _dirty = true;
  }

  void _fill(SalesGoal goal) {
    _name.text = goal.name;
    // `toStringAsFixed(2)` would show "50000.00" for a round quota. The server
    // sends a DecimalField, so trailing zeros are on the wire, and trimming
    // them is the difference between a field somebody edits and one they
    // retype.
    _target.text = goal.targetValue == goal.targetValue.roundToDouble()
        ? goal.targetValue.round().toString()
        : goal.targetValue.toString();
    _goalType = goal.goalType;
    _periodType = goal.periodType;
    _periodStart = goal.periodStart;
    _periodEnd = goal.periodEnd;
    _targetOwner = goalTargetValue(goal);
    _isActive = goal.isActive;
    // Set last: filling the controllers fires the listeners above, and a form
    // that opens already dirty prompts on the way out of a page nobody edited.
    _loaded = true;
    _dirty = false;
  }

  Future<void> _pickDate({required bool isStart}) async {
    final current = DateTime.tryParse(isStart ? _periodStart : _periodEnd);
    final picked = await showDatePicker(
      context: context,
      initialDate: current ?? DateTime.now(),
      // Wide enough for a goal set against a past period (backfilling last
      // quarter) and for a multi-year one.
      firstDate: DateTime(DateTime.now().year - 3),
      lastDate: DateTime(DateTime.now().year + 6),
    );
    if (picked == null) return;
    setState(() {
      _dirty = true;
      if (isStart) {
        _periodStart = goalToday(picked);
      } else {
        _periodEnd = goalToday(picked);
      }
    });
  }

  Future<void> _save() async {
    final problem = validateGoalForm(
      name: _name.text,
      targetValue: _target.text,
      periodStart: _periodStart,
      periodEnd: _periodEnd,
    );
    if (problem != null) {
      setState(() => _error = problem);
      return;
    }

    setState(() {
      _saving = true;
      _error = null;
    });

    final body = <String, dynamic>{
      'name': _name.text.trim(),
      'goal_type': _goalType,
      'target_value': _target.text.trim(),
      'period_type': _periodType,
      'period_start': _periodStart,
      'period_end': _periodEnd,
      'is_active': _isActive,
      ...goalTargetFields(_targetOwner),
    };

    final notifier = ref.read(goalsProvider.notifier);
    final response = widget.isEditing
        ? await notifier.updateGoal(widget.goalId!, body)
        : await notifier.createGoal(body);

    if (!mounted) return;
    if (!response.success) {
      setState(() {
        _saving = false;
        _error = response.message ?? 'Could not save this goal.';
      });
      return;
    }

    _dirty = false;
    context.pop();
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(widget.isEditing ? 'Goal updated' : 'Goal created'),
      ),
    );
  }

  Future<void> _delete() async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Delete this goal?'),
        content: const Text(
          'The goal and its record of the period go for good. Nothing that was '
          'closed against it changes: those deals stay exactly as they are. '
          'To stop tracking a goal without losing it, retire it instead.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Keep it'),
          ),
          TextButton(
            onPressed: () => Navigator.pop(context, true),
            style: TextButton.styleFrom(foregroundColor: AppColors.danger600),
            child: const Text('Delete'),
          ),
        ],
      ),
    );
    if (confirmed != true || !mounted) return;

    setState(() => _saving = true);
    final response = await ref
        .read(goalsProvider.notifier)
        .deleteGoal(widget.goalId!);
    if (!mounted) return;
    if (!response.success) {
      setState(() {
        _saving = false;
        _error = response.message ?? 'Could not delete this goal.';
      });
      return;
    }
    _dirty = false;
    context.pop();
    ScaffoldMessenger.of(
      context,
    ).showSnackBar(const SnackBar(content: Text('Goal deleted')));
  }

  @override
  Widget build(BuildContext context) {
    if (!ref.watch(isOrgAdminProvider)) return const _AdminsOnly();

    if (widget.isEditing && !_loaded) {
      return ref
          .watch(goalProvider(widget.goalId!))
          .when(
            loading: () => const _Busy(),
            error: (e, _) => _LoadFailed(message: e.toString()),
            data: (goal) {
              _fill(goal);
              return _form(context);
            },
          );
    }
    return _form(context);
  }

  Widget _form(BuildContext context) {
    final people = ref.watch(usersProvider);
    final teams = ref.watch(teamsProvider);

    return UnsavedChangesGuard(
      hasUnsavedChanges: () => _dirty,
      isSaving: _saving,
      child: Scaffold(
        backgroundColor: AppColors.surfaceDim,
        appBar: AppBar(
          title: Text(widget.isEditing ? 'Edit goal' : 'New goal'),
          backgroundColor: AppColors.surface,
          elevation: 0,
          scrolledUnderElevation: 1,
          leading: IconButton(
            icon: const Icon(LucideIcons.arrowLeft),
            onPressed: _saving
                ? null
                : () => leaveForm(context, hasUnsavedChanges: _dirty),
          ),
        ),
        body: ListView(
          padding: const EdgeInsets.fromLTRB(16, 16, 16, 32),
          children: [
            if (_error != null) ...[
              Container(
                width: double.infinity,
                padding: const EdgeInsets.all(12),
                decoration: BoxDecoration(
                  color: AppColors.danger50,
                  borderRadius: BorderRadius.circular(8),
                  border: Border.all(color: AppColors.danger200),
                ),
                child: Text(
                  _error!,
                  style: AppTypography.caption.copyWith(
                    color: AppColors.danger600,
                  ),
                ),
              ),
              const SizedBox(height: 16),
            ],

            TextField(
              controller: _name,
              textCapitalization: TextCapitalization.sentences,
              maxLength: 255,
              decoration: const InputDecoration(
                labelText: 'Goal name',
                border: OutlineInputBorder(),
                counterText: '',
              ),
            ),
            const SizedBox(height: 16),

            DropdownButtonFormField<String>(
              initialValue: _goalType,
              decoration: const InputDecoration(
                labelText: 'Measured in',
                border: OutlineInputBorder(),
              ),
              items: [
                for (final type in goalTypes)
                  DropdownMenuItem(
                    value: type,
                    child: Text(goalTypeLabel(type)),
                  ),
              ],
              onChanged: _saving
                  ? null
                  : (value) => setState(() {
                      _goalType = value ?? _goalType;
                      _dirty = true;
                    }),
            ),
            const SizedBox(height: 16),

            TextField(
              controller: _target,
              keyboardType: const TextInputType.numberWithOptions(
                decimal: true,
              ),
              inputFormatters: [
                FilteringTextInputFormatter.allow(RegExp(r'[0-9.]')),
              ],
              decoration: InputDecoration(
                labelText: 'Target',
                border: const OutlineInputBorder(),
                helperText: _goalType == 'DEALS_CLOSED'
                    ? 'How many deals must close in the period'
                    : 'Total revenue that must close in the period',
                helperMaxLines: 2,
              ),
            ),
            const SizedBox(height: 16),

            DropdownButtonFormField<String>(
              initialValue: _periodType,
              decoration: const InputDecoration(
                labelText: 'Period',
                border: OutlineInputBorder(),
              ),
              items: [
                for (final period in goalPeriodTypes)
                  DropdownMenuItem(
                    value: period,
                    child: Text(goalPeriodLabel(period)),
                  ),
              ],
              onChanged: _saving
                  ? null
                  : (value) => setState(() {
                      _periodType = value ?? _periodType;
                      _dirty = true;
                    }),
            ),
            const SizedBox(height: 6),
            Text(
              // The dates are what the server actually counts against, and the
              // period type is only a label on the board's filter. Saying so
              // stops somebody choosing Monthly and expecting the dates to
              // follow.
              'The period type groups goals on the leaderboard. The dates below '
              'are what progress is counted against.',
              style: AppTypography.caption.copyWith(
                color: AppColors.textSecondary,
              ),
            ),
            const SizedBox(height: 16),

            Row(
              children: [
                Expanded(
                  child: _DateField(
                    label: 'Starts',
                    value: _periodStart,
                    onTap: _saving ? null : () => _pickDate(isStart: true),
                  ),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: _DateField(
                    label: 'Ends',
                    value: _periodEnd,
                    onTap: _saving ? null : () => _pickDate(isStart: false),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 16),

            DropdownButtonFormField<String>(
              initialValue: _ownerValue(people, teams),
              isExpanded: true,
              decoration: const InputDecoration(
                labelText: 'Goal for',
                border: OutlineInputBorder(),
              ),
              items: [
                const DropdownMenuItem(
                  value: 'org',
                  child: Text('The whole organisation'),
                ),
                for (final person in people)
                  DropdownMenuItem(
                    value: 'profile:${person.id}',
                    child: Text(
                      person.displayName,
                      overflow: TextOverflow.ellipsis,
                    ),
                  ),
                for (final team in teams)
                  DropdownMenuItem(
                    value: 'team:${team.id}',
                    child: Text(
                      '${team.name} (team)',
                      overflow: TextOverflow.ellipsis,
                    ),
                  ),
              ],
              onChanged: _saving
                  ? null
                  : (value) => setState(() {
                      _targetOwner = value ?? _targetOwner;
                      _dirty = true;
                    }),
            ),
            const SizedBox(height: 8),

            SwitchListTile(
              contentPadding: EdgeInsets.zero,
              value: _isActive,
              onChanged: _saving
                  ? null
                  : (value) => setState(() {
                      _isActive = value;
                      _dirty = true;
                    }),
              title: const Text('Active'),
              subtitle: Text(
                'A retired goal stays on the list and stops counting towards '
                'the totals and the leaderboard.',
                style: AppTypography.caption.copyWith(
                  color: AppColors.textSecondary,
                ),
              ),
            ),
            const SizedBox(height: 20),

            SizedBox(
              height: 48,
              child: FilledButton(
                onPressed: _saving ? null : _save,
                child: _saving
                    ? const SizedBox(
                        width: 18,
                        height: 18,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : Text(widget.isEditing ? 'Save changes' : 'Create goal'),
              ),
            ),

            if (widget.isEditing) ...[
              const SizedBox(height: 12),
              SizedBox(
                height: 48,
                child: OutlinedButton(
                  onPressed: _saving ? null : _delete,
                  style: OutlinedButton.styleFrom(
                    foregroundColor: AppColors.danger600,
                    side: BorderSide(color: AppColors.danger200),
                  ),
                  child: const Text('Delete goal'),
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }

  /// The dropdown's value, guarded against an option that is not in the list.
  ///
  /// A goal assigned to somebody since deactivated has an `assigned_to` the
  /// people lookup (active profiles only) does not carry, and a
  /// `DropdownButtonFormField` whose value matches no item throws. Falling back
  /// to `org` would silently reassign the goal on the next save, so the stored
  /// value is kept and only the DISPLAY falls back, with the mismatch surfaced
  /// as an extra item.
  String? _ownerValue(List<UserLookup> people, List<TeamLookup> teams) {
    if (_targetOwner == 'org') return 'org';
    final known = [
      for (final p in people) 'profile:${p.id}',
      for (final t in teams) 'team:${t.id}',
    ];
    return known.contains(_targetOwner) ? _targetOwner : null;
  }
}

class _DateField extends StatelessWidget {
  const _DateField({required this.label, required this.value, this.onTap});

  final String label;
  final String value;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: onTap,
      child: InputDecorator(
        decoration: InputDecoration(
          labelText: label,
          border: const OutlineInputBorder(),
        ),
        // A 48px box keeps the tap target above the 44px floor, which an
        // InputDecorator wrapping a single line of text does not reach on its
        // own.
        child: SizedBox(
          height: 24,
          child: Align(
            alignment: Alignment.centerLeft,
            child: Text(value.isEmpty ? 'Pick a date' : value),
          ),
        ),
      ),
    );
  }
}

class _Busy extends StatelessWidget {
  const _Busy();

  @override
  Widget build(BuildContext context) =>
      const Scaffold(body: Center(child: CircularProgressIndicator()));
}

class _LoadFailed extends StatelessWidget {
  const _LoadFailed({required this.message});

  final String message;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Goal')),
      body: Center(
        child: Padding(
          padding: const EdgeInsets.all(32),
          child: Text(
            message,
            style: AppTypography.body.copyWith(color: AppColors.textSecondary),
            textAlign: TextAlign.center,
          ),
        ),
      ),
    );
  }
}

class _AdminsOnly extends StatelessWidget {
  const _AdminsOnly();

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Goal')),
      body: Center(
        child: Padding(
          padding: const EdgeInsets.all(32),
          child: Text(
            'Only an administrator can add or change a goal. You can see the '
            'ones set for you and your teams on the goals list.',
            style: AppTypography.body.copyWith(color: AppColors.textSecondary),
            textAlign: TextAlign.center,
          ),
        ),
      ),
    );
  }
}
