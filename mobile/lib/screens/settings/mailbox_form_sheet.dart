import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/theme/theme.dart';
import '../../data/models/mailbox.dart';
import '../../providers/lookup_provider.dart';

/// Add or edit an inbound address.
///
/// Returns the request body, or null if dismissed.
///
/// **No secret field, and none is coming.** The signing-secret column is
/// write-only server-side and reserved for providers that sign deliveries that
/// way, none of which are implemented. A field for it would mean a credential
/// travels to a phone and back on every edit, and an empty one posted on a save
/// would blank the column. The topic pin is the webhook's to set, from the
/// first verified subscription, so it is not a field either.
Future<Map<String, dynamic>?> showMailboxFormSheet(
  BuildContext context, {
  Mailbox? existing,
}) {
  return showModalBottomSheet<Map<String, dynamic>>(
    context: context,
    isScrollControlled: true,
    builder: (context) => _MailboxFormSheet(existing: existing),
  );
}

class _MailboxFormSheet extends ConsumerStatefulWidget {
  const _MailboxFormSheet({this.existing});

  final Mailbox? existing;

  @override
  ConsumerState<_MailboxFormSheet> createState() => _MailboxFormSheetState();
}

class _MailboxFormSheetState extends ConsumerState<_MailboxFormSheet> {
  late final TextEditingController _address;
  late String _provider;
  late String _priority;
  String? _caseType;
  String? _assigneeId;
  String? _error;

  bool get _isCreate => widget.existing == null;

  @override
  void initState() {
    super.initState();
    final mailbox = widget.existing;
    _address = TextEditingController(text: mailbox?.address ?? '');
    _provider = mailbox?.provider ?? 'ses';
    _priority = mailbox?.defaultPriority ?? 'Normal';
    _caseType = mailbox?.defaultCaseType;
    _assigneeId = mailbox?.defaultAssignee?.id;
  }

  @override
  void dispose() {
    _address.dispose();
    super.dispose();
  }

  void _submit() {
    final problem = mailboxAddressProblem(_address.text);
    if (problem != null) {
      setState(() => _error = problem);
      return;
    }
    Navigator.of(context).pop(
      mailboxPayload(
        address: _address.text,
        provider: _provider,
        defaultPriority: _priority,
        defaultCaseType: _caseType,
        defaultAssigneeId: _assigneeId,
        // Only the create form carries this. On an edit the row's own Turn off
        // and Turn on controls own the state, and resending it from here would
        // let a form opened before a toggle undo it.
        isActive: _isCreate ? true : null,
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final people = ref.watch(usersProvider);
    // The stored assignee when the picker cannot offer them, because the
    // profile has been deactivated since it was chosen (or the list has not
    // loaded yet). Dropping them here would move the mailbox back to
    // unassigned as a side effect of editing the address, so they get an entry
    // of their own, labelled deactivated only when they are.
    final stored = widget.existing?.defaultAssignee;
    final offList = stored != null && !people.any((p) => p.id == stored.id)
        ? stored
        : null;

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
              _isCreate ? 'New address' : 'Edit ${widget.existing!.address}',
              style: AppTypography.h3.copyWith(fontWeight: FontWeight.w600),
            ),
            const SizedBox(height: 16),
            TextField(
              controller: _address,
              keyboardType: TextInputType.emailAddress,
              autocorrect: false,
              decoration: InputDecoration(
                labelText: 'Address',
                helperText: _isCreate
                    ? null
                    : 'Mail to the old address stops becoming tickets.',
                helperMaxLines: 2,
                border: const OutlineInputBorder(),
              ),
            ),
            const SizedBox(height: 12),
            DropdownButtonFormField<String>(
              initialValue: _provider,
              isExpanded: true,
              decoration: const InputDecoration(
                labelText: 'Provider',
                helperText:
                    'Only AWS SES is implemented. Mail to an address using one '
                    'of the others becomes nothing until that integration '
                    'exists.',
                helperMaxLines: 4,
                border: OutlineInputBorder(),
              ),
              items: [
                for (final provider in mailboxProviderLabels.keys)
                  DropdownMenuItem(
                    value: provider,
                    child: Text(mailboxProviderLabel(provider)),
                  ),
              ],
              onChanged: (v) => setState(() => _provider = v ?? _provider),
            ),
            const SizedBox(height: 16),
            const _SectionLabel('What a ticket opens as'),
            DropdownButtonFormField<String>(
              initialValue: _priority,
              isExpanded: true,
              decoration: const InputDecoration(
                labelText: 'Priority',
                border: OutlineInputBorder(),
              ),
              items: [
                for (final priority in mailboxPriorities)
                  DropdownMenuItem(value: priority, child: Text(priority)),
              ],
              onChanged: (v) => setState(() => _priority = v ?? _priority),
            ),
            const SizedBox(height: 12),
            DropdownButtonFormField<String?>(
              initialValue: _caseType,
              isExpanded: true,
              decoration: const InputDecoration(
                labelText: 'Type',
                border: OutlineInputBorder(),
              ),
              items: [
                const DropdownMenuItem(value: null, child: Text('None')),
                for (final type in mailboxCaseTypes)
                  DropdownMenuItem(value: type, child: Text(type)),
              ],
              onChanged: (v) => setState(() => _caseType = v),
            ),
            const SizedBox(height: 12),
            DropdownButtonFormField<String?>(
              initialValue: _assigneeId,
              isExpanded: true,
              decoration: const InputDecoration(
                labelText: 'Assigned to',
                helperText:
                    'Where a ticket goes after that is decided by routing, not '
                    'by this.',
                helperMaxLines: 3,
                border: OutlineInputBorder(),
              ),
              items: [
                const DropdownMenuItem(
                  value: null,
                  child: Text('Unassigned, then routed'),
                ),
                if (offList != null)
                  DropdownMenuItem(
                    value: offList.id,
                    child: Text(
                      offList.isActive
                          ? offList.displayName
                          : '${offList.displayName} (deactivated)',
                    ),
                  ),
                for (final person in people)
                  DropdownMenuItem(
                    value: person.id,
                    child: Text(person.displayName),
                  ),
              ],
              onChanged: (v) => setState(() => _assigneeId = v),
            ),
            if (offList != null &&
                !offList.isActive &&
                _assigneeId == offList.id) ...[
              const SizedBox(height: 8),
              Text(
                'Deactivated users are not assigned. New tickets from this '
                'address go to routing until you choose someone else.',
                style: AppTypography.caption.copyWith(
                  color: AppColors.warning600,
                ),
              ),
            ],
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
                      child: Text(_isCreate ? 'Add address' : 'Save changes'),
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
