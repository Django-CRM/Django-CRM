import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:intl/intl.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

import '../../core/theme/theme.dart';
import '../../data/models/audit_entry.dart';
import '../../providers/audit_log_provider.dart';
import '../../providers/auth_provider.dart';
import '../../routes/app_router.dart';

/// The security audit log, mirroring `/settings/audit-log` on the web:
/// sign-ins, org switches, refused requests, and webhooks paused or turned
/// back on. Read-only.
///
/// **Admin-only to read.** The API answers a member 403, so the screen gates
/// on `isOrgAdminProvider` rather than issuing a request it knows will fail.
/// Tapping a person narrows the log to them, the one way to set that filter,
/// as on the web.
class AuditLogScreen extends ConsumerStatefulWidget {
  const AuditLogScreen({super.key});

  @override
  ConsumerState<AuditLogScreen> createState() => _AuditLogScreenState();
}

class _AuditLogScreenState extends ConsumerState<AuditLogScreen> {
  AuditLogQuery _query = auditLogFirstPage;
  String _actorLabel = '';

  /// A changed filter starts again from the first page.
  void _filter(AuditLogQuery next) => setState(
    () => _query = (
      eventType: next.eventType,
      actor: next.actor,
      from: next.from,
      to: next.to,
      offset: 0,
    ),
  );

  Future<void> _pickDay({required bool from}) async {
    final now = DateTime.now();
    final current = from ? _query.from : _query.to;
    final picked = await showDatePicker(
      context: context,
      initialDate: current ?? now,
      firstDate: DateTime(now.year - 5),
      lastDate: now,
    );
    if (picked == null || !mounted) return;
    final q = _query;
    _filter((
      eventType: q.eventType,
      actor: q.actor,
      from: from ? picked : q.from,
      to: from ? q.to : picked,
      offset: 0,
    ));
  }

  @override
  Widget build(BuildContext context) {
    final isAdmin = ref.watch(isOrgAdminProvider);
    return Scaffold(
      backgroundColor: AppColors.surfaceDim,
      appBar: AppBar(
        title: const Text('Audit log'),
        backgroundColor: AppColors.surface,
        elevation: 0,
        scrolledUnderElevation: 1,
      ),
      body: !isAdmin ? const _MemberNotice() : _body(),
    );
  }

  Widget _body() {
    final async = ref.watch(auditLogProvider(_query));
    final q = _query;
    final filtered =
        q.eventType != null ||
        q.actor != null ||
        q.from != null ||
        q.to != null;
    return RefreshIndicator(
      onRefresh: () => ref.refresh(auditLogProvider(_query).future),
      child: ListView(
        padding: const EdgeInsets.only(bottom: 48),
        children: [
          _Filters(
            query: q,
            eventTypes: async.value?.eventTypes ?? const [],
            onEventType: (value) => _filter((
              eventType: value,
              actor: q.actor,
              from: q.from,
              to: q.to,
              offset: 0,
            )),
            onFrom: () => _pickDay(from: true),
            onTo: () => _pickDay(from: false),
            onClear: filtered
                ? () {
                    _actorLabel = '';
                    _filter(auditLogFirstPage);
                  }
                : null,
          ),
          if (q.actor != null)
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 4, 16, 4),
              child: Wrap(
                crossAxisAlignment: WrapCrossAlignment.center,
                spacing: 8,
                children: [
                  Text(
                    'Showing $_actorLabel only.',
                    style: AppTypography.caption.copyWith(
                      color: AppColors.textSecondary,
                    ),
                  ),
                  TextButton(
                    style: TextButton.styleFrom(minimumSize: const Size(0, 44)),
                    onPressed: () => _filter((
                      eventType: q.eventType,
                      actor: null,
                      from: q.from,
                      to: q.to,
                      offset: 0,
                    )),
                    child: const Text('Show everyone'),
                  ),
                ],
              ),
            ),
          ...async.when(
            loading: () => const [
              Padding(
                padding: EdgeInsets.all(32),
                child: Center(child: CircularProgressIndicator()),
              ),
            ],
            error: (error, _) => [
              _Message(
                icon: LucideIcons.circleAlert,
                title: 'That did not work',
                body: error.toString().replaceFirst('Exception: ', ''),
                onRetry: () => ref.invalidate(auditLogProvider(_query)),
              ),
            ],
            data: (page) => [
              if (page.entries.isEmpty)
                _Message(
                  icon: LucideIcons.scrollText,
                  title: filtered ? 'Nothing matches' : 'Nothing recorded yet',
                  body: filtered
                      ? 'No entry matches these filters. Widen the dates or '
                            'clear them.'
                      : 'Sign-ins, org switches, refused requests and '
                            'webhook pauses appear here as they happen.',
                )
              else
                for (final entry in page.entries)
                  _EntryRow(
                    entry: entry,
                    onActor: entry.actorId == null
                        ? null
                        : () {
                            _actorLabel = entry.actorLabel;
                            _filter((
                              eventType: q.eventType,
                              actor: entry.actorId,
                              from: q.from,
                              to: q.to,
                              offset: 0,
                            ));
                          },
                  ),
              _Pager(
                offset: q.offset,
                count: page.count,
                onPage: (offset) => setState(
                  () => _query = (
                    eventType: q.eventType,
                    actor: q.actor,
                    from: q.from,
                    to: q.to,
                    offset: offset,
                  ),
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

class _Filters extends StatelessWidget {
  const _Filters({
    required this.query,
    required this.eventTypes,
    required this.onEventType,
    required this.onFrom,
    required this.onTo,
    required this.onClear,
  });

  final AuditLogQuery query;
  final List<AuditEventType> eventTypes;
  final ValueChanged<String?> onEventType;
  final VoidCallback onFrom;
  final VoidCallback onTo;
  final VoidCallback? onClear;

  static final _day = DateFormat('d MMM yyyy');

  @override
  Widget build(BuildContext context) {
    const size = Size(0, 48);
    // The types arrive with the first page. Until then a chosen type reads as
    // "All events", because a dropdown whose value is not among its items
    // throws. The key rebuilds the field once they arrive.
    final known = {for (final t in eventTypes) t.value};
    final selected = known.contains(query.eventType) ? query.eventType : null;
    return Container(
      color: AppColors.surface,
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 12),
      margin: const EdgeInsets.only(bottom: 1),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          DropdownButtonFormField<String?>(
            key: ValueKey('${selected ?? ''}|${eventTypes.length}'),
            initialValue: selected,
            isExpanded: true,
            decoration: const InputDecoration(labelText: 'Event'),
            items: [
              const DropdownMenuItem<String?>(
                value: null,
                child: Text('All events'),
              ),
              for (final t in eventTypes)
                DropdownMenuItem<String?>(
                  value: t.value,
                  child: Text(t.label, overflow: TextOverflow.ellipsis),
                ),
            ],
            onChanged: onEventType,
          ),
          const SizedBox(height: 10),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              OutlinedButton.icon(
                style: OutlinedButton.styleFrom(minimumSize: size),
                onPressed: onFrom,
                icon: const Icon(LucideIcons.calendar, size: 16),
                label: Text(
                  query.from == null
                      ? 'From'
                      : 'From ${_day.format(query.from!)}',
                ),
              ),
              OutlinedButton.icon(
                style: OutlinedButton.styleFrom(minimumSize: size),
                onPressed: onTo,
                icon: const Icon(LucideIcons.calendar, size: 16),
                label: Text(
                  query.to == null ? 'To' : 'To ${_day.format(query.to!)}',
                ),
              ),
              if (onClear != null)
                TextButton(
                  style: TextButton.styleFrom(minimumSize: size),
                  onPressed: onClear,
                  child: const Text('Clear'),
                ),
            ],
          ),
        ],
      ),
    );
  }
}

class _EntryRow extends StatelessWidget {
  const _EntryRow({required this.entry, required this.onActor});

  final AuditEntry entry;
  final VoidCallback? onActor;

  static final _when = DateFormat('d MMM yyyy, HH:mm');

  @override
  Widget build(BuildContext context) {
    final detail = entry.detail;
    final webhookId = entry.webhookId;
    final (label, color) = entry.success
        ? ('OK', AppColors.success600)
        : ('Refused', AppColors.danger600);
    final meta = [
      if (entry.createdAt != null) _when.format(entry.createdAt!.toLocal()),
      if (entry.ipAddress.isNotEmpty) entry.ipAddress,
    ].join(' · ');
    return Container(
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 8),
      decoration: BoxDecoration(
        color: AppColors.surface,
        border: Border(bottom: BorderSide(color: AppColors.gray100)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(
                child: Text(
                  entry.eventLabel,
                  style: AppTypography.body.copyWith(
                    fontWeight: FontWeight.w500,
                  ),
                ),
              ),
              const SizedBox(width: 10),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                decoration: BoxDecoration(
                  borderRadius: BorderRadius.circular(999),
                  border: Border.all(color: color.withValues(alpha: 0.5)),
                ),
                child: Text(
                  label,
                  style: AppTypography.caption.copyWith(
                    color: color,
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ),
            ],
          ),
          if (detail.isNotEmpty) ...[
            const SizedBox(height: 3),
            Text(
              detail,
              style: AppTypography.caption.copyWith(
                color: AppColors.textSecondary,
              ),
            ),
          ],
          if (meta.isNotEmpty) ...[
            const SizedBox(height: 3),
            Text(
              meta,
              style: AppTypography.caption.copyWith(
                color: AppColors.textTertiary,
              ),
            ),
          ],
          Wrap(
            spacing: 4,
            children: [
              TextButton.icon(
                style: TextButton.styleFrom(
                  minimumSize: const Size(0, 44),
                  padding: const EdgeInsets.symmetric(horizontal: 4),
                ),
                onPressed: onActor,
                icon: const Icon(LucideIcons.user, size: 14),
                label: Text(entry.actorLabel, overflow: TextOverflow.ellipsis),
              ),
              if (webhookId != null)
                TextButton(
                  style: TextButton.styleFrom(
                    minimumSize: const Size(0, 44),
                    padding: const EdgeInsets.symmetric(horizontal: 4),
                  ),
                  onPressed: () =>
                      context.push(AppRoutes.settingsWebhook(webhookId)),
                  child: const Text('Open webhook'),
                ),
            ],
          ),
        ],
      ),
    );
  }
}

class _Pager extends StatelessWidget {
  const _Pager({
    required this.offset,
    required this.count,
    required this.onPage,
  });

  final int offset;
  final int count;
  final ValueChanged<int> onPage;

  @override
  Widget build(BuildContext context) {
    final newer = offset > 0;
    final older = offset + auditLogPageSize < count;
    if (!newer && !older) return const SizedBox.shrink();
    const size = Size(0, 48);
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 0),
      child: Wrap(
        spacing: 8,
        children: [
          if (newer)
            OutlinedButton(
              style: OutlinedButton.styleFrom(minimumSize: size),
              onPressed: () =>
                  onPage((offset - auditLogPageSize).clamp(0, offset)),
              child: const Text('Newer'),
            ),
          if (older)
            OutlinedButton(
              style: OutlinedButton.styleFrom(minimumSize: size),
              onPressed: () => onPage(offset + auditLogPageSize),
              child: const Text('Older'),
            ),
        ],
      ),
    );
  }
}

class _Message extends StatelessWidget {
  const _Message({
    required this.icon,
    required this.title,
    required this.body,
    this.onRetry,
  });

  final IconData icon;
  final String title;
  final String body;
  final VoidCallback? onRetry;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(24, 32, 24, 16),
      child: Column(
        children: [
          Icon(icon, size: 36, color: AppColors.textTertiary),
          const SizedBox(height: 12),
          Text(title, style: AppTypography.h3, textAlign: TextAlign.center),
          const SizedBox(height: 8),
          Text(
            body,
            style: AppTypography.body.copyWith(color: AppColors.textSecondary),
            textAlign: TextAlign.center,
          ),
          if (onRetry != null) ...[
            const SizedBox(height: 12),
            OutlinedButton(
              style: OutlinedButton.styleFrom(minimumSize: const Size(0, 48)),
              onPressed: onRetry,
              child: const Text('Try again'),
            ),
          ],
        ],
      ),
    );
  }
}

class _MemberNotice extends StatelessWidget {
  const _MemberNotice();

  @override
  Widget build(BuildContext context) {
    return const _Message(
      icon: LucideIcons.lock,
      title: 'Administrators only',
      body:
          'The audit log records who signed in, from where, and what was '
          'refused, so only administrators can read it.',
    );
  }
}
