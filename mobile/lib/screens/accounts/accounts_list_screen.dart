import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

import '../../config/api_config.dart';
import '../../core/theme/theme.dart';
import '../../data/models/account.dart';
import '../../providers/accounts_provider.dart';
import '../../routes/app_router.dart';
import '../../widgets/common/export_csv_button.dart';

/// The accounts list.
///
/// Mobile had no accounts module at all, so this is the first screen in it.
/// The count header follows the rule the leads and tickets lists now follow:
/// say nothing until there is something to count, because "0 accounts" printed
/// over a spinner is indistinguishable from an org that has none.
class AccountsListScreen extends ConsumerStatefulWidget {
  const AccountsListScreen({super.key});

  @override
  ConsumerState<AccountsListScreen> createState() => _AccountsListScreenState();
}

class _AccountsListScreenState extends ConsumerState<AccountsListScreen> {
  final _searchController = TextEditingController();
  final _scrollController = ScrollController();
  Timer? _debounce;
  bool _showClosed = false;

  @override
  void initState() {
    super.initState();
    _scrollController.addListener(_onScroll);
  }

  @override
  void dispose() {
    _debounce?.cancel();
    _searchController.dispose();
    _scrollController.dispose();
    super.dispose();
  }

  void _onScroll() {
    if (!_scrollController.hasClients) return;
    final position = _scrollController.position;
    if (position.pixels >= position.maxScrollExtent - 400) {
      ref.read(accountsProvider.notifier).loadMore();
    }
  }

  void _onSearchChanged(String value) {
    _debounce?.cancel();
    _debounce = Timer(const Duration(milliseconds: 300), () {
      ref.read(accountsProvider.notifier).search(value);
    });
  }

  @override
  Widget build(BuildContext context) {
    final async = ref.watch(accountsProvider);

    return Scaffold(
      backgroundColor: AppColors.surfaceDim,
      appBar: AppBar(
        title: const Text('Accounts'),
        backgroundColor: AppColors.surface,
        elevation: 0,
        scrolledUnderElevation: 1,
        actions: [
          ExportCsvButton(
            endpoint: ApiConfig.accountsExport,
            filePrefix: 'accounts',
            query: () async => ref.read(accountsProvider.notifier).filterQuery,
          ),
          IconButton(
            icon: const Icon(LucideIcons.plus),
            tooltip: 'New account',
            onPressed: () => context.push(AppRoutes.accountCreate),
          ),
        ],
      ),
      body: Column(
        children: [
          _searchBar(),
          _openClosedToggle(),
          _countStrip(async),
          Expanded(child: _list(async)),
        ],
      ),
    );
  }

  Widget _searchBar() {
    return Container(
      color: AppColors.surface,
      padding: const EdgeInsets.fromLTRB(12, 8, 12, 8),
      child: TextField(
        controller: _searchController,
        onChanged: _onSearchChanged,
        textInputAction: TextInputAction.search,
        decoration: InputDecoration(
          hintText: 'Search accounts',
          prefixIcon: const Icon(LucideIcons.search, size: 18),
          isDense: true,
          border: const OutlineInputBorder(),
          suffixIcon: _searchController.text.isEmpty
              ? null
              : IconButton(
                  icon: const Icon(LucideIcons.x, size: 16),
                  onPressed: () {
                    _searchController.clear();
                    ref.read(accountsProvider.notifier).search('');
                    setState(() {});
                  },
                ),
        ),
      ),
    );
  }

  /// Open and closed both arrive in the same response, so this switches which
  /// half is shown rather than issuing a different query.
  Widget _openClosedToggle() {
    return Container(
      color: AppColors.surface,
      padding: const EdgeInsets.fromLTRB(12, 0, 12, 8),
      child: Row(
        children: [
          _toggleChip('Open', !_showClosed, () => _setClosed(false)),
          const SizedBox(width: 8),
          _toggleChip('Closed', _showClosed, () => _setClosed(true)),
        ],
      ),
    );
  }

  void _setClosed(bool value) {
    if (_showClosed == value) return;
    setState(() => _showClosed = value);
    ref.read(accountsProvider.notifier).showClosed(value);
  }

  Widget _toggleChip(String label, bool active, VoidCallback onTap) {
    return GestureDetector(
      onTap: onTap,
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 6),
        decoration: BoxDecoration(
          color: active ? AppColors.primary600 : AppColors.surface,
          border: Border.all(
            color: active ? AppColors.primary600 : AppColors.gray300,
          ),
          borderRadius: BorderRadius.circular(16),
        ),
        child: Text(
          label,
          style: AppTypography.caption.copyWith(
            color: active ? Colors.white : AppColors.textSecondary,
          ),
        ),
      ),
    );
  }

  Widget _countStrip(AsyncValue<AccountsListData> async) {
    final data = async.value;
    final String label;
    if (data == null) {
      label = async.hasError ? '' : 'Loading';
    } else {
      final unit = data.totalCount == 1 ? 'account' : 'accounts';
      label = data.accounts.length < data.totalCount
          ? '${data.accounts.length} of ${data.totalCount} $unit'
          : '${data.totalCount} $unit';
    }
    return Container(
      width: double.infinity,
      color: AppColors.surfaceDim,
      padding: const EdgeInsets.fromLTRB(12, 6, 12, 8),
      child: Text(
        label,
        style: AppTypography.caption.copyWith(color: AppColors.textSecondary),
      ),
    );
  }

  Widget _list(AsyncValue<AccountsListData> async) {
    return async.when(
      loading: () => const Center(child: CircularProgressIndicator()),
      error: (error, _) => _errorState(error),
      data: (data) {
        if (data.accounts.isEmpty) return _emptyState();
        return RefreshIndicator(
          onRefresh: () => ref.read(accountsProvider.notifier).refresh(),
          child: ListView.builder(
            controller: _scrollController,
            padding: const EdgeInsets.only(bottom: 96),
            itemCount: data.accounts.length + (data.hasMore ? 1 : 0),
            itemBuilder: (context, index) {
              if (index >= data.accounts.length) {
                return const Padding(
                  padding: EdgeInsets.all(16),
                  child: Center(child: CircularProgressIndicator()),
                );
              }
              return _AccountRow(account: data.accounts[index]);
            },
          ),
        );
      },
    );
  }

  Widget _emptyState() {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(LucideIcons.building2, size: 48, color: AppColors.gray300),
            const SizedBox(height: 12),
            Text(
              _searchController.text.isNotEmpty
                  ? 'No accounts match that search'
                  : _showClosed
                  ? 'No closed accounts'
                  : 'No accounts yet',
              style: AppTypography.body.copyWith(
                color: AppColors.textSecondary,
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _errorState(Object error) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(LucideIcons.alertCircle, size: 40, color: AppColors.danger500),
            const SizedBox(height: 12),
            Text(
              'Could not load accounts',
              style: AppTypography.body.copyWith(color: AppColors.textPrimary),
            ),
            const SizedBox(height: 16),
            FilledButton(
              onPressed: () => ref.read(accountsProvider.notifier).refresh(),
              child: const Text('Retry'),
            ),
          ],
        ),
      ),
    );
  }
}

class _AccountRow extends StatelessWidget {
  const _AccountRow({required this.account});

  final Account account;

  @override
  Widget build(BuildContext context) {
    final location = account.locationLine;
    return Material(
      color: AppColors.surface,
      child: InkWell(
        onTap: () => context.push('${AppRoutes.accounts}/${account.id}'),
        child: Container(
          decoration: BoxDecoration(
            border: Border(bottom: BorderSide(color: AppColors.gray200)),
          ),
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
          child: Row(
            children: [
              CircleAvatar(
                radius: 18,
                backgroundColor: AppColors.primary50,
                child: Text(
                  account.initials,
                  style: AppTypography.caption.copyWith(
                    color: AppColors.primary600,
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      account.name,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: AppTypography.body.copyWith(
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                    if (location != null) ...[
                      const SizedBox(height: 2),
                      Text(
                        location,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: AppTypography.caption.copyWith(
                          color: AppColors.textSecondary,
                        ),
                      ),
                    ],
                  ],
                ),
              ),
              Icon(
                LucideIcons.chevronRight,
                size: 18,
                color: AppColors.gray300,
              ),
            ],
          ),
        ),
      ),
    );
  }
}
