import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

import '../../config/api_config.dart';
import '../../core/theme/theme.dart';
import '../../data/models/contact.dart';
import '../../providers/contacts_provider.dart';
import '../../providers/csv_import_provider.dart';
import '../../routes/app_router.dart';
import '../../widgets/common/export_csv_button.dart';
import '../../widgets/forms/csv_import_sheet.dart';

/// The contacts list.
class ContactsListScreen extends ConsumerStatefulWidget {
  const ContactsListScreen({super.key});

  @override
  ConsumerState<ContactsListScreen> createState() => _ContactsListScreenState();
}

class _ContactsListScreenState extends ConsumerState<ContactsListScreen> {
  final _searchController = TextEditingController();
  final _scrollController = ScrollController();
  Timer? _debounce;

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
      ref.read(contactsProvider.notifier).loadMore();
    }
  }

  void _onSearchChanged(String value) {
    _debounce?.cancel();
    _debounce = Timer(const Duration(milliseconds: 300), () {
      ref.read(contactsProvider.notifier).search(value);
    });
  }

  @override
  Widget build(BuildContext context) {
    final async = ref.watch(contactsProvider);

    return Scaffold(
      backgroundColor: AppColors.surfaceDim,
      appBar: AppBar(
        title: const Text('Contacts'),
        backgroundColor: AppColors.surface,
        elevation: 0,
        scrolledUnderElevation: 1,
        actions: [
          // Offered to every member, as on the web; the import views answer
          // 403 for anyone without admin or sales access.
          IconButton(
            icon: const Icon(LucideIcons.upload),
            tooltip: 'Import from CSV',
            onPressed: () => showCsvImportSheet(
              context,
              CsvImportTarget.contacts,
              onImported: () => ref.read(contactsProvider.notifier).refresh(),
            ),
          ),
          ExportCsvButton(
            endpoint: ApiConfig.contactsExport,
            filePrefix: 'contacts',
            query: () async => ref.read(contactsProvider.notifier).filterQuery,
          ),
          IconButton(
            icon: const Icon(LucideIcons.plus),
            tooltip: 'New contact',
            onPressed: () => context.push(AppRoutes.contactCreate),
          ),
        ],
      ),
      body: Column(
        children: [
          Container(
            color: AppColors.surface,
            padding: const EdgeInsets.fromLTRB(12, 8, 12, 8),
            child: TextField(
              controller: _searchController,
              onChanged: _onSearchChanged,
              textInputAction: TextInputAction.search,
              decoration: const InputDecoration(
                hintText: 'Search contacts',
                prefixIcon: Icon(LucideIcons.search, size: 18),
                isDense: true,
                border: OutlineInputBorder(),
              ),
            ),
          ),
          _countStrip(async),
          Expanded(child: _list(async)),
        ],
      ),
    );
  }

  /// Says nothing until there is something to count. "0 contacts" over a
  /// spinner is indistinguishable from an org that has none.
  Widget _countStrip(AsyncValue<ContactsListData> async) {
    final data = async.value;
    final String label;
    if (data == null) {
      label = async.hasError ? '' : 'Loading';
    } else {
      final unit = data.totalCount == 1 ? 'contact' : 'contacts';
      label = data.contacts.length < data.totalCount
          ? '${data.contacts.length} of ${data.totalCount} $unit'
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

  Widget _list(AsyncValue<ContactsListData> async) {
    return async.when(
      loading: () => const Center(child: CircularProgressIndicator()),
      error: (error, _) => Center(
        child: Padding(
          padding: const EdgeInsets.all(32),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Icon(
                LucideIcons.alertCircle,
                size: 40,
                color: AppColors.danger500,
              ),
              const SizedBox(height: 12),
              Text('Could not load contacts', style: AppTypography.body),
              const SizedBox(height: 16),
              FilledButton(
                onPressed: () => ref.read(contactsProvider.notifier).refresh(),
                child: const Text('Retry'),
              ),
            ],
          ),
        ),
      ),
      data: (data) {
        if (data.contacts.isEmpty) {
          return Center(
            child: Padding(
              padding: const EdgeInsets.all(32),
              child: Column(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  Icon(LucideIcons.users, size: 48, color: AppColors.gray300),
                  const SizedBox(height: 12),
                  Text(
                    _searchController.text.isNotEmpty
                        ? 'No contacts match that search'
                        : 'No contacts yet',
                    style: AppTypography.body.copyWith(
                      color: AppColors.textSecondary,
                    ),
                  ),
                ],
              ),
            ),
          );
        }
        return RefreshIndicator(
          onRefresh: () => ref.read(contactsProvider.notifier).refresh(),
          child: ListView.builder(
            controller: _scrollController,
            padding: const EdgeInsets.only(bottom: 96),
            itemCount: data.contacts.length + (data.hasMore ? 1 : 0),
            itemBuilder: (context, index) {
              if (index >= data.contacts.length) {
                return const Padding(
                  padding: EdgeInsets.all(16),
                  child: Center(child: CircularProgressIndicator()),
                );
              }
              return _ContactRow(contact: data.contacts[index]);
            },
          ),
        );
      },
    );
  }
}

class _ContactRow extends StatelessWidget {
  const _ContactRow({required this.contact});

  final Contact contact;

  @override
  Widget build(BuildContext context) {
    final subtitle = contact.subtitle;
    return Material(
      color: AppColors.surface,
      child: InkWell(
        onTap: () => context.push('${AppRoutes.contacts}/${contact.id}'),
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
                  contact.initials,
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
                    Row(
                      children: [
                        Flexible(
                          child: Text(
                            contact.fullName,
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: AppTypography.body.copyWith(
                              fontWeight: FontWeight.w600,
                            ),
                          ),
                        ),
                        if (contact.doNotCall) ...[
                          const SizedBox(width: 6),
                          Icon(
                            LucideIcons.phoneOff,
                            size: 13,
                            color: AppColors.danger500,
                          ),
                        ],
                      ],
                    ),
                    if (subtitle != null) ...[
                      const SizedBox(height: 2),
                      Text(
                        subtitle,
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
