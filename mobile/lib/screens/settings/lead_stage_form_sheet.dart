import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../core/theme/theme.dart';
import '../../data/models/lead_pipeline.dart';

/// Add or edit a stage of a lead pipeline.
///
/// Returns the request body, or `null` if dismissed. Both pickers offer the
/// stage's current value even when it is not a choice a new stage may take
/// (see [leadStageStatusOptions]), so saving a stage without touching them
/// sends back what it already had.
Future<Map<String, dynamic>?> showLeadStageFormSheet(
  BuildContext context, {
  LeadStage? existing,
}) {
  return showModalBottomSheet<Map<String, dynamic>>(
    context: context,
    isScrollControlled: true,
    builder: (context) => _LeadStageFormSheet(existing: existing),
  );
}

class _LeadStageFormSheet extends StatefulWidget {
  const _LeadStageFormSheet({this.existing});

  final LeadStage? existing;

  @override
  State<_LeadStageFormSheet> createState() => _LeadStageFormSheetState();
}

class _LeadStageFormSheetState extends State<_LeadStageFormSheet> {
  late final TextEditingController _name;
  late final TextEditingController _probability;
  late String _stageType;
  late String? _mapsToStatus;
  late final Map<String, String> _typeOptions;
  late final Map<String?, String> _statusOptions;
  String? _error;

  bool get _isCreate => widget.existing == null;

  @override
  void initState() {
    super.initState();
    final stage = widget.existing;
    _name = TextEditingController(text: stage?.name ?? '');
    _probability = TextEditingController(text: '${stage?.winProbability ?? 0}');
    _stageType = stage?.stageType ?? 'open';
    _mapsToStatus = stage?.mapsToStatus;
    _typeOptions = leadStageTypeOptions(_stageType);
    _statusOptions = leadStageStatusOptions(_mapsToStatus);
  }

  @override
  void dispose() {
    _name.dispose();
    _probability.dispose();
    super.dispose();
  }

  void _submit() {
    final problem = leadStageDraftProblem(
      name: _name.text,
      winProbability: _probability.text,
    );
    if (problem != null) {
      setState(() => _error = problem);
      return;
    }
    Navigator.of(context).pop(
      leadStagePayload(
        name: _name.text,
        stageType: _stageType,
        mapsToStatus: _mapsToStatus,
        winProbability: int.parse(_probability.text.trim()),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
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
              _isCreate ? 'New stage' : 'Edit stage',
              style: AppTypography.h3.copyWith(fontWeight: FontWeight.w600),
            ),
            const SizedBox(height: 12),
            TextField(
              controller: _name,
              autofocus: _isCreate,
              maxLength: leadStageNameMax,
              textCapitalization: TextCapitalization.sentences,
              decoration: const InputDecoration(
                labelText: 'Name',
                border: OutlineInputBorder(),
                counterText: '',
              ),
            ),
            const SizedBox(height: 12),
            DropdownButtonFormField<String>(
              initialValue: _stageType,
              isExpanded: true,
              decoration: const InputDecoration(
                labelText: 'Stage type',
                helperText: 'Whether leads here are still open, won or lost',
                helperMaxLines: 2,
                border: OutlineInputBorder(),
              ),
              items: [
                for (final entry in _typeOptions.entries)
                  DropdownMenuItem(value: entry.key, child: Text(entry.value)),
              ],
              onChanged: (v) => setState(() => _stageType = v ?? _stageType),
            ),
            const SizedBox(height: 12),
            DropdownButtonFormField<String?>(
              initialValue: _mapsToStatus,
              isExpanded: true,
              decoration: const InputDecoration(
                labelText: 'Sets the lead status to',
                helperText: 'Applied when a lead is moved into this stage',
                helperMaxLines: 2,
                border: OutlineInputBorder(),
              ),
              items: [
                for (final entry in _statusOptions.entries)
                  DropdownMenuItem(value: entry.key, child: Text(entry.value)),
              ],
              onChanged: (v) => setState(() => _mapsToStatus = v),
            ),
            const SizedBox(height: 12),
            TextField(
              controller: _probability,
              keyboardType: TextInputType.number,
              inputFormatters: [FilteringTextInputFormatter.digitsOnly],
              decoration: const InputDecoration(
                labelText: 'Win probability, in percent',
                helperText: '0 to 100. Given to a lead moved in with none set.',
                helperMaxLines: 2,
                border: OutlineInputBorder(),
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
                      child: Text(_isCreate ? 'Add stage' : 'Save'),
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
