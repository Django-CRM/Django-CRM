import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

import '../../config/api_config.dart';
import '../../core/theme/theme.dart';
import '../../data/models/approval.dart';
import '../../providers/approvals_provider.dart';
import '../../services/api_service.dart';
import '../common/common.dart';

/// Per-ticket approval state: latest approval + actions.
///
/// Hidden when this org doesn't use Approvals (the panel only renders when
/// an existing approval row is found OR the user explicitly opts in via the
/// "Request approval" button surfaced lower on the screen).
class TicketApprovalPanel extends ConsumerStatefulWidget {
  final String ticketId;

  /// Whether this person may file a request: the ticket's write rule,
  /// reported as `comment_permission` (the API answers anyone else 403).
  /// Approving, rejecting and cancelling are the approval's own rules.
  final bool canRequest;

  const TicketApprovalPanel({
    super.key,
    required this.ticketId,
    required this.canRequest,
  });

  @override
  ConsumerState<TicketApprovalPanel> createState() =>
      _TicketApprovalPanelState();
}

class _TicketApprovalPanelState extends ConsumerState<TicketApprovalPanel> {
  final ApiService _api = ApiService();
  List<Approval> _approvals = const [];
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
        : const <Approval>[];
    if (!mounted) return;
    setState(() {
      _approvals = approvals;
      _isLoaded = true;
    });
  }

  Approval? get _latest =>
      _approvals.isEmpty ? null : _approvals.first; // ordered DESC by API

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
    final latest = _latest;
    if (latest == null) {
      return _shellCard(
        children: [
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 4),
            child: Text(
              'No approval requested yet.',
              style: AppTypography.body.copyWith(
                color: AppColors.textSecondary,
              ),
            ),
          ),
          if (widget.canRequest) ...[
            const SizedBox(height: 8),
            SizedBox(
              width: double.infinity,
              child: OutlinedButton.icon(
                onPressed: _isBusy ? null : _request,
                icon: const Icon(LucideIcons.shieldCheck, size: 16),
                label: const Text('Request approval'),
              ),
            ),
          ],
        ],
      );
    }

    return _shellCard(
      children: [
        Row(
          children: [
            StatusBadge(label: latest.state.label, color: latest.state.color),
            const Spacer(),
            if (latest.decidedAt != null)
              Text(
                _formatDate(latest.decidedAt!),
                style: AppTypography.caption.copyWith(
                  color: AppColors.textTertiary,
                ),
              ),
          ],
        ),
        if (latest.ruleSummary != null) ...[
          const SizedBox(height: 8),
          Text(
            'Rule: ${latest.ruleSummary!.name}',
            style: AppTypography.caption.copyWith(
              color: AppColors.textSecondary,
            ),
          ),
        ],
        if (latest.note != null && latest.note!.isNotEmpty) ...[
          const SizedBox(height: 8),
          Text(latest.note!, style: AppTypography.body),
        ],
        if (latest.reason != null && latest.reason!.isNotEmpty) ...[
          const SizedBox(height: 8),
          Container(
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
        const SizedBox(height: 12),
        if (latest.isPending)
          Row(
            children: [
              Expanded(
                child: FilledButton.icon(
                  onPressed: _isBusy ? null : () => _approve(latest),
                  icon: const Icon(LucideIcons.check, size: 16),
                  label: const Text('Approve'),
                  style: FilledButton.styleFrom(
                    backgroundColor: AppColors.success600,
                  ),
                ),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: OutlinedButton.icon(
                  onPressed: _isBusy ? null : () => _reject(latest),
                  icon: const Icon(LucideIcons.x, size: 16),
                  label: const Text('Reject'),
                  style: OutlinedButton.styleFrom(
                    foregroundColor: AppColors.danger600,
                  ),
                ),
              ),
              const SizedBox(width: 8),
              IconButton(
                tooltip: 'Cancel request',
                onPressed: _isBusy ? null : () => _cancel(latest),
                icon: const Icon(LucideIcons.minusCircle, size: 16),
              ),
            ],
          )
        else
          SizedBox(
            width: double.infinity,
            child: OutlinedButton.icon(
              onPressed: _isBusy ? null : _request,
              icon: const Icon(LucideIcons.refreshCw, size: 16),
              label: const Text('Request again'),
            ),
          ),
      ],
    );
  }

  Widget _shellCard({required List<Widget> children}) {
    return Container(
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
