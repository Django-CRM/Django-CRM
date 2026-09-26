import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';
import '../../core/theme/theme.dart';
import '../../data/models/attachment.dart';
import '../../data/models/ticket.dart';
import '../../data/models/comment.dart';
import 'close_with_children_dialog.dart';
import 'macro_picker_sheet.dart';
import '../../data/models/email_message.dart';
import '../../data/models/org_settings.dart';
import '../../providers/auth_provider.dart';
import '../../providers/lookup_provider.dart';
import '../../providers/settings_provider.dart';
import '../../providers/tickets_provider.dart';
import '../../config/api_config.dart';
import '../../services/api_service.dart' show ApiResponse;
import '../../services/attachment_upload.dart';
import '../../data/models/lookup_models.dart';
import '../../widgets/common/common.dart';
import '../../widgets/forms/multi_select_sheet.dart';
import '../../widgets/tickets/ticket_approval_panel.dart';
import '../../widgets/tickets/ticket_properties_card.dart';
import '../../widgets/tickets/ticket_solutions_panel.dart';
import '../../widgets/tickets/ticket_time_panel.dart';

/// Ticket Detail Screen, Overview / Comments / Activity tabs.
class TicketDetailScreen extends ConsumerStatefulWidget {
  final String ticketId;

  const TicketDetailScreen({super.key, required this.ticketId});

  @override
  ConsumerState<TicketDetailScreen> createState() => _TicketDetailScreenState();
}

class _TicketDetailScreenState extends ConsumerState<TicketDetailScreen>
    with SingleTickerProviderStateMixin {
  late TabController _tabController;
  final TextEditingController _commentController = TextEditingController();

  TicketDetailResult? _detail;

  /// Other open tickets on the same account. Context beside the ticket, so a
  /// failure here leaves it empty rather than failing the screen.
  List<Ticket> _alsoOpen = const [];
  TicketWatchers? _watchers;
  TicketTreeNode? _tree;
  bool _isLoading = true;
  bool _isAddingComment = false;
  bool _isUploadingAttachment = false;
  _ThreadSegment _segment = _ThreadSegment.public;
  String? _error;

  @override
  void initState() {
    super.initState();
    _tabController = TabController(length: 4, vsync: this);
    _fetchDetail();
  }

  @override
  void dispose() {
    _tabController.dispose();
    _commentController.dispose();
    super.dispose();
  }

  Future<void> _fetchDetail() async {
    setState(() {
      _isLoading = true;
      _error = null;
    });
    final notifier = ref.read(ticketsProvider.notifier);
    final results = await Future.wait([
      notifier.getTicketDetail(widget.ticketId),
      notifier.getWatchers(widget.ticketId),
      notifier.fetchTree(widget.ticketId),
    ]);
    if (!mounted) return;
    final detail = results[0] as TicketDetailResult?;
    setState(() {
      _isLoading = false;
      _detail = detail;
      _watchers = results[1] as TicketWatchers?;
      _tree = results[2] as TicketTreeNode?;
      if (detail == null) _error = 'Failed to load ticket';
    });

    // Second request, after the ticket, because it needs the account id the
    // detail response carries. The web rail asks the same question the same
    // way; the phone showed nothing at all.
    final accountId = detail?.ticketObj.accountId;
    if (accountId == null || accountId.isEmpty) {
      if (mounted) setState(() => _alsoOpen = const []);
      return;
    }
    final siblings = await notifier.getAlsoOpenOnAccount(
      accountId: accountId,
      excludeTicketId: widget.ticketId,
    );
    if (mounted) setState(() => _alsoOpen = siblings);
  }

  @override
  Widget build(BuildContext context) {
    if (_isLoading) {
      return Scaffold(
        appBar: AppBar(
          leading: IconButton(
            icon: const Icon(LucideIcons.chevronLeft),
            onPressed: () => context.pop(),
          ),
        ),
        body: const Center(child: CircularProgressIndicator()),
      );
    }
    if (_detail == null || _error != null) {
      return Scaffold(
        appBar: AppBar(
          leading: IconButton(
            icon: const Icon(LucideIcons.chevronLeft),
            onPressed: () => context.pop(),
          ),
        ),
        body: Center(
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Icon(LucideIcons.fileX, size: 48, color: AppColors.gray400),
              const SizedBox(height: 16),
              Text('Ticket not found', style: AppTypography.h3),
              const SizedBox(height: 8),
              Text(
                _error ?? 'This ticket may have been deleted',
                style: AppTypography.body.copyWith(
                  color: AppColors.textSecondary,
                ),
              ),
              const SizedBox(height: 16),
              TextButton(onPressed: _fetchDetail, child: const Text('Retry')),
            ],
          ),
        ),
      );
    }

    final c = _detail!.ticketObj;

    return Scaffold(
      backgroundColor: AppColors.surface,
      appBar: AppBar(
        backgroundColor: AppColors.surface,
        elevation: 0,
        scrolledUnderElevation: 1,
        leading: IconButton(
          icon: const Icon(LucideIcons.chevronLeft),
          onPressed: () => context.pop(),
        ),
        title: Text(
          c.name,
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
          style: AppTypography.h3,
        ),
        actions: [
          IconButton(
            tooltip: (_watchers?.isCurrentUserWatching ?? false)
                ? 'Unwatch'
                : 'Watch',
            icon: Icon(
              (_watchers?.isCurrentUserWatching ?? false)
                  ? LucideIcons.eye
                  : LucideIcons.eyeOff,
              color: (_watchers?.isCurrentUserWatching ?? false)
                  ? AppColors.primary600
                  : AppColors.textSecondary,
            ),
            onPressed: _toggleWatch,
          ),
          IconButton(
            tooltip: 'Edit',
            icon: const Icon(LucideIcons.pencil),
            onPressed: () async {
              final result = await context.push(
                '/tickets/${widget.ticketId}/edit',
              );
              if (result == true && mounted) _fetchDetail();
            },
          ),
          IconButton(
            tooltip: 'More',
            icon: const Icon(LucideIcons.moreVertical),
            onPressed: _openActionSheet,
          ),
        ],
        bottom: TabBar(
          controller: _tabController,
          labelColor: AppColors.primary600,
          unselectedLabelColor: AppColors.textSecondary,
          indicatorColor: AppColors.primary600,
          indicatorWeight: 2,
          labelStyle: AppTypography.label,
          tabs: const [
            Tab(text: 'Overview'),
            Tab(text: 'Comments'),
            Tab(text: 'Files'),
            Tab(text: 'Activity'),
          ],
        ),
      ),
      body: Column(
        children: [
          _buildHeaderStrip(c),
          Expanded(
            child: TabBarView(
              controller: _tabController,
              children: [
                _buildOverview(c),
                _buildCommentsTab(c),
                _buildFilesTab(),
                _buildActivityTab(),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildHeaderStrip(Ticket c) {
    return Container(
      width: double.infinity,
      color: AppColors.surfaceDim,
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 12),
      child: Wrap(
        spacing: 8,
        runSpacing: 6,
        crossAxisAlignment: WrapCrossAlignment.center,
        children: [
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
            decoration: BoxDecoration(
              color: c.status.color.withValues(alpha: 0.12),
              borderRadius: BorderRadius.circular(6),
            ),
            child: Text(
              c.status.label,
              style: AppTypography.caption.copyWith(
                color: c.status.color,
                fontWeight: FontWeight.w600,
              ),
            ),
          ),
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
            decoration: BoxDecoration(
              color: c.priority.color.withValues(alpha: 0.12),
              borderRadius: BorderRadius.circular(6),
            ),
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                Icon(LucideIcons.flag, size: 12, color: c.priority.color),
                const SizedBox(width: 4),
                Text(
                  c.priority.label,
                  style: AppTypography.caption.copyWith(
                    color: c.priority.color,
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ],
            ),
          ),
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
            decoration: BoxDecoration(
              color: AppColors.gray100,
              borderRadius: BorderRadius.circular(6),
            ),
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                Icon(
                  c.ticketType.icon,
                  size: 12,
                  color: AppColors.textSecondary,
                ),
                const SizedBox(width: 4),
                Text(
                  c.ticketType.label,
                  style: AppTypography.caption.copyWith(
                    color: AppColors.textSecondary,
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ],
            ),
          ),
          if (c.isFirstResponseSlaBreached)
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
              decoration: BoxDecoration(
                color: AppColors.danger100,
                borderRadius: BorderRadius.circular(6),
              ),
              child: Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Icon(
                    LucideIcons.alertTriangle,
                    size: 12,
                    color: AppColors.danger600,
                  ),
                  const SizedBox(width: 4),
                  Text(
                    'SLA breached',
                    style: AppTypography.caption.copyWith(
                      color: AppColors.danger600,
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                ],
              ),
            ),
        ],
      ),
    );
  }

  Widget _buildOverview(Ticket c) {
    return SingleChildScrollView(
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          TicketPropertiesCard(
            ticket: c,
            watcherCount: _watchers?.count,
            isWatching: _watchers?.isCurrentUserWatching ?? false,
          ),
          if ((_detail?.mergedFromCases ?? const []).isNotEmpty) ...[
            const SizedBox(height: 16),
            _buildMergedFromCard(_detail!.mergedFromCases),
          ],
          if (_shouldShowTree(c)) ...[
            const SizedBox(height: 16),
            _buildTreeCard(c),
          ],
          const SizedBox(height: 16),
          TicketTimePanel(ticketId: c.id),
          const SizedBox(height: 16),
          TicketSolutionsPanel(
            ticketId: c.id,
            linked: _detail?.linkedSolutions ?? const [],
            onChanged: _fetchDetail,
          ),
          const SizedBox(height: 16),
          TicketApprovalPanel(ticketId: c.id),
          const SizedBox(height: 16),
          _card(
            title: 'Ticket Information',
            child: Column(
              children: [
                _InfoRow(
                  icon: LucideIcons.fileText,
                  label: 'NAME',
                  value: c.name,
                ),
                const Divider(height: 24),
                _InfoRow(
                  icon: LucideIcons.building2,
                  label: 'ACCOUNT',
                  value: c.accountName ?? 'No account linked',
                ),
                const Divider(height: 24),
                _InfoRow(
                  icon: LucideIcons.calendar,
                  label: 'CREATED',
                  value: _formatDate(c.createdAt),
                ),
                if (c.closedOn != null) ...[
                  const Divider(height: 24),
                  _InfoRow(
                    icon: LucideIcons.checkCircle,
                    label: 'CLOSED',
                    value: _formatDate(c.closedOn!),
                  ),
                ],
              ],
            ),
          ),
          const SizedBox(height: 16),
          _card(
            title: 'Assigned To',
            child: Row(
              children: [
                UserAvatar(name: c.assignedToName, size: AvatarSize.md),
                const SizedBox(width: 12),
                Expanded(
                  child: Text(c.assignedToName, style: AppTypography.label),
                ),
              ],
            ),
          ),
          if (c.contactNames.isNotEmpty) ...[
            const SizedBox(height: 16),
            _card(
              title: c.contactNames.length == 1
                  ? 'Reported by'
                  : '${c.contactNames.length} people on this ticket',
              child: Column(
                children: [
                  for (final name in c.contactNames)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 8),
                      child: Row(
                        children: [
                          UserAvatar(name: name, size: AvatarSize.sm),
                          const SizedBox(width: 12),
                          Expanded(
                            child: Text(name, style: AppTypography.label),
                          ),
                        ],
                      ),
                    ),
                ],
              ),
            ),
          ],
          if (c.description != null && c.description!.isNotEmpty) ...[
            const SizedBox(height: 16),
            _card(
              title: 'Description',
              child: Text(
                c.description!,
                style: AppTypography.body.copyWith(
                  color: AppColors.textSecondary,
                  height: 1.5,
                ),
              ),
            ),
          ],
          // The account's other open tickets. Empty when the ticket has no
          // account, or when it is the only one open, so the card appears only
          // when it has something to say.
          if (_alsoOpen.isNotEmpty) ...[
            const SizedBox(height: 16),
            _card(
              title: 'Also open on ${c.accountName ?? 'this account'}',
              child: Column(
                children: [
                  for (final sibling in _alsoOpen)
                    _AlsoOpenRow(
                      ticket: sibling,
                      // Same literal every other jump to a ticket uses in
                      // this file; there is no builder on AppRoutes for it.
                      onTap: () => context.push('/tickets/${sibling.id}'),
                    ),
                ],
              ),
            ),
          ],
          const SizedBox(height: 100),
        ],
      ),
    );
  }

  Widget _buildCommentsTab(Ticket c) {
    final internalIds = _detail?.internalCommentIds ?? const {};
    final allComments = c.comments;
    final emails = _detail?.emails ?? const [];

    final filteredComments = switch (_segment) {
      _ThreadSegment.public =>
        allComments.where((cm) => !internalIds.contains(cm.id)).toList(),
      _ThreadSegment.internal =>
        allComments.where((cm) => internalIds.contains(cm.id)).toList(),
      _ThreadSegment.emails => const <Comment>[],
    };

    final canCompose =
        (_detail?.commentPermission ?? false) &&
        _segment != _ThreadSegment.emails;

    return Column(
      children: [
        _buildThreadSegments(
          publicCount: allComments
              .where((cm) => !internalIds.contains(cm.id))
              .length,
          internalCount: allComments
              .where((cm) => internalIds.contains(cm.id))
              .length,
          emailsCount: emails.length,
        ),
        Expanded(
          child: _segment == _ThreadSegment.emails
              ? _buildEmailsList(emails)
              : _buildCommentsList(filteredComments, internalIds),
        ),
        if (canCompose) _buildCommentComposer(),
      ],
    );
  }

  Widget _buildThreadSegments({
    required int publicCount,
    required int internalCount,
    required int emailsCount,
  }) {
    Widget chip(_ThreadSegment seg, String label, IconData icon, int count) {
      final selected = _segment == seg;
      return Expanded(
        child: GestureDetector(
          onTap: () => setState(() => _segment = seg),
          child: Container(
            padding: const EdgeInsets.symmetric(vertical: 10),
            decoration: BoxDecoration(
              color: selected ? AppColors.surface : Colors.transparent,
              borderRadius: BorderRadius.circular(8),
              border: Border.all(
                color: selected ? AppColors.border : Colors.transparent,
              ),
            ),
            child: Row(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                Icon(
                  icon,
                  size: 14,
                  color: selected
                      ? AppColors.primary600
                      : AppColors.textSecondary,
                ),
                const SizedBox(width: 6),
                Text(
                  label,
                  style: AppTypography.caption.copyWith(
                    color: selected
                        ? AppColors.primary600
                        : AppColors.textSecondary,
                    fontWeight: FontWeight.w600,
                  ),
                ),
                if (count > 0) ...[
                  const SizedBox(width: 4),
                  Text(
                    '$count',
                    style: AppTypography.caption.copyWith(
                      color: selected
                          ? AppColors.primary600
                          : AppColors.textTertiary,
                    ),
                  ),
                ],
              ],
            ),
          ),
        ),
      );
    }

    return Container(
      margin: const EdgeInsets.fromLTRB(16, 12, 16, 8),
      padding: const EdgeInsets.all(4),
      decoration: BoxDecoration(
        color: AppColors.gray100,
        borderRadius: BorderRadius.circular(10),
      ),
      child: Row(
        children: [
          chip(
            _ThreadSegment.public,
            'Public',
            LucideIcons.messageSquare,
            publicCount,
          ),
          chip(
            _ThreadSegment.internal,
            'Internal',
            LucideIcons.lock,
            internalCount,
          ),
          chip(_ThreadSegment.emails, 'Emails', LucideIcons.mail, emailsCount),
        ],
      ),
    );
  }

  Widget _buildCommentsList(List<Comment> comments, Set<String> internalIds) {
    if (comments.isEmpty) {
      return EmptyState(
        icon: _segment == _ThreadSegment.internal
            ? LucideIcons.lock
            : LucideIcons.messageSquare,
        title: _segment == _ThreadSegment.internal
            ? 'No internal notes'
            : 'No comments yet',
        description: _segment == _ThreadSegment.internal
            ? 'Internal notes are visible only to your team.'
            : 'Add the first comment to get started.',
      );
    }
    return ListView.builder(
      padding: const EdgeInsets.fromLTRB(16, 4, 16, 16),
      itemCount: comments.length,
      itemBuilder: (context, i) {
        final comment = comments[i];
        return _CommentTile(
          comment: comment,
          isInternal: internalIds.contains(comment.id),
        );
      },
    );
  }

  Widget _buildEmailsList(List<EmailMessage> emails) {
    if (emails.isEmpty) {
      return const EmptyState(
        icon: LucideIcons.mail,
        title: 'No emails',
        description:
            'Inbound and outbound emails for this ticket will appear here.',
      );
    }
    return ListView.builder(
      padding: const EdgeInsets.fromLTRB(16, 4, 16, 16),
      itemCount: emails.length,
      itemBuilder: (context, i) => _EmailTile(email: emails[i]),
    );
  }

  Widget _buildCommentComposer() {
    final isInternal = _segment == _ThreadSegment.internal;
    return Container(
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: AppColors.surface,
        border: Border(top: BorderSide(color: AppColors.border)),
      ),
      child: SafeArea(
        child: Row(
          children: [
            IconButton(
              tooltip: 'Mention',
              icon: const Icon(LucideIcons.atSign, size: 20),
              color: AppColors.textSecondary,
              onPressed: _isAddingComment ? null : _pickMention,
            ),
            IconButton(
              tooltip: 'Saved reply',
              icon: const Icon(LucideIcons.messageSquareQuote, size: 20),
              color: AppColors.textSecondary,
              onPressed: _isAddingComment ? null : _pickMacro,
            ),
            Expanded(
              child: TextField(
                controller: _commentController,
                enabled: !_isAddingComment,
                decoration: InputDecoration(
                  hintText: isInternal
                      ? 'Add an internal note…'
                      : 'Email the customer…',
                  hintStyle: AppTypography.body.copyWith(
                    color: AppColors.textTertiary,
                  ),
                  filled: true,
                  fillColor: isInternal
                      ? AppColors.warning50
                      : AppColors.gray100,
                  contentPadding: const EdgeInsets.symmetric(
                    horizontal: 16,
                    vertical: 12,
                  ),
                  border: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(24),
                    borderSide: BorderSide.none,
                  ),
                ),
                maxLines: null,
              ),
            ),
            const SizedBox(width: 12),
            Container(
              decoration: BoxDecoration(
                color: _isAddingComment
                    ? AppColors.gray400
                    : (isInternal
                          ? AppColors.warning600
                          : AppColors.primary600),
                shape: BoxShape.circle,
              ),
              child: IconButton(
                icon: _isAddingComment
                    ? const SizedBox(
                        width: 20,
                        height: 20,
                        child: CircularProgressIndicator(
                          strokeWidth: 2,
                          color: Colors.white,
                        ),
                      )
                    : const Icon(
                        LucideIcons.send,
                        color: Colors.white,
                        size: 20,
                      ),
                onPressed: _isAddingComment ? null : _addComment,
              ),
            ),
          ],
        ),
      ),
    );
  }

  Future<void> _addComment() async {
    final text = _commentController.text.trim();
    if (text.isEmpty) return;
    final isInternal = _segment == _ThreadSegment.internal;
    setState(() => _isAddingComment = true);
    final response = await ref
        .read(ticketsProvider.notifier)
        .addComment(widget.ticketId, text, isInternal: isInternal);
    if (!mounted) return;

    if (!response.success) {
      setState(() => _isAddingComment = false);
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(response.message ?? 'Failed to add comment'),
          behavior: SnackBarBehavior.floating,
          backgroundColor: AppColors.danger600,
        ),
      );
      return;
    }

    // The POST response already returns the fresh `comments` and
    // `internal_notes` arrays, re-fold them into the existing _detail
    // instead of refetching the whole envelope + watchers + tree (which
    // is what made the screen feel like a full reload).
    final data = response.data;
    final current = _detail;
    if (data != null && current != null) {
      final tagged = <_TaggedCommentLocal>[];
      for (final c in (data['comments'] as List<dynamic>? ?? [])) {
        if (c is Map<String, dynamic>) {
          tagged.add(
            _TaggedCommentLocal(
              comment: Comment.fromJson(c),
              isInternal: false,
            ),
          );
        }
      }
      for (final c in (data['internal_notes'] as List<dynamic>? ?? [])) {
        if (c is Map<String, dynamic>) {
          tagged.add(
            _TaggedCommentLocal(comment: Comment.fromJson(c), isInternal: true),
          );
        }
      }
      tagged.sort(
        (a, b) => b.comment.commentedOn.compareTo(a.comment.commentedOn),
      );

      setState(() {
        _isAddingComment = false;
        _commentController.clear();
        _detail = TicketDetailResult(
          ticketObj: current.ticketObj.copyWith(
            comments: tagged.map((t) => t.comment).toList(),
          ),
          activities: current.activities,
          emails: current.emails,
          mergedFromCases: current.mergedFromCases,
          linkedSolutions: current.linkedSolutions,
          attachments: current.attachments,
          commentPermission: current.commentPermission,
          canMerge: current.canMerge,
          internalCommentIds: tagged
              .where((t) => t.isInternal)
              .map((t) => t.comment.id)
              .toSet(),
        );
      });
    } else {
      setState(() {
        _isAddingComment = false;
        _commentController.clear();
      });
    }
  }

  // ===========================================================================
  // FILES TAB
  // ===========================================================================

  /// Attachments on a ticket. `CaseDetailView` has always returned these and
  /// nothing on the phone read them, so a screenshot attached to a ticket, the
  /// single most useful thing on a support record, was invisible here.
  Widget _buildFilesTab() {
    final attachments = _detail?.attachments ?? const <Attachment>[];
    if (attachments.isEmpty) {
      return RefreshIndicator(
        onRefresh: _fetchDetail,
        child: ListView(
          physics: const AlwaysScrollableScrollPhysics(),
          children: [
            const SizedBox(height: 120),
            Center(
              child: Column(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  Icon(
                    LucideIcons.paperclip,
                    size: 48,
                    color: AppColors.gray400,
                  ),
                  const SizedBox(height: 16),
                  Text('No files attached', style: AppTypography.h3),
                  const SizedBox(height: 8),
                  Text(
                    'Attach a screenshot, a log, or a document.',
                    style: AppTypography.body.copyWith(
                      color: AppColors.textSecondary,
                    ),
                  ),
                  const SizedBox(height: 12),
                  AttachFileButton(
                    isUploading: _isUploadingAttachment,
                    onPressed: _pickAndUploadAttachment,
                  ),
                ],
              ),
            ),
          ],
        ),
      );
    }
    return RefreshIndicator(
      onRefresh: _fetchDetail,
      child: ListView.separated(
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.fromLTRB(16, 16, 16, 32),
        itemCount: attachments.length + 1,
        separatorBuilder: (_, _) => const SizedBox(height: 8),
        itemBuilder: (context, index) => index == attachments.length
            ? Align(
                alignment: Alignment.centerLeft,
                child: AttachFileButton(
                  isUploading: _isUploadingAttachment,
                  onPressed: _pickAndUploadAttachment,
                ),
              )
            : AttachmentTile(
                attachment: attachments[index],
                onOpen: _openAttachment,
              ),
      ),
    );
  }

  /// Download an attachment through the API, with the token attached. See the
  /// note on `Attachment.filePath` for why the stored path is not a URL.
  Future<void> _openAttachment(Attachment attachment) async {
    if (!attachment.hasFile) {
      _snack('That attachment has no file.');
      return;
    }
    final result = await downloadAttachment(
      url: ApiConfig.attachmentDownload(attachment.id),
      fileName: attachment.fileName,
    );
    if (!mounted || result.success) return;
    _snack(result.error ?? 'Could not download that file.');
  }

  /// Attach a file to this ticket.
  Future<void> _pickAndUploadAttachment() async {
    if (_isUploadingAttachment || _detail == null) return;
    setState(() => _isUploadingAttachment = true);

    final result = await pickAndUploadAttachment(
      target: AttachmentTarget.ticket,
      recordId: widget.ticketId,
    );

    if (!mounted) return;
    setState(() => _isUploadingAttachment = false);

    if (result.cancelled) return;
    if (!result.succeeded) {
      _snack(result.error!);
      return;
    }

    final attachmentsData = result.data?['attachments'];
    if (attachmentsData is List && _detail != null) {
      setState(() {
        _detail = _detail!.copyWith(
          attachments: attachmentsData
              .whereType<Map<String, dynamic>>()
              .map(Attachment.fromJson)
              .toList(),
        );
      });
    } else {
      await _fetchDetail();
    }
  }

  void _snack(String message) {
    if (!mounted) return;
    ScaffoldMessenger.of(
      context,
    ).showSnackBar(SnackBar(content: Text(message)));
  }

  Widget _buildActivityTab() {
    final activities = _detail?.activities ?? [];
    if (activities.isEmpty) {
      return const Center(
        child: EmptyState(
          icon: LucideIcons.clock,
          title: 'No activity yet',
          description: 'Status changes and assignments will appear here',
        ),
      );
    }
    return ListView.builder(
      padding: const EdgeInsets.all(16),
      itemCount: activities.length,
      itemBuilder: (context, i) {
        final a = activities[i];
        return _ActivityTile(activity: a);
      },
    );
  }

  Future<void> _pickMention() async {
    final users = ref.read(usersProvider);
    final picked = await showModalBottomSheet<UserLookup>(
      context: context,
      backgroundColor: AppColors.surface,
      isScrollControlled: true,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (_) => DraggableScrollableSheet(
        initialChildSize: 0.6,
        minChildSize: 0.3,
        maxChildSize: 0.95,
        expand: false,
        builder: (ctx, controller) => Column(
          children: [
            Container(
              margin: const EdgeInsets.only(top: 12),
              width: 40,
              height: 4,
              decoration: BoxDecoration(
                color: AppColors.gray300,
                borderRadius: BorderRadius.circular(2),
              ),
            ),
            Padding(
              padding: const EdgeInsets.all(16),
              child: Text('Mention someone', style: AppTypography.h3),
            ),
            Expanded(
              child: ListView.builder(
                controller: controller,
                itemCount: users.length,
                itemBuilder: (_, i) {
                  final u = users[i];
                  return InkWell(
                    onTap: () => Navigator.pop(context, u),
                    child: Padding(
                      padding: const EdgeInsets.symmetric(
                        horizontal: 16,
                        vertical: 12,
                      ),
                      child: Row(
                        children: [
                          UserAvatar(name: u.displayName, size: AvatarSize.xs),
                          const SizedBox(width: 12),
                          Expanded(
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Text(
                                  u.displayName,
                                  style: AppTypography.body.copyWith(
                                    fontWeight: FontWeight.w500,
                                  ),
                                ),
                                Text(
                                  u.email,
                                  style: AppTypography.caption.copyWith(
                                    color: AppColors.textSecondary,
                                  ),
                                ),
                              ],
                            ),
                          ),
                        ],
                      ),
                    ),
                  );
                },
              ),
            ),
          ],
        ),
      ),
    );
    if (picked == null) return;

    // Backend mention regex matches `@local-part` (the slug before "@").
    // Inserting "@$local " also auto-adds them as a watcher.
    final local = picked.email.split('@').first;
    final token = '@$local ';
    final text = _commentController.text;
    final sel = _commentController.selection;
    final insertAt = sel.isValid ? sel.start : text.length;
    final next = text.replaceRange(insertAt, insertAt, token);
    _commentController.value = TextEditingValue(
      text: next,
      selection: TextSelection.collapsed(offset: insertAt + token.length),
    );
  }

  /// Insert a saved reply, expanded against this ticket.
  ///
  /// Inserted at the cursor rather than replacing the box, so a macro can be
  /// dropped into a reply already part-written. Nothing is sent: the agent
  /// still reads it and presses send, which is the point of expanding it into
  /// the composer instead of posting it.
  Future<void> _pickMacro() async {
    final rendered = await showMacroPickerSheet(context, widget.ticketId);
    if (rendered == null || rendered.isEmpty || !mounted) return;

    final text = _commentController.text;
    final sel = _commentController.selection;
    final insertAt = sel.isValid ? sel.start : text.length;
    final insertEnd = sel.isValid ? sel.end : text.length;
    final next = text.replaceRange(insertAt, insertEnd, rendered);
    _commentController.value = TextEditingValue(
      text: next,
      selection: TextSelection.collapsed(offset: insertAt + rendered.length),
    );
  }

  Future<void> _toggleWatch() async {
    final current = _watchers?.isCurrentUserWatching ?? false;
    final ok = await ref
        .read(ticketsProvider.notifier)
        .setWatching(widget.ticketId, !current);
    if (!mounted) return;
    if (ok) {
      // Refetch watcher state instead of optimistic toggle so the count
      // stays accurate even if multiple subscribed-via paths flip.
      final fresh = await ref
          .read(ticketsProvider.notifier)
          .getWatchers(widget.ticketId);
      if (mounted) setState(() => _watchers = fresh);
    } else {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(current ? 'Failed to unwatch' : 'Failed to watch'),
          backgroundColor: AppColors.danger600,
          behavior: SnackBarBehavior.floating,
        ),
      );
    }
  }

  bool get _isAdmin {
    final org = ref.read(selectedOrgProvider);
    return (org?.role ?? '').toUpperCase() == 'ADMIN';
  }

  void _openActionSheet() {
    final c = _detail?.ticketObj;
    if (c == null) return;
    showModalBottomSheet(
      context: context,
      backgroundColor: AppColors.surface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (_) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Container(
              margin: const EdgeInsets.only(top: 12, bottom: 8),
              width: 40,
              height: 4,
              decoration: BoxDecoration(
                color: AppColors.gray300,
                borderRadius: BorderRadius.circular(2),
              ),
            ),
            _actionRow(
              icon: LucideIcons.users,
              label: 'Reassign',
              onTap: () {
                Navigator.pop(context);
                _reassign(c);
              },
            ),
            _actionRow(
              icon: LucideIcons.circleDot,
              label: 'Change status',
              onTap: () {
                Navigator.pop(context);
                _changeStatus(c);
              },
            ),
            _actionRow(
              icon: LucideIcons.flag,
              label: 'Change priority',
              onTap: () {
                Navigator.pop(context);
                _changePriority(c);
              },
            ),
            if (c.status != TicketStatus.closed)
              _actionRow(
                icon: LucideIcons.checkCircle,
                iconColor: AppColors.success600,
                label: 'Close ticket',
                onTap: () {
                  Navigator.pop(context);
                  _closeTicket(c);
                },
              ),
            // The server answers `can_merge` (admin or the ticket's creator)
            // and refuses the merge itself for anyone else.
            if (_detail?.canMerge == true)
              _actionRow(
                icon: LucideIcons.gitMerge,
                label: 'Merge into another ticket',
                onTap: () {
                  Navigator.pop(context);
                  _mergeInto(c);
                },
              ),
            _actionRow(
              icon: LucideIcons.link,
              label: c.parentSummary == null
                  ? 'Link to parent ticket'
                  : 'Change / detach parent',
              onTap: () {
                Navigator.pop(context);
                _linkParent(c);
              },
            ),
            if (c.childCount > 0 && c.status != TicketStatus.closed)
              _actionRow(
                icon: LucideIcons.checkCheck,
                iconColor: AppColors.success600,
                label: 'Close with children',
                onTap: () {
                  Navigator.pop(context);
                  _closeWithChildren(c);
                },
              ),
            if (_isAdmin)
              _actionRow(
                icon: LucideIcons.trash2,
                iconColor: AppColors.danger600,
                label: 'Delete ticket',
                labelColor: AppColors.danger600,
                onTap: () {
                  Navigator.pop(context);
                  _deleteTicket(c);
                },
              ),
            const SizedBox(height: 8),
          ],
        ),
      ),
    );
  }

  Widget _actionRow({
    required IconData icon,
    required String label,
    required VoidCallback onTap,
    Color? iconColor,
    Color? labelColor,
  }) {
    return InkWell(
      onTap: onTap,
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
        child: Row(
          children: [
            Icon(icon, size: 20, color: iconColor ?? AppColors.textSecondary),
            const SizedBox(width: 16),
            // Expanded so a long label wraps instead of overflowing: the
            // detach row names the parent, and a ticket subject has no limit
            // a 390px sheet can hold.
            Expanded(
              child: Text(
                label,
                style: AppTypography.body.copyWith(
                  color: labelColor ?? AppColors.textPrimary,
                  fontWeight: FontWeight.w500,
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Future<void> _reassign(Ticket c) async {
    final users = ref.read(usersProvider);
    final initial = users.where((u) => c.assignedToIds.contains(u.id)).toList();
    final result = await MultiSelectSheet.show<UserLookup>(
      context: context,
      title: 'Reassign ticket',
      items: users,
      initialSelection: initial,
      labelOf: (u) => u.displayName,
      searchText: (u) => '${u.email} ${u.displayName}',
      leadingOf: (u) => UserAvatar(name: u.displayName, size: AvatarSize.xs),
    );
    if (result == null) return;
    await _applyUpdate({
      'assigned_to': result.map((u) => u.id).toList(),
    }, 'Reassigned');
  }

  Future<void> _changeStatus(Ticket c) async {
    final picked = await _showEnumPicker<TicketStatus>(
      title: 'Change status',
      options: TicketStatus.settable,
      current: c.status,
      labelOf: (s) => s.label,
      colorOf: (s) => s.color,
    );
    if (picked == null || picked == c.status) return;

    final payload = <String, dynamic>{'status': picked.value};
    if (picked == TicketStatus.closed) {
      payload['closed_on'] = _todayIso();
    }
    await _applyUpdate(payload, 'Status updated');
  }

  Future<void> _changePriority(Ticket c) async {
    final picked = await _showEnumPicker<TicketPriority>(
      title: 'Change priority',
      options: TicketPriority.values,
      current: c.priority,
      labelOf: (p) => p.label,
      colorOf: (p) => p.color,
    );
    if (picked == null || picked == c.priority) return;
    await _applyUpdate({'priority': picked.value}, 'Priority updated');
  }

  Future<void> _closeTicket(Ticket c) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Close ticket?'),
        content: const Text(
          'The ticket will be marked Closed with today\'s date as the closed-on date.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('Cancel'),
          ),
          TextButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('Close ticket'),
          ),
        ],
      ),
    );
    if (confirmed != true) return;
    await _applyUpdate({
      'status': TicketStatus.closed.value,
      'closed_on': _todayIso(),
    }, 'Ticket closed');
  }

  Future<void> _mergeInto(Ticket c) async {
    // The candidates come from the server, which lists only the tickets this
    // merge would accept. The loaded list is not a safe source: it holds
    // tickets the caller did not create, which a non-admin's merge refuses.
    final tickets = ref.read(ticketsProvider.notifier);
    final target = await showModalBottomSheet<Ticket>(
      context: context,
      backgroundColor: AppColors.surface,
      isScrollControlled: true,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (_) => DraggableScrollableSheet(
        initialChildSize: 0.7,
        minChildSize: 0.4,
        maxChildSize: 0.95,
        expand: false,
        builder: (ctx, controller) => _TicketPickerSheet(
          title: 'Merge into…',
          controller: controller,
          search: (query) => tickets.mergeTargets(c.id, search: query),
        ),
      ),
    );
    if (target == null) return;
    if (!mounted) return;

    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Merge ticket?'),
        content: Text(
          '"${c.name}" will be marked Duplicate and its comments, '
          'attachments, and emails will move into "${target.name}". '
          'You can undo this from the target ticket.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('Cancel'),
          ),
          TextButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('Merge'),
          ),
        ],
      ),
    );
    if (confirmed != true) return;

    final res = await ref
        .read(ticketsProvider.notifier)
        .mergeInto(c.id, target.id);
    if (!mounted) return;
    if (res.success) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text('Merged into "${target.name}"'),
          behavior: SnackBarBehavior.floating,
        ),
      );
      // Source now redirects on detail fetch, pop back to the list so the
      // user isn't stuck on a now-redirecting ticket.
      context.pop(true);
    } else {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(res.message ?? 'Failed to merge'),
          backgroundColor: AppColors.danger600,
          behavior: SnackBarBehavior.floating,
        ),
      );
    }
  }

  Future<void> _unmergeSource(MergedFromSummary src) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Unmerge ticket?'),
        content: Text(
          '"${src.name}" will be restored and its comments, attachments, '
          'and emails moved back out of this ticket.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('Cancel'),
          ),
          TextButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('Unmerge'),
          ),
        ],
      ),
    );
    if (confirmed != true) return;

    final res = await ref.read(ticketsProvider.notifier).unmerge(src.id);
    if (!mounted) return;
    if (res.success) {
      await _fetchDetail();
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text('Unmerged "${src.name}"'),
            behavior: SnackBarBehavior.floating,
          ),
        );
      }
    } else {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(res.message ?? 'Failed to unmerge'),
          backgroundColor: AppColors.danger600,
          behavior: SnackBarBehavior.floating,
        ),
      );
    }
  }

  Future<void> _deleteTicket(Ticket c) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Delete ticket?'),
        content: Text(
          'This will soft-delete "${c.name}". You can restore it from the web admin.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('Cancel'),
          ),
          TextButton(
            style: TextButton.styleFrom(foregroundColor: AppColors.danger600),
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('Delete'),
          ),
        ],
      ),
    );
    if (confirmed != true) return;
    final response = await ref
        .read(ticketsProvider.notifier)
        .deleteTicket(widget.ticketId);
    if (!mounted) return;
    if (response.success) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text('Ticket deleted'),
          behavior: SnackBarBehavior.floating,
        ),
      );
      context.pop(true);
    } else {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(response.message ?? 'Failed to delete ticket'),
          backgroundColor: AppColors.danger600,
          behavior: SnackBarBehavior.floating,
        ),
      );
    }
  }

  Future<void> _applyUpdate(
    Map<String, dynamic> patch,
    String successMessage,
  ) async {
    final response = await ref
        .read(ticketsProvider.notifier)
        .updateTicket(widget.ticketId, patch);
    if (!mounted) return;
    if (response.success) {
      await _fetchDetail();
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text(successMessage),
            behavior: SnackBarBehavior.floating,
          ),
        );
      }
    } else {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(response.message ?? 'Failed to update ticket'),
          backgroundColor: AppColors.danger600,
          behavior: SnackBarBehavior.floating,
        ),
      );
    }
  }

  Future<T?> _showEnumPicker<T>({
    required String title,
    required List<T> options,
    required T current,
    required String Function(T) labelOf,
    Color Function(T)? colorOf,
  }) {
    return showModalBottomSheet<T>(
      context: context,
      backgroundColor: AppColors.surface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (_) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Container(
              margin: const EdgeInsets.only(top: 12),
              width: 40,
              height: 4,
              decoration: BoxDecoration(
                color: AppColors.gray300,
                borderRadius: BorderRadius.circular(2),
              ),
            ),
            Padding(
              padding: const EdgeInsets.all(16),
              child: Text(title, style: AppTypography.h3),
            ),
            for (final opt in options)
              InkWell(
                onTap: () => Navigator.pop(context, opt),
                child: Padding(
                  padding: const EdgeInsets.symmetric(
                    horizontal: 16,
                    vertical: 14,
                  ),
                  child: Row(
                    children: [
                      if (colorOf != null) ...[
                        Container(
                          width: 12,
                          height: 12,
                          decoration: BoxDecoration(
                            color: colorOf(opt),
                            shape: BoxShape.circle,
                          ),
                        ),
                        const SizedBox(width: 12),
                      ],
                      Expanded(
                        child: Text(
                          labelOf(opt),
                          style: AppTypography.body.copyWith(
                            fontWeight: opt == current
                                ? FontWeight.w600
                                : FontWeight.normal,
                            color: opt == current
                                ? AppColors.primary600
                                : AppColors.textPrimary,
                          ),
                        ),
                      ),
                      if (opt == current)
                        Icon(
                          LucideIcons.check,
                          size: 20,
                          color: AppColors.primary600,
                        ),
                    ],
                  ),
                ),
              ),
            const SizedBox(height: 16),
          ],
        ),
      ),
    );
  }

  String _todayIso() {
    final now = DateTime.now();
    return '${now.year.toString().padLeft(4, '0')}-'
        '${now.month.toString().padLeft(2, '0')}-'
        '${now.day.toString().padLeft(2, '0')}';
  }

  bool _shouldShowTree(Ticket c) {
    if (c.parentSummary != null) return true;
    if (c.childCount > 0) return true;
    if (c.isProblem) return true;
    return false;
  }

  Widget _buildTreeCard(Ticket c) {
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
              Icon(LucideIcons.gitBranch, size: 16, color: AppColors.gray600),
              const SizedBox(width: 8),
              Text(
                'LINKED TICKETS',
                style: AppTypography.overline.copyWith(
                  color: AppColors.textSecondary,
                ),
              ),
              if (c.isProblem) ...[
                const SizedBox(width: 8),
                Container(
                  padding: const EdgeInsets.symmetric(
                    horizontal: 6,
                    vertical: 2,
                  ),
                  decoration: BoxDecoration(
                    color: AppColors.danger100,
                    borderRadius: BorderRadius.circular(4),
                  ),
                  child: Text(
                    'PROBLEM',
                    style: AppTypography.caption.copyWith(
                      color: AppColors.danger700,
                      fontWeight: FontWeight.w700,
                      fontSize: 10,
                    ),
                  ),
                ),
              ],
            ],
          ),
          const SizedBox(height: 12),
          if (c.parentSummary != null)
            InkWell(
              // A parent the viewer cannot open would only answer 403.
              onTap: c.parentSummary!.restricted
                  ? null
                  : () => context.push('/tickets/${c.parentSummary!.id}'),
              child: Padding(
                padding: const EdgeInsets.symmetric(vertical: 6),
                child: Row(
                  children: [
                    Icon(
                      LucideIcons.arrowUp,
                      size: 14,
                      color: AppColors.gray500,
                    ),
                    const SizedBox(width: 8),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            'Parent',
                            style: AppTypography.caption.copyWith(
                              color: AppColors.textTertiary,
                            ),
                          ),
                          Text(
                            c.parentSummary!.name,
                            style: AppTypography.body.copyWith(
                              fontWeight: FontWeight.w500,
                              fontStyle: c.parentSummary!.restricted
                                  ? FontStyle.italic
                                  : FontStyle.normal,
                              color: c.parentSummary!.restricted
                                  ? AppColors.textTertiary
                                  : null,
                            ),
                          ),
                        ],
                      ),
                    ),
                    if (c.parentSummary!.status != null)
                      StatusBadge.fromTicketStatus(
                        TicketStatus.fromString(c.parentSummary!.status),
                      ),
                  ],
                ),
              ),
            ),
          if (_tree != null && _tree!.children.isNotEmpty) ...[
            const Divider(height: 20),
            // Tree shows up to PARENT_MAX_DEPTH (3). We render the root-down
            // view since the user opened a node inside it.
            ..._treeRows(_tree!, depth: 0),
            if (_tree!.truncated)
              Padding(
                padding: const EdgeInsets.only(top: 8),
                child: Text(
                  'Tree truncated (max depth reached)',
                  style: AppTypography.caption.copyWith(
                    color: AppColors.textTertiary,
                  ),
                ),
              ),
          ] else if (c.parentSummary == null) ...[
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 4),
              child: Text(
                '${c.childCount} linked ticket${c.childCount == 1 ? '' : 's'}',
                style: AppTypography.body.copyWith(
                  color: AppColors.textSecondary,
                ),
              ),
            ),
          ],
        ],
      ),
    );
  }

  List<Widget> _treeRows(TicketTreeNode node, {required int depth}) {
    final rows = <Widget>[];
    // Don't render the root again. It's the parent we already showed above.
    if (depth > 0) {
      rows.add(
        InkWell(
          // A ticket the viewer cannot open would only answer 403.
          onTap: node.restricted
              ? null
              : () => context.push('/tickets/${node.id}'),
          child: Padding(
            padding: EdgeInsets.fromLTRB((depth - 1) * 16.0, 6, 0, 6),
            child: Row(
              children: [
                Icon(
                  LucideIcons.cornerDownRight,
                  size: 14,
                  color: AppColors.gray400,
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    node.name,
                    style: AppTypography.body.copyWith(
                      fontWeight: node.isFocused
                          ? FontWeight.w700
                          : FontWeight.w500,
                      fontStyle: node.restricted
                          ? FontStyle.italic
                          : FontStyle.normal,
                      color: node.isFocused
                          ? AppColors.primary600
                          : node.restricted
                          ? AppColors.textTertiary
                          : AppColors.textPrimary,
                    ),
                  ),
                ),
                if (node.status != null)
                  StatusBadge.fromTicketStatus(
                    TicketStatus.fromString(node.status),
                  ),
              ],
            ),
          ),
        ),
      );
    }
    for (final child in node.children) {
      rows.addAll(_treeRows(child, depth: depth + 1));
    }
    return rows;
  }

  Future<void> _linkParent(Ticket c) async {
    // For "detach", offer the action up-front when a parent already exists.
    if (c.parentSummary != null) {
      final picked = await showModalBottomSheet<String>(
        context: context,
        backgroundColor: AppColors.surface,
        shape: const RoundedRectangleBorder(
          borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
        ),
        builder: (_) => SafeArea(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              const SizedBox(height: 12),
              Container(
                width: 40,
                height: 4,
                decoration: BoxDecoration(
                  color: AppColors.gray300,
                  borderRadius: BorderRadius.circular(2),
                ),
              ),
              Padding(
                padding: const EdgeInsets.all(16),
                child: Text('Parent ticket', style: AppTypography.h3),
              ),
              _actionRow(
                icon: LucideIcons.replace,
                label: 'Pick a different parent',
                onTap: () => Navigator.pop(context, 'pick'),
              ),
              _actionRow(
                icon: LucideIcons.unlink,
                iconColor: AppColors.danger600,
                // Unlinking needs write on this ticket only, so a hidden
                // parent can still be left; it just is not named.
                label: c.parentSummary!.restricted
                    ? 'Detach from parent ticket'
                    : 'Detach from "${c.parentSummary!.name}"',
                labelColor: AppColors.danger600,
                onTap: () => Navigator.pop(context, 'detach'),
              ),
              const SizedBox(height: 8),
            ],
          ),
        ),
      );
      if (picked == 'detach') {
        await _applyLink(c.id, null, 'Detached from parent');
        return;
      }
      if (picked != 'pick') return;
    }

    if (!mounted) return;
    final candidates = ref
        .read(ticketsListProvider)
        .where(
          (t) =>
              t.id != c.id &&
              t.status != TicketStatus.duplicate &&
              t.id != c.parentSummary?.id,
        )
        .toList();
    if (candidates.isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text(
            'No other tickets to link to. Load more from the list first.',
          ),
          behavior: SnackBarBehavior.floating,
        ),
      );
      return;
    }
    final parent = await showModalBottomSheet<Ticket>(
      context: context,
      backgroundColor: AppColors.surface,
      isScrollControlled: true,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (_) => DraggableScrollableSheet(
        initialChildSize: 0.7,
        minChildSize: 0.4,
        maxChildSize: 0.95,
        expand: false,
        builder: (ctx, controller) => _TicketPickerSheet(
          title: 'Link to parent…',
          tickets: candidates,
          controller: controller,
        ),
      ),
    );
    if (parent == null) return;
    if (!mounted) return;
    await _applyLink(c.id, parent.id, 'Linked to "${parent.name}"');
  }

  Future<void> _applyLink(
    String id,
    String? parentId,
    String successMessage,
  ) async {
    final res = await ref
        .read(ticketsProvider.notifier)
        .linkParent(id, parentId);
    if (!mounted) return;
    if (res.success) {
      await _fetchDetail();
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text(successMessage),
            behavior: SnackBarBehavior.floating,
          ),
        );
      }
    } else {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(res.message ?? 'Failed to link parent'),
          backgroundColor: AppColors.danger600,
          behavior: SnackBarBehavior.floating,
        ),
      );
    }
  }

  Future<void> _closeWithChildren(Ticket c) async {
    // **The org setting decides how this prompt starts.** That is the whole of
    // what `Org.auto_close_children_on_parent_close` does for a person:
    // `CaseCloseWithChildrenView` reads it only when the caller omits
    // `cascade`, and this app always sends it, so hardcoding `true` here made
    // the setting inert. The settings screen says "starts ticked"/"starts
    // unticked"; this is the line that has to make that true.
    final orgSettings = await ref
        .read(orgSettingsProvider.future)
        .catchError(
          // A settings read that fails must not block closing a ticket. Falling
          // back to the model default (false) is the safer of the two: it offers
          // not to close children rather than offering to.
          (_) => const OrgSettings(),
        );
    if (!mounted) return;

    // The tickets that would actually close, from the tree this screen already
    // loaded. `childCount` was what this used to pass, and it counts direct
    // children whether open or closed, so the prompt could offer to close
    // three tickets that were closed last week and stay silent about an open
    // grandchild that was about to close.
    final openBelow = _tree?.openDescendantsOf(c.id) ?? const [];

    final choice = await showCloseWithChildrenDialog(
      context,
      ticketName: c.name,
      openNames: [for (final node in openBelow) node.name],
      startsTicked: orgSettings.autoCloseChildren,
      truncated: _tree?.subtreeTruncatedFor(c.id) ?? false,
    );
    if (choice == null || !mounted) return;
    final cascade = choice.cascade;

    final res = await ref
        .read(ticketsProvider.notifier)
        .closeWithChildren(
          c.id,
          cascade: cascade,
          resolutionComment: choice.comment,
        );
    if (!mounted) return;
    if (res.success) {
      await _fetchDetail();
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text(
              cascadeCloseMessage(
                cascade: cascade,
                cascaded: cascadedCount(res.data),
              ),
            ),
            behavior: SnackBarBehavior.floating,
          ),
        );
      }
    } else {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(res.message ?? 'Failed to close'),
          backgroundColor: AppColors.danger600,
          behavior: SnackBarBehavior.floating,
        ),
      );
    }
  }

  Widget _buildMergedFromCard(List<MergedFromSummary> sources) {
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
              Icon(LucideIcons.gitMerge, size: 16, color: AppColors.gray600),
              const SizedBox(width: 8),
              Text(
                'MERGED FROM',
                style: AppTypography.overline.copyWith(
                  color: AppColors.textSecondary,
                ),
              ),
            ],
          ),
          const SizedBox(height: 8),
          for (final src in sources) ...[
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 4),
              child: Row(
                children: [
                  Expanded(
                    child: Text(
                      src.name,
                      style: AppTypography.body.copyWith(
                        fontWeight: FontWeight.w500,
                      ),
                    ),
                  ),
                  // Offered only where the server says this caller may
                  // unmerge (admin, or creator of both tickets).
                  if (src.canUnmerge)
                    TextButton(
                      // Themed buttons carry an infinite minimum width, which a
                      // Row does not bound. Without this the row fails to lay
                      // out and the screen paints nothing. See
                      // AppLayout.buttonMinSizeInRow.
                      style: TextButton.styleFrom(
                        minimumSize: AppLayout.buttonMinSizeInRow,
                      ),
                      onPressed: () => _unmergeSource(src),
                      child: const Text('Unmerge'),
                    ),
                ],
              ),
            ),
          ],
        ],
      ),
    );
  }

  Widget _card({required String title, required Widget child}) {
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
          Text(
            title,
            style: AppTypography.overline.copyWith(
              color: AppColors.textSecondary,
            ),
          ),
          const SizedBox(height: 12),
          child,
        ],
      ),
    );
  }

  String _formatDate(DateTime date) {
    final months = [
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
    return '${months[date.month - 1]} ${date.day}, ${date.year}';
  }
}

class _InfoRow extends StatelessWidget {
  final IconData icon;
  final String label;
  final String value;

  const _InfoRow({
    required this.icon,
    required this.label,
    required this.value,
  });

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        Icon(icon, size: 20, color: AppColors.gray400),
        const SizedBox(width: 12),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                label,
                style: AppTypography.overline.copyWith(
                  color: AppColors.textTertiary,
                  fontSize: 10,
                ),
              ),
              const SizedBox(height: 2),
              Text(value, style: AppTypography.body),
            ],
          ),
        ),
      ],
    );
  }
}

class _CommentTile extends StatelessWidget {
  final Comment comment;
  final bool isInternal;

  const _CommentTile({required this.comment, required this.isInternal});

  @override
  Widget build(BuildContext context) {
    return Container(
      margin: const EdgeInsets.only(bottom: 12),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: isInternal ? AppColors.warning50 : AppColors.gray50,
        borderRadius: AppLayout.borderRadiusLg,
        border: Border.all(
          color: isInternal ? AppColors.warning200 : AppColors.border,
        ),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (isInternal) ...[
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
              decoration: BoxDecoration(
                color: AppColors.warning100,
                borderRadius: BorderRadius.circular(4),
              ),
              child: Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Icon(LucideIcons.lock, size: 11, color: AppColors.warning700),
                  const SizedBox(width: 3),
                  Text(
                    'Internal',
                    style: AppTypography.caption.copyWith(
                      color: AppColors.warning700,
                      fontWeight: FontWeight.w600,
                      fontSize: 11,
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 6),
          ],
          Text(
            comment.comment,
            style: AppTypography.body.copyWith(
              color: AppColors.textPrimary,
              height: 1.5,
            ),
          ),
          const SizedBox(height: 8),
          Row(
            children: [
              UserAvatar(name: comment.authorName, size: AvatarSize.xs),
              const SizedBox(width: 8),
              Text(
                comment.authorName,
                style: AppTypography.caption.copyWith(
                  fontWeight: FontWeight.w500,
                ),
              ),
              const SizedBox(width: 8),
              Text(
                _formatTimeAgo(comment.commentedOn),
                style: AppTypography.caption.copyWith(
                  color: AppColors.textTertiary,
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }

  String _formatTimeAgo(DateTime dateTime) {
    final now = DateTime.now();
    final difference = now.difference(dateTime);
    if (difference.inDays > 30) {
      return '${(difference.inDays / 30).floor()}mo ago';
    } else if (difference.inDays > 0) {
      return '${difference.inDays}d ago';
    } else if (difference.inHours > 0) {
      return '${difference.inHours}h ago';
    } else if (difference.inMinutes > 0) {
      return '${difference.inMinutes}m ago';
    } else {
      return 'Just now';
    }
  }
}

enum _ThreadSegment { public, internal, emails }

class _TaggedCommentLocal {
  final Comment comment;
  final bool isInternal;
  const _TaggedCommentLocal({required this.comment, required this.isInternal});
}

class _TicketPickerSheet extends StatefulWidget {
  final String title;

  /// Tickets filtered here, by name or account, as the user types.
  final List<Ticket> tickets;

  /// When set, the list comes from the server instead: called with the query
  /// (debounced while typing) and shown as answered, never filtered here.
  final Future<ApiResponse<List<Ticket>>> Function(String query)? search;
  final ScrollController controller;
  const _TicketPickerSheet({
    required this.title,
    required this.controller,
    this.tickets = const [],
    this.search,
  });

  @override
  State<_TicketPickerSheet> createState() => _TicketPickerSheetState();
}

class _TicketPickerSheetState extends State<_TicketPickerSheet> {
  String _query = '';
  Timer? _debounce;

  /// Bumped per server search, so a slow answer to an older query is dropped
  /// rather than replacing a newer one.
  int _searchSeq = 0;
  bool _searching = false;
  String? _searchError;
  List<Ticket> _results = const [];

  @override
  void initState() {
    super.initState();
    if (widget.search != null) _runSearch('');
  }

  @override
  void dispose() {
    _debounce?.cancel();
    super.dispose();
  }

  void _onQueryChanged(String value) {
    setState(() => _query = value);
    if (widget.search == null) return;
    _debounce?.cancel();
    _debounce = Timer(
      const Duration(milliseconds: 300),
      () => _runSearch(value),
    );
  }

  Future<void> _runSearch(String query) async {
    final seq = ++_searchSeq;
    setState(() => _searching = true);
    final response = await widget.search!(query);
    if (!mounted || seq != _searchSeq) return;
    setState(() {
      _searching = false;
      _searchError = response.success ? null : response.message;
      _results = response.data ?? const [];
    });
  }

  @override
  Widget build(BuildContext context) {
    final q = _query.toLowerCase();
    final filtered = widget.search != null
        ? _results
        : q.isEmpty
        ? widget.tickets
        : widget.tickets
              .where(
                (t) =>
                    t.name.toLowerCase().contains(q) ||
                    (t.accountName?.toLowerCase().contains(q) ?? false),
              )
              .toList();
    final Widget? placeholder = filtered.isNotEmpty
        ? null
        : _searching
        ? const CircularProgressIndicator()
        : Padding(
            padding: const EdgeInsets.symmetric(horizontal: 24),
            child: Text(
              _searchError ?? 'No matching tickets',
              textAlign: TextAlign.center,
              style: AppTypography.body.copyWith(
                color: AppColors.textSecondary,
              ),
            ),
          );
    return Column(
      children: [
        Container(
          margin: const EdgeInsets.only(top: 12),
          width: 40,
          height: 4,
          decoration: BoxDecoration(
            color: AppColors.gray300,
            borderRadius: BorderRadius.circular(2),
          ),
        ),
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 16, 16, 8),
          child: Text(widget.title, style: AppTypography.h3),
        ),
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 0, 16, 12),
          child: TextField(
            autofocus: true,
            decoration: InputDecoration(
              hintText: 'Search tickets…',
              prefixIcon: const Icon(LucideIcons.search, size: 18),
              filled: true,
              fillColor: AppColors.gray50,
              contentPadding: const EdgeInsets.symmetric(vertical: 8),
              border: OutlineInputBorder(
                borderRadius: BorderRadius.circular(10),
                borderSide: BorderSide(color: AppColors.border),
              ),
            ),
            onChanged: _onQueryChanged,
          ),
        ),
        Expanded(
          child: placeholder != null
              ? Center(child: placeholder)
              : ListView.builder(
                  controller: widget.controller,
                  itemCount: filtered.length,
                  itemBuilder: (_, i) {
                    final t = filtered[i];
                    return InkWell(
                      onTap: () => Navigator.pop(context, t),
                      child: Padding(
                        padding: const EdgeInsets.symmetric(
                          horizontal: 16,
                          vertical: 12,
                        ),
                        child: Row(
                          children: [
                            Container(
                              width: 8,
                              height: 8,
                              decoration: BoxDecoration(
                                color: t.priority.color,
                                shape: BoxShape.circle,
                              ),
                            ),
                            const SizedBox(width: 12),
                            Expanded(
                              child: Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  Text(
                                    t.name,
                                    style: AppTypography.body.copyWith(
                                      fontWeight: FontWeight.w500,
                                    ),
                                  ),
                                  if (t.accountName != null)
                                    Text(
                                      t.accountName!,
                                      style: AppTypography.caption.copyWith(
                                        color: AppColors.textSecondary,
                                      ),
                                    ),
                                ],
                              ),
                            ),
                            StatusBadge.fromTicketStatus(t.status),
                          ],
                        ),
                      ),
                    );
                  },
                ),
        ),
      ],
    );
  }
}

class _EmailTile extends StatelessWidget {
  final EmailMessage email;
  const _EmailTile({required this.email});

  @override
  Widget build(BuildContext context) {
    final isInbound = email.isInbound;
    final timestamp = email.receivedAt ?? email.createdAt;
    return Container(
      margin: const EdgeInsets.only(bottom: 12),
      padding: const EdgeInsets.all(12),
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
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                decoration: BoxDecoration(
                  color: isInbound
                      ? AppColors.primary100
                      : AppColors.success100,
                  borderRadius: BorderRadius.circular(4),
                ),
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Icon(
                      isInbound
                          ? LucideIcons.arrowDownLeft
                          : LucideIcons.arrowUpRight,
                      size: 11,
                      color: isInbound
                          ? AppColors.primary700
                          : AppColors.success700,
                    ),
                    const SizedBox(width: 3),
                    Text(
                      isInbound ? 'Inbound' : 'Outbound',
                      style: AppTypography.caption.copyWith(
                        color: isInbound
                            ? AppColors.primary700
                            : AppColors.success700,
                        fontWeight: FontWeight.w600,
                        fontSize: 11,
                      ),
                    ),
                  ],
                ),
              ),
              const Spacer(),
              if (timestamp != null)
                Text(
                  _formatTimeAgo(timestamp),
                  style: AppTypography.caption.copyWith(
                    color: AppColors.textTertiary,
                  ),
                ),
            ],
          ),
          const SizedBox(height: 8),
          if (email.subject != null && email.subject!.isNotEmpty) ...[
            Text(
              email.subject!,
              style: AppTypography.label.copyWith(fontWeight: FontWeight.w600),
            ),
            const SizedBox(height: 4),
          ],
          if (email.fromAddress != null && email.fromAddress!.isNotEmpty)
            Text(
              'From: ${email.fromAddress}',
              style: AppTypography.caption.copyWith(
                color: AppColors.textSecondary,
              ),
            ),
          if (email.toAddresses.isNotEmpty)
            Text(
              'To: ${email.toAddresses.join(', ')}',
              style: AppTypography.caption.copyWith(
                color: AppColors.textSecondary,
              ),
            ),
          if (email.bodyText != null && email.bodyText!.isNotEmpty) ...[
            const SizedBox(height: 8),
            Text(
              email.bodyText!,
              style: AppTypography.body.copyWith(
                color: AppColors.textPrimary,
                height: 1.5,
              ),
              maxLines: 6,
              overflow: TextOverflow.ellipsis,
            ),
          ],
        ],
      ),
    );
  }

  String _formatTimeAgo(DateTime dateTime) {
    final diff = DateTime.now().difference(dateTime);
    if (diff.inDays > 30) return '${(diff.inDays / 30).floor()}mo ago';
    if (diff.inDays > 0) return '${diff.inDays}d ago';
    if (diff.inHours > 0) return '${diff.inHours}h ago';
    if (diff.inMinutes > 0) return '${diff.inMinutes}m ago';
    return 'Just now';
  }
}

class _ActivityTile extends StatelessWidget {
  final Map<String, dynamic> activity;
  const _ActivityTile({required this.activity});

  @override
  Widget build(BuildContext context) {
    final action = activity['action'] as String? ?? '';
    final actionDisplay = activity['action_display'] as String? ?? action;
    final freeText = (activity['description'] as String?) ?? '';
    final metadata = activity['metadata'] is Map<String, dynamic>
        ? activity['metadata'] as Map<String, dynamic>
        : const <String, dynamic>{};

    final userEmail =
        activity['user_email'] as String? ??
        ((activity['user'] as Map<String, dynamic>?)?['email'] as String?);
    final humanized = activity['humanized_time'] as String?;
    final timestampRaw = activity['timestamp'] ?? activity['created_at'];
    DateTime? timestamp;
    if (timestampRaw is String) timestamp = DateTime.tryParse(timestampRaw);

    final detail = _describe(action, actionDisplay, metadata, freeText);

    return Container(
      margin: const EdgeInsets.only(bottom: 8),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: AppColors.surface,
        borderRadius: AppLayout.borderRadiusMd,
        border: Border.all(color: AppColors.border),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Container(
            padding: const EdgeInsets.all(6),
            decoration: BoxDecoration(
              color: _iconBg(action),
              shape: BoxShape.circle,
            ),
            child: Icon(_iconFor(action), size: 14, color: _iconColor(action)),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  detail,
                  style: AppTypography.body.copyWith(
                    fontWeight: FontWeight.w500,
                  ),
                ),
                const SizedBox(height: 4),
                Text(
                  [
                    if (userEmail != null && userEmail.isNotEmpty) userEmail,
                    if (humanized != null && humanized.isNotEmpty)
                      humanized
                    else if (timestamp != null)
                      _format(timestamp),
                  ].join(' · '),
                  style: AppTypography.caption.copyWith(
                    color: AppColors.textTertiary,
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  /// Build a human-readable line for the activity. Falls back to the
  /// backend's `action_display` for verbs we don't render specially.
  String _describe(
    String action,
    String actionDisplay,
    Map<String, dynamic> meta,
    String freeText,
  ) {
    String beforeAfter(String fallback) {
      final before = meta['before']?.toString();
      final after = meta['after']?.toString();
      if (before != null && after != null) {
        return '$fallback: $before → $after';
      }
      return fallback;
    }

    switch (action) {
      case 'CREATE':
        return 'Created the ticket';
      case 'UPDATE':
        final changes = meta['changes'];
        if (changes is Map<String, dynamic> && changes.isNotEmpty) {
          final fields = changes.keys
              .map((k) => k.replaceAll('_', ' '))
              .join(', ');
          return 'Updated $fields';
        }
        return 'Updated the ticket';
      case 'STATUS_CHANGED':
        return beforeAfter('Status changed');
      case 'PRIORITY_CHANGED':
        return beforeAfter('Priority changed');
      case 'COMMENT':
        return 'Added a comment';
      case 'ASSIGN':
        return 'Updated assignees';
      case 'LINKED_SOLUTION':
        return 'Linked a solution';
      case 'UNLINKED_SOLUTION':
        return 'Unlinked a solution';
      case 'LINKED_PARENT':
        return 'Linked to a parent ticket';
      case 'UNLINKED_PARENT':
        return 'Detached from parent';
      case 'WATCHED':
        return 'Started watching';
      case 'UNWATCHED':
        return 'Stopped watching';
      case 'MENTIONED':
        return 'Mentioned a user';
      case 'APPROVAL_REQUESTED':
        return 'Requested approval';
      case 'APPROVED':
        return 'Approval granted';
      case 'REJECTED':
        return 'Approval rejected';
      case 'APPROVAL_CANCELLED':
        return 'Approval cancelled';
      case 'ESCALATED':
        return 'Escalated';
      case 'REOPENED':
        return 'Reopened';
      case 'MERGED':
        return 'Merged into another ticket';
      case 'MERGE_TARGET':
        return 'Received a merge';
      case 'UNMERGED':
      case 'UNMERGE_TARGET':
        return 'Unmerged';
      case 'ROUTED':
        return 'Routed by auto-assignment';
      case 'TIME_LOGGED':
        final minutes = meta['minutes'];
        if (minutes is num) return 'Logged ${minutes.round()}m';
        return 'Logged time';
      case 'PARENT_CLOSED_CASCADE':
        return 'Closed via parent cascade';
      default:
        if (freeText.isNotEmpty) return freeText;
        return actionDisplay.isNotEmpty ? actionDisplay : action;
    }
  }

  IconData _iconFor(String action) {
    switch (action) {
      case 'CREATE':
        return LucideIcons.plus;
      case 'STATUS_CHANGED':
        return LucideIcons.circleDot;
      case 'PRIORITY_CHANGED':
        return LucideIcons.flag;
      case 'COMMENT':
        return LucideIcons.messageSquare;
      case 'ASSIGN':
        return LucideIcons.users;
      case 'LINKED_SOLUTION':
      case 'UNLINKED_SOLUTION':
        return LucideIcons.bookOpen;
      case 'LINKED_PARENT':
      case 'UNLINKED_PARENT':
        return LucideIcons.gitBranch;
      case 'WATCHED':
      case 'UNWATCHED':
        return LucideIcons.eye;
      case 'MENTIONED':
        return LucideIcons.atSign;
      case 'APPROVAL_REQUESTED':
      case 'APPROVED':
      case 'REJECTED':
      case 'APPROVAL_CANCELLED':
        return LucideIcons.shieldCheck;
      case 'ESCALATED':
        return LucideIcons.alertTriangle;
      case 'REOPENED':
        return LucideIcons.refreshCw;
      case 'MERGED':
      case 'MERGE_TARGET':
      case 'UNMERGED':
      case 'UNMERGE_TARGET':
        return LucideIcons.gitMerge;
      case 'TIME_LOGGED':
        return LucideIcons.clock;
      case 'DELETE':
        return LucideIcons.trash2;
      default:
        return LucideIcons.activity;
    }
  }

  Color _iconColor(String action) {
    switch (action) {
      case 'ESCALATED':
      case 'REJECTED':
      case 'DELETE':
        return AppColors.danger600;
      case 'APPROVED':
      case 'CREATE':
        return AppColors.success600;
      case 'PRIORITY_CHANGED':
      case 'APPROVAL_REQUESTED':
        return AppColors.warning600;
      default:
        return AppColors.primary600;
    }
  }

  Color _iconBg(String action) {
    switch (action) {
      case 'ESCALATED':
      case 'REJECTED':
      case 'DELETE':
        return AppColors.danger50;
      case 'APPROVED':
      case 'CREATE':
        return AppColors.success50;
      case 'PRIORITY_CHANGED':
      case 'APPROVAL_REQUESTED':
        return AppColors.warning50;
      default:
        return AppColors.primary50;
    }
  }

  String _format(DateTime t) {
    final now = DateTime.now();
    final diff = now.difference(t);
    if (diff.inDays > 30) return '${(diff.inDays / 30).floor()}mo ago';
    if (diff.inDays > 0) return '${diff.inDays}d ago';
    if (diff.inHours > 0) return '${diff.inHours}h ago';
    if (diff.inMinutes > 0) return '${diff.inMinutes}m ago';
    return 'Just now';
  }
}

/// One other open ticket on the same account.
///
/// A full-width tap target at 48px, because the card sits low on a phone
/// screen and a thumb reaching it is aiming without looking.
class _AlsoOpenRow extends StatelessWidget {
  const _AlsoOpenRow({required this.ticket, required this.onTap});

  final Ticket ticket;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: onTap,
      child: Container(
        constraints: const BoxConstraints(minHeight: 48),
        padding: const EdgeInsets.symmetric(vertical: 8),
        child: Row(
          children: [
            Container(
              width: 8,
              height: 8,
              decoration: BoxDecoration(
                color: ticket.status.color,
                shape: BoxShape.circle,
              ),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    ticket.name,
                    style: AppTypography.label,
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                  ),
                  const SizedBox(height: 2),
                  Text(
                    '${ticket.status.label} · ${ticket.priority.label}',
                    style: AppTypography.caption.copyWith(
                      color: AppColors.textSecondary,
                    ),
                  ),
                ],
              ),
            ),
            Icon(
              LucideIcons.chevronRight,
              size: 16,
              color: AppColors.textTertiary,
            ),
          ],
        ),
      ),
    );
  }
}
