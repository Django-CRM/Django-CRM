import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:intl/intl.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

import '../../core/theme/theme.dart';
import '../../data/models/estimate.dart';
import '../../providers/invoice_extras_provider.dart';
import '../../routes/app_router.dart';
import '../../widgets/common/badge.dart';
import 'invoice_format.dart';
import 'invoice_shell.dart';

/// Estimates, the quotes worklist.
///
/// The job this screen does is separate "agreed but not yet billed" from
/// "already billed", which is why a converted estimate shows its invoice
/// number instead of a Convert button. Offering to convert twice is the
/// mistake the row is shaped to prevent, and the server refuses it anyway.
class EstimatesListScreen extends ConsumerWidget {
  const EstimatesListScreen({super.key});

  static const _filters = <EstimateStatus?>[
    null,
    EstimateStatus.draft,
    EstimateStatus.sent,
    EstimateStatus.accepted,
    EstimateStatus.declined,
  ];

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final async = ref.watch(estimatesProvider);
    final active = ref.watch(estimatesProvider.notifier).status;

    return Scaffold(
      backgroundColor: AppColors.surfaceDim,
      appBar: AppBar(
        title: const Text('Estimates'),
        backgroundColor: AppColors.surface,
        elevation: 0,
        scrolledUnderElevation: 1,
        actions: [
          IconButton(
            icon: const Icon(LucideIcons.plus),
            tooltip: 'New estimate',
            onPressed: () => context.push(AppRoutes.estimateNew),
          ),
        ],
      ),
      body: Column(
        children: [
          InvoiceFilterChips(
            labels: [for (final s in _filters) s?.label ?? 'All'],
            selected: _filters.indexOf(active),
            onSelected: (index) => ref
                .read(estimatesProvider.notifier)
                .filterByStatus(_filters[index]),
          ),
          Expanded(
            child: async.when(
              loading: () => const Center(child: CircularProgressIndicator()),
              error: (_, _) => InvoiceErrorState(
                message: 'Could not load estimates',
                onRetry: () => ref.read(estimatesProvider.notifier).refresh(),
              ),
              data: (estimates) {
                if (estimates.isEmpty) {
                  return const InvoiceEmptyState(
                    icon: LucideIcons.fileText,
                    message: 'No estimates here',
                  );
                }
                return RefreshIndicator(
                  onRefresh: () =>
                      ref.read(estimatesProvider.notifier).refresh(),
                  child: ListView.builder(
                    padding: const EdgeInsets.only(bottom: 96),
                    itemCount: estimates.length,
                    itemBuilder: (context, i) =>
                        _EstimateCard(estimate: estimates[i]),
                  ),
                );
              },
            ),
          ),
        ],
      ),
    );
  }
}

class _EstimateCard extends ConsumerStatefulWidget {
  const _EstimateCard({required this.estimate});

  final Estimate estimate;

  @override
  ConsumerState<_EstimateCard> createState() => _EstimateCardState();
}

class _EstimateCardState extends ConsumerState<_EstimateCard> {
  bool _busy = false;

  Future<void> _convert() async {
    setState(() => _busy = true);
    final result = await ref
        .read(estimatesProvider.notifier)
        .convert(widget.estimate.id);
    if (!mounted) return;
    setState(() => _busy = false);

    if (result.error != null) {
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(SnackBar(content: Text(result.error!)));
      return;
    }
    // Converting exists to produce an invoice, so land on it rather than
    // leaving the user to find it in a list of hundreds.
    final id = result.invoiceId;
    ScaffoldMessenger.of(
      context,
    ).showSnackBar(const SnackBar(content: Text('Invoice raised')));
    if (id != null && id.isNotEmpty) {
      context.push('${AppRoutes.invoices}/$id');
    }
  }

  Future<void> _send() async {
    setState(() => _busy = true);
    final error = await ref
        .read(estimatesProvider.notifier)
        .send(widget.estimate.id);
    if (!mounted) return;
    setState(() => _busy = false);
    ScaffoldMessenger.of(
      context,
    ).showSnackBar(SnackBar(content: Text(error ?? 'Estimate sent')));
  }

  @override
  Widget build(BuildContext context) {
    final estimate = widget.estimate;
    return Container(
      color: AppColors.surface,
      margin: const EdgeInsets.only(bottom: 1),
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      estimate.estimateNumber.isEmpty
                          ? estimate.title
                          : estimate.estimateNumber,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: AppTypography.body.copyWith(
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                    const SizedBox(height: 2),
                    Text(
                      estimate.accountName ??
                          (estimate.clientName.isEmpty
                              ? 'No account'
                              : estimate.clientName),
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: AppTypography.caption.copyWith(
                        color: AppColors.textSecondary,
                      ),
                    ),
                  ],
                ),
              ),
              const SizedBox(width: 12),
              Text(
                money(estimate.totalAmount, estimate.currencyInfo.symbol),
                style: AppTypography.body.copyWith(fontWeight: FontWeight.w600),
              ),
            ],
          ),
          const SizedBox(height: 8),
          Wrap(
            spacing: 8,
            runSpacing: 4,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              StatusBadge(
                label: estimate.statusLabel,
                color: estimate.statusColor,
              ),
              if (estimate.isConverted)
                StatusBadge(
                  label: 'billed ${estimate.convertedInvoiceNumber ?? ''}'
                      .trim(),
                  color: AppColors.success600,
                ),
              if (estimate.expiryDate != null && !estimate.isConverted)
                Text(
                  estimate.isExpired
                      ? 'expired'
                      : 'expires ${DateFormat('d MMM').format(estimate.expiryDate!)}',
                  style: AppTypography.caption.copyWith(
                    color: estimate.isExpired
                        ? AppColors.danger600
                        : AppColors.textSecondary,
                  ),
                ),
            ],
          ),
          if (estimate.canSend || estimate.canConvert) ...[
            const SizedBox(height: 10),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                if (estimate.canSend)
                  SizedBox(
                    height: 40,
                    child: OutlinedButton(
                      onPressed: _busy ? null : _send,
                      child: Text(
                        estimate.status == EstimateStatus.draft
                            ? 'Send'
                            : 'Send again',
                      ),
                    ),
                  ),
                if (estimate.canConvert)
                  SizedBox(
                    height: 40,
                    child: FilledButton(
                      onPressed: _busy ? null : _convert,
                      child: const Text('Raise an invoice'),
                    ),
                  ),
              ],
            ),
          ],
          if (estimate.isConverted && estimate.convertedInvoiceId != null) ...[
            const SizedBox(height: 10),
            SizedBox(
              height: 40,
              child: OutlinedButton(
                onPressed: () => context.push(
                  '${AppRoutes.invoices}/${estimate.convertedInvoiceId}',
                ),
                child: const Text('Open the invoice'),
              ),
            ),
          ],
        ],
      ),
    );
  }
}
