import 'package:flutter/material.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

import '../../core/theme/theme.dart';
import '../../data/models/interaction.dart';

/// One row of a record's timeline: a comment, a file, an interaction or the
/// record's creation.
class TimelineEvent {
  const TimelineEvent({
    required this.icon,
    required this.tint,
    required this.ts,
    required this.actor,
    required this.summary,
    this.detail,
    this.body,
    this.onEdit,
    this.onDelete,
  });

  final IconData icon;
  final Color tint;
  final DateTime ts;
  final String actor;
  final String summary;

  /// A quieter line under the summary: an outcome, a length, a subject.
  final String? detail;
  final String? body;

  /// What the viewer may do with this row, as a menu beside the time. A row
  /// with neither shows no menu.
  final VoidCallback? onEdit;
  final VoidCallback? onDelete;

  factory TimelineEvent.interaction(Interaction i) {
    final enquiry = i.kind == InteractionKind.enquiry;
    return TimelineEvent(
      icon: switch (i.kind) {
        InteractionKind.call =>
          i.inbound ? LucideIcons.phoneIncoming : LucideIcons.phoneOutgoing,
        InteractionKind.meeting => LucideIcons.users,
        InteractionKind.email => LucideIcons.mail,
        InteractionKind.whatsapp => LucideIcons.messageCircle,
        InteractionKind.sms => LucideIcons.messageSquareText,
        InteractionKind.enquiry => LucideIcons.inbox,
        null => LucideIcons.activity,
      },
      tint: enquiry ? AppColors.success600 : AppColors.purple600,
      ts: i.occurredAt,
      actor: '',
      summary: i.title,
      detail: _join([
        i.loggedByName,
        CallOutcome.parse(i.outcome)?.label,
        i.durationText,
        i.subject,
      ]),
      body: i.body,
    );
  }

  static String? _join(List<String?> parts) {
    final kept = parts.where((p) => p != null && p.trim().isNotEmpty);
    return kept.isEmpty ? null : kept.join(' · ');
  }
}

class TimelineEventRow extends StatelessWidget {
  const TimelineEventRow({
    super.key,
    required this.event,
    required this.isLast,
  });

  final TimelineEvent event;
  final bool isLast;

  @override
  Widget build(BuildContext context) {
    final tint = event.tint;
    return IntrinsicHeight(
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Column(
            children: [
              Container(
                width: 28,
                height: 28,
                decoration: BoxDecoration(
                  color: tint.withValues(alpha: 0.12),
                  shape: BoxShape.circle,
                ),
                child: Icon(event.icon, size: 14, color: tint),
              ),
              if (!isLast)
                Expanded(child: Container(width: 2, color: AppColors.border)),
            ],
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Padding(
              padding: EdgeInsets.only(bottom: isLast ? 0 : 16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      Expanded(
                        child: Text.rich(
                          TextSpan(
                            style: AppTypography.body,
                            children: [
                              if (event.actor.isNotEmpty) ...[
                                TextSpan(
                                  text: event.actor,
                                  style: const TextStyle(
                                    fontWeight: FontWeight.w600,
                                  ),
                                ),
                                const TextSpan(text: ' '),
                              ],
                              TextSpan(text: event.summary),
                            ],
                          ),
                        ),
                      ),
                      const SizedBox(width: 8),
                      Text(
                        _relTime(event.ts),
                        style: AppTypography.caption.copyWith(
                          color: AppColors.textTertiary,
                        ),
                      ),
                      if (event.onEdit != null || event.onDelete != null)
                        PopupMenuButton<VoidCallback>(
                          tooltip: 'Note actions',
                          icon: Icon(
                            LucideIcons.ellipsis,
                            size: 18,
                            color: AppColors.textTertiary,
                          ),
                          constraints: const BoxConstraints(minWidth: 140),
                          onSelected: (action) => action(),
                          itemBuilder: (_) => [
                            if (event.onEdit != null)
                              PopupMenuItem(
                                value: event.onEdit,
                                child: const Text('Edit'),
                              ),
                            if (event.onDelete != null)
                              PopupMenuItem(
                                value: event.onDelete,
                                child: Text(
                                  'Delete',
                                  style: TextStyle(color: AppColors.danger600),
                                ),
                              ),
                          ],
                        ),
                    ],
                  ),
                  if (event.detail != null) ...[
                    const SizedBox(height: 2),
                    Text(
                      event.detail!,
                      style: AppTypography.caption.copyWith(
                        color: AppColors.textSecondary,
                      ),
                    ),
                  ],
                  if (event.body != null && event.body!.isNotEmpty) ...[
                    const SizedBox(height: 6),
                    Container(
                      padding: const EdgeInsets.all(12),
                      decoration: BoxDecoration(
                        color: AppColors.gray50,
                        borderRadius: AppLayout.borderRadiusMd,
                      ),
                      child: Text(
                        event.body!,
                        style: AppTypography.body.copyWith(
                          color: AppColors.textSecondary,
                          height: 1.5,
                        ),
                      ),
                    ),
                  ],
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }

  String _relTime(DateTime dt) {
    final d = DateTime.now().difference(dt);
    if (d.inDays > 30) return '${(d.inDays / 30).floor()}mo';
    if (d.inDays > 0) return '${d.inDays}d';
    if (d.inHours > 0) return '${d.inHours}h';
    if (d.inMinutes > 0) return '${d.inMinutes}m';
    return 'now';
  }
}

/// "Log activity" above a timeline, and a line saying when the timeline shows
/// only the newest of a record's interactions. Nothing on an API without them.
class InteractionsBar extends StatelessWidget {
  const InteractionsBar({super.key, required this.answer, required this.onLog});

  final RecordInteractions? answer;
  final VoidCallback onLog;

  @override
  Widget build(BuildContext context) {
    final answer = this.answer;
    if (answer == null || !answer.offered) return const SizedBox.shrink();
    final hidden = answer.count - answer.items.length;
    return Padding(
      padding: const EdgeInsets.only(bottom: 16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          OutlinedButton.icon(
            style: OutlinedButton.styleFrom(minimumSize: const Size(0, 44)),
            onPressed: onLog,
            icon: const Icon(LucideIcons.notebookPen, size: 16),
            label: const Text('Log activity'),
          ),
          if (hidden > 0) ...[
            const SizedBox(height: 8),
            Text(
              'Showing the newest ${answer.items.length} of '
              '${answer.count} activities.',
              style: AppTypography.caption.copyWith(
                color: AppColors.textSecondary,
              ),
            ),
          ],
        ],
      ),
    );
  }
}
