import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

import '../../config/api_config.dart';
import '../../core/theme/theme.dart';
import '../../data/models/approval.dart';
import '../../providers/approvals_provider.dart';
import '../../services/api_service.dart';
import '../common/common.dart';

/// What the ticket's approval panel shows and offers, mirroring the web's
/// `routes/(app)/tickets/[id]/approval.js`.
///
/// Every input is the server's: [rule] is the detail's `approval_rule` (the
/// rule that gates closing the ticket, the one a request binds to), [canWrite]
/// is `comment_permission` (the write rule `request-approval/` takes), and each
/// approval carries its own `can_act` and `can_cancel`. Nothing is worked
/// out from the viewer's role.
///
/// A request is offered only where the API would take it: a rule gates the
/// ticket, the viewer may write to it, it is still open, and that rule has
/// neither a pending request (409) nor an approved one (the close is already
/// allowed). [approvals] is null when the list could not be loaded; then
/// nothing is offered, since a pending request cannot be ruled out.
({
  bool show,
  bool failed,
  Approval? latest,
  bool canRequest,
  bool canDecide,
  bool canWithdraw,
})
ticketApprovalView(
  List<Approval>? approvals,
  ApprovalRuleSummary? rule, {
  required bool canWrite,
  required bool isOpen,
}) {
  final rows = approvals ?? const <Approval>[];
  final latest = rows.isEmpty ? null : rows.first;
  final settled =
      rule != null &&
      rows.any(
        (a) =>
            a.ruleSummary?.id == rule.id &&
            (a.state == ApprovalState.pending ||
                a.state == ApprovalState.approved),
      );
  final pending = latest != null && latest.isPending;
  return (
    show: rule != null || rows.isNotEmpty,
    failed: approvals == null,
    latest: latest,
    canRequest:
        rule != null && approvals != null && canWrite && isOpen && !settled,
    // Only the newest row carries actions; older ones are history.
    canDecide: pending && latest.canAct,
    canWithdraw: pending && latest.canCancel,
  );
}

/// Per-ticket approval state: latest approval + actions.
///
/// Hidden when no rule gates the ticket and nobody has filed a request on it.
/// Carries its own top spacing so a hidden panel leaves no gap.
class TicketApprovalPanel extends ConsumerStatefulWidget {
  final String ticketId;

  /// The rule that gates closing this ticket, from the detail's
  /// `approval_rule`, or null.
  final ApprovalRuleSummary? approvalRule;

  /// The ticket's write rule, reported as `comment_permission` (the API
  /// answers anyone else 403 on a request). Approving, rejecting and
  /// withdrawing follow each approval's own `can_act` / `can_cancel`.
  final bool canWrite;

  /// Whether the ticket is still open; a closed one needs no approval.
  final bool isOpen;

  const TicketApprovalPanel({
    super.key,
    required this.ticketId,
    required this.approvalRule,
    required this.canWrite,
    required this.isOpen,
  });

  @override
  ConsumerState<TicketApprovalPanel> createState() =>
      _TicketApprovalPanelState();
}

class _TicketApprovalPanelState extends ConsumerState<TicketApprovalPanel> {
  final ApiService _api = ApiService();

  /// Null when the list could not be loaded.
  List<Approval>? _approvals = const [];
  bool _isBusy = false;
  bool _isLoaded = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  /// Direct fetch. Keeps the inbox screen's shared `approvalsProvider`
  /// state untouched when the user opens a ticket.
  Future<void> _load() async {
    final url = Uri.parse(ApiConfig.approvals)
        .replace(queryParameters: {'state': 'all', 'case': widget.ticketId})
        .toString();
    final response = await _api.get(url);
    final approvals = (response.success && response.data != null)
        ? ((response.data!['approvals'] as List<dynamic>? ?? [])
              .whereType<Map<String, dynamic>>()
              .map(Approval.fromJson)
              .toList())
        : null;
    if (!mounted) return;
    setState(() {
      _approvals = approvals;
      _isLoaded = true;
    });
  }

  Future<void> _request() async {
    final note = await _promptText(
      title: 'Request approval',
      hint: 'Optional note for the approver',
      okLabel: 'Send request',
    );
    if (note == null) return;
    setState(() => _isBusy = true);
    final res = await ref
        .read(approvalsProvider.notifier)
        .requestApproval(widget.ticketId, note: note);
    if (!mounted) return;
    setState(() => _isBusy = false);
    if (res.success) {
      await _load();
    } else {
      _showError(res.message ?? 'Failed to request approval');
    }
  }

  Future<void> _approve(Approval a) async {
    setState(() => _isBusy = true);
    final res = await ref.read(approvalsProvider.notifier).approve(a.id);
    if (!mounted) return;
    setState(() => _isBusy = false);
    if (res.success) {
      await _load();
    } else {
      _showError(res.message ?? 'Failed to approve');
    }
  }

  Future<void> _reject(Approval a) async {
    final reason = await _promptText(
      title: 'Reject approval',
      hint: 'Reason (required)',
      okLabel: 'Reject',
      required: true,
    );
    if (reason == null || reason.isEmpty) return;
    setState(() => _isBusy = true);
    final res = await ref.read(approvalsProvider.notifier).reject(a.id, reason);
    if (!mounted) return;
    setState(() => _isBusy = false);
    if (res.success) {
      await _load();
    } else {
      _showError(res.message ?? 'Failed to reject');
    }
  }

  Future<void> _cancel(Approval a) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Cancel approval request?'),
        content: const Text(
          'The approval request will be cancelled. You can request a new one later.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('Keep'),
          ),
          TextButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('Cancel request'),
          ),
        ],
      ),
    );
    if (ok != true) return;
    setState(() => _isBusy = true);
    final res = await ref.read(approvalsProvider.notifier).cancel(a.id);
    if (!mounted) return;
    setState(() => _isBusy = false);
    if (res.success) {
      await _load();
    } else {
      _showError(res.message ?? 'Failed to cancel');
    }
  }

  Future<String?> _promptText({
    required String title,
    required String hint,
    required String okLabel,
    bool required = false,
  }) async {
    final controller = TextEditingController();
    return showDialog<String?>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(title),
        content: TextField(
          controller: controller,
          autofocus: true,
          maxLines: 3,
          decoration: InputDecoration(hintText: hint),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, null),
            child: const Text('Cancel'),
          ),
          TextButton(
            onPressed: () {
              final v = controller.text.trim();
              if (required && v.isEmpty) return;
              Navigator.pop(ctx, v);
            },
            child: Text(okLabel),
          ),
        ],
      ),
    );
  }

  void _showError(String msg) {
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(msg),
        backgroundColor: AppColors.danger600,
        behavior: SnackBarBehavior.floating,
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    // Don't render anything until the first fetch settles, avoids flashing
    // an empty panel on every detail open.
    if (!_isLoaded) return const SizedBox.shrink();
    final rule = widget.approvalRule;
    final view = ticketApprovalView(
      _approvals,
      rule,
      canWrite: widget.canWrite,
      isOpen: widget.isOpen,
    );
    if (!view.show) return const SizedBox.shrink();
    final latest = view.latest;
    final secondary = AppTypography.body.copyWith(
      color: AppColors.textSecondary,
    );
    final caption = AppTypography.caption.copyWith(
      color: AppColors.textSecondary,
    );

    return _shellCard(
      children: [
        if (rule != null) ...[
          Text(
            'Closing this ticket needs approval under ${rule.name}.',
            style: secondary,
          ),
          const SizedBox(height: 8),
        ],
        if (view.failed)
          Text(
            'The approval requests could not be loaded. Nothing else on this '
            'ticket is affected.',
            style: secondary,
          )
        else if (latest == null)
          Text('No approval requested yet.', style: secondary)
        else ...[
          Wrap(
            spacing: 8,
            runSpacing: 4,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              StatusBadge(label: latest.state.label, color: latest.state.color),
              if (latest.decidedAt != null)
                Text(
                  _formatDate(latest.decidedAt!.toLocal()),
                  style: AppTypography.caption.copyWith(
                    color: AppColors.textTertiary,
                  ),
                ),
            ],
          ),
          if (latest.requestedBy != null) ...[
            const SizedBox(height: 8),
            Text('Requested by ${latest.requestedBy!.email}', style: caption),
          ],
          if (latest.approver != null && !latest.isPending) ...[
            const SizedBox(height: 4),
            Text(
              '${latest.state.label} by ${latest.approver!.email}',
              style: caption,
            ),
          ],
          if (latest.ruleSummary != null &&
              latest.ruleSummary!.id != rule?.id) ...[
            const SizedBox(height: 4),
            Text('Rule: ${latest.ruleSummary!.name}', style: caption),
          ],
          if (latest.note != null && latest.note!.isNotEmpty) ...[
            const SizedBox(height: 8),
            Text(latest.note!, style: AppTypography.body),
          ],
          if (latest.state == ApprovalState.rejected &&
              latest.reason != null &&
              latest.reason!.isNotEmpty) ...[
            const SizedBox(height: 8),
            Container(
              width: double.infinity,
              padding: const EdgeInsets.all(10),
              decoration: BoxDecoration(
                color: AppColors.danger50,
                borderRadius: BorderRadius.circular(8),
                border: Border.all(color: AppColors.danger200),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    'Rejection reason',
                    style: AppTypography.caption.copyWith(
                      color: AppColors.danger700,
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                  const SizedBox(height: 2),
                  Text(latest.reason!, style: AppTypography.body),
                ],
              ),
            ),
          ],
          if (latest.isPending && latest.isOwnRequest) ...[
            const SizedBox(height: 8),
            Text(
              'You asked for this, so another approver must decide it.',
              style: caption,
            ),
          ],
        ],
        if (view.canDecide || view.canWithdraw || view.canRequest) ...[
          const SizedBox(height: 12),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              if (view.canDecide) ...[
                FilledButton.icon(
                  onPressed: _isBusy ? null : () => _approve(latest!),
                  icon: const Icon(LucideIcons.check, size: 16),
                  label: const Text('Approve'),
                  style: FilledButton.styleFrom(
                    backgroundColor: AppColors.success600,
                    minimumSize: const Size(0, 44),
                  ),
                ),
                OutlinedButton.icon(
                  onPressed: _isBusy ? null : () => _reject(latest!),
                  icon: const Icon(LucideIcons.x, size: 16),
                  label: const Text('Reject'),
                  style: OutlinedButton.styleFrom(
                    foregroundColor: AppColors.danger600,
                    minimumSize: const Size(0, 44),
                  ),
                ),
              ],
              if (view.canWithdraw)
                OutlinedButton.icon(
                  onPressed: _isBusy ? null : () => _cancel(latest!),
                  icon: const Icon(LucideIcons.circleMinus, size: 16),
                  label: const Text('Withdraw request'),
                  style: OutlinedButton.styleFrom(
                    minimumSize: const Size(0, 44),
                  ),
                ),
              if (view.canRequest)
                OutlinedButton.icon(
                  onPressed: _isBusy ? null : _request,
                  icon: Icon(
                    latest == null
                        ? LucideIcons.shieldCheck
                        : LucideIcons.refreshCw,
                    size: 16,
                  ),
                  label: Text(
                    latest == null ? 'Request approval' : 'Request again',
                  ),
                  style: OutlinedButton.styleFrom(
                    minimumSize: const Size(0, 44),
                  ),
                ),
            ],
          ),
        ],
      ],
    );
  }

  Widget _shellCard({required List<Widget> children}) {
    final card = Container(
      width: double.infinity,
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: AppColors.surface,
        borderRadius: AppLayout.borderRadiusLg,
        border: Border.all(color: AppColors.border),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(LucideIcons.shieldCheck, size: 16, color: AppColors.gray600),
              const SizedBox(width: 8),
              Text(
                'APPROVAL',
                style: AppTypography.overline.copyWith(
                  color: AppColors.textSecondary,
                ),
              ),
            ],
          ),
          const SizedBox(height: 12),
          ...children,
        ],
      ),
    );
    return Padding(padding: const EdgeInsets.only(top: 16), child: card);
  }

  String _formatDate(DateTime t) {
    const months = [
      'Jan',
      'Feb',
      'Mar',
      'Apr',
      'May',
      'Jun',
      'Jul',
      'Aug',
      'Sep',
      'Oct',
      'Nov',
      'Dec',
    ];
    final hh = t.hour.toString().padLeft(2, '0');
    final mm = t.minute.toString().padLeft(2, '0');
    return '${months[t.month - 1]} ${t.day}, $hh:$mm';
  }
}
