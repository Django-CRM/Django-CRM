import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:intl/intl.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

import '../../core/permissions.dart';
import '../../core/theme/theme.dart';
import '../../data/models/account.dart';
import '../../data/models/deal.dart' show Currency;
import '../../providers/accounts_provider.dart';
import '../../providers/auth_provider.dart';
import '../../routes/app_router.dart';

/// One account: who they are, what they are worth, and what is open against
/// them.
class AccountDetailScreen extends ConsumerStatefulWidget {
  const AccountDetailScreen({super.key, required this.accountId});

  final String accountId;

  @override
  ConsumerState<AccountDetailScreen> createState() =>
      _AccountDetailScreenState();
}

class _AccountDetailScreenState extends ConsumerState<AccountDetailScreen> {
  Account? _account;
  bool _loading = true;
  String? _error;
  bool _deleting = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    final account = await ref
        .read(accountsProvider.notifier)
        .getAccount(widget.accountId);
    if (!mounted) return;
    setState(() {
      _loading = false;
      _account = account;
      if (account == null) _error = 'Could not load this account';
    });
  }

  /// Mirrors `AccountDetailView.delete`, which allows the org's admins and the
  /// user who created the record. The server is what refuses; hiding the
  /// button only stops the app offering an action it knows will 403.
  ///
  /// Emails on both sides, like every other detail screen here. Comparing an
  /// id against an email is the mismatch that has silently disabled checks in
  /// this codebase before, and it fails closed, so nobody notices.
  bool get _canDelete {
    final account = _account;
    if (account == null) return false;
    return isAdminOrOwner(
      isAdmin: ref.read(isOrgAdminProvider),
      currentUserKey: ref.read(currentUserProvider)?.email,
      ownerKey: account.createdByEmail,
    );
  }

  Future<void> _confirmDelete() async {
    final account = _account;
    if (account == null || _deleting) return;

    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Delete this account?'),
        content: Text(
          'Deleting ${account.name} cannot be undone. Contacts and deals '
          'linked to it stay, but they lose the link.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Cancel'),
          ),
          TextButton(
            onPressed: () => Navigator.pop(context, true),
            child: Text('Delete', style: TextStyle(color: AppColors.danger600)),
          ),
        ],
      ),
    );
    if (confirmed != true || !mounted) return;

    setState(() => _deleting = true);
    final failure = await ref
        .read(accountsProvider.notifier)
        .deleteAccount(account.id);
    if (!mounted) return;
    setState(() => _deleting = false);

    if (failure != null) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(failure), backgroundColor: AppColors.danger600),
      );
      return;
    }
    context.pop();
  }

  @override
  Widget build(BuildContext context) {
    final account = _account;
    return Scaffold(
      backgroundColor: AppColors.surfaceDim,
      appBar: AppBar(
        title: Text(account?.name ?? 'Account'),
        backgroundColor: AppColors.surface,
        elevation: 0,
        scrolledUnderElevation: 1,
        leading: IconButton(
          icon: const Icon(LucideIcons.chevronLeft),
          onPressed: () => context.pop(),
        ),
        actions: [
          if (account != null)
            IconButton(
              icon: const Icon(LucideIcons.edit3, size: 20),
              tooltip: 'Edit account',
              onPressed: () async {
                await context.push('${AppRoutes.accounts}/${account.id}/edit');
                if (mounted) _load();
              },
            ),
          if (_canDelete)
            IconButton(
              icon: const Icon(LucideIcons.trash2, size: 20),
              tooltip: 'Delete account',
              onPressed: _deleting ? null : _confirmDelete,
            ),
        ],
      ),
      body: _body(),
    );
  }

  Widget _body() {
    if (_loading) return const Center(child: CircularProgressIndicator());
    final account = _account;
    if (account == null) {
      return Center(
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(LucideIcons.alertCircle, size: 40, color: AppColors.danger500),
            const SizedBox(height: 12),
            Text(_error ?? 'Not found', style: AppTypography.body),
            const SizedBox(height: 16),
            FilledButton(onPressed: _load, child: const Text('Retry')),
          ],
        ),
      );
    }

    return RefreshIndicator(
      onRefresh: _load,
      child: ListView(
        padding: const EdgeInsets.fromLTRB(12, 12, 12, 48),
        children: [
          _rollups(account),
          _details(account),
          _relationSection('Contacts', LucideIcons.users, account.contacts),
          _relationSection(
            'Deals',
            LucideIcons.trendingUp,
            account.opportunities,
          ),
          _relationSection('Tickets', LucideIcons.lifeBuoy, account.cases),
          _relationSection('Tasks', LucideIcons.checkCheck, account.tasks),
        ],
      ),
    );
  }

  /// Absent rollups and zero rollups are different things. The server sends
  /// null from any endpoint that did not run the annotation, and a panel of
  /// zeroes would be a claim this screen has not earned.
  Widget _rollups(Account account) {
    final rollups = account.rollups;
    if (rollups == null || rollups.isEmpty) return const SizedBox.shrink();
    return _card(
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          _metric(
            'Open pipeline',
            _perCurrency(account, rollups, (m) => m.openPipeline),
          ),
          _metric('Won', _perCurrency(account, rollups, (m) => m.wonAmount)),
          _metric('Open tickets', '${rollups.openTickets ?? 0}'),
        ],
      ),
    );
  }

  /// One amount per currency that has any, never a sum across currencies:
  /// there are no exchange rates. Nothing at all reads as zero in the
  /// account's own currency, as it always has.
  String _perCurrency(
    Account account,
    AccountRollups rollups,
    double Function(RollupMoney) amount,
  ) {
    String format(double value, String code) => NumberFormat.compactCurrency(
      symbol: code.isEmpty
          ? Currency.fromString(account.currency).symbol
          : Currency.symbolFor(code),
    ).format(value);
    final shown = [
      for (final m in rollups.money)
        if (amount(m) != 0) format(amount(m), m.currency),
    ];
    return shown.isEmpty ? format(0, '') : shown.join('\n');
  }

  Widget _metric(String label, String value) {
    return Expanded(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            value,
            style: AppTypography.h3.copyWith(color: AppColors.textPrimary),
          ),
          const SizedBox(height: 2),
          Text(
            label,
            style: AppTypography.caption.copyWith(
              color: AppColors.textSecondary,
            ),
          ),
        ],
      ),
    );
  }

  Widget _details(Account account) {
    final rows = <(String, String?)>[
      ('Website', account.website),
      ('Email', account.email),
      ('Phone', account.phone),
      ('Industry', account.industry),
      (
        'Employees',
        account.numberOfEmployees == null
            ? null
            : '${account.numberOfEmployees}',
      ),
      ('Annual revenue', account.annualRevenue),
      ('Address', _address(account)),
      (
        'Assigned to',
        account.assignedToNames.isEmpty
            ? null
            : account.assignedToNames.join(', '),
      ),
      ('Tags', account.tagNames.isEmpty ? null : account.tagNames.join(', ')),
      ('Notes', account.description),
    ].where((row) => row.$2 != null && row.$2!.trim().isNotEmpty).toList();

    if (rows.isEmpty) {
      return _card(
        child: Text(
          'No details recorded yet.',
          style: AppTypography.caption.copyWith(color: AppColors.textSecondary),
        ),
      );
    }

    return _card(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          for (final row in rows) ...[
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 6),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  SizedBox(
                    width: 110,
                    child: Text(
                      row.$1,
                      style: AppTypography.caption.copyWith(
                        color: AppColors.textSecondary,
                      ),
                    ),
                  ),
                  Expanded(child: Text(row.$2!, style: AppTypography.body)),
                ],
              ),
            ),
          ],
        ],
      ),
    );
  }

  String? _address(Account account) {
    final parts = [
      account.addressLine,
      account.city,
      account.state,
      account.postcode,
      account.countryDisplay ?? account.country,
    ].where((p) => p != null && p.trim().isNotEmpty).join(', ');
    return parts.isEmpty ? null : parts;
  }

  Widget _relationSection(
    String title,
    IconData icon,
    List<AccountRelation> rows,
  ) {
    if (rows.isEmpty) return const SizedBox.shrink();
    return _card(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(icon, size: 16, color: AppColors.textSecondary),
              const SizedBox(width: 6),
              Text(
                '$title (${rows.length})',
                style: AppTypography.label.copyWith(
                  color: AppColors.textSecondary,
                ),
              ),
            ],
          ),
          const SizedBox(height: 8),
          for (final row in rows)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 4),
              child: Row(
                children: [
                  Expanded(
                    child: Text(
                      row.label,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: AppTypography.body,
                    ),
                  ),
                  if (row.detail != null)
                    Text(
                      row.detail!,
                      style: AppTypography.caption.copyWith(
                        color: AppColors.textSecondary,
                      ),
                    ),
                ],
              ),
            ),
        ],
      ),
    );
  }

  Widget _card({required Widget child}) {
    return Container(
      margin: const EdgeInsets.only(bottom: 12),
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: AppColors.surface,
        border: Border.all(color: AppColors.gray200),
        borderRadius: BorderRadius.circular(8),
      ),
      child: child,
    );
  }
}
