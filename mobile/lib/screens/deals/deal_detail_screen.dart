import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';
import '../../core/permissions.dart';
import '../../core/theme/theme.dart';
import '../../data/models/models.dart';
import '../../providers/auth_provider.dart';
import '../../providers/deal_pipelines_provider.dart';
import '../../providers/deals_provider.dart';
import '../../providers/invoices_provider.dart';
import '../../routes/app_router.dart';
import '../../config/api_config.dart';
import '../../services/attachment_upload.dart';
import '../../widgets/cards/deal_aging_badge.dart';
import '../../widgets/common/common.dart';
import '../../widgets/misc/stage_stepper.dart';

/// Deal Detail Screen, mirrors the lead-detail tab structure (Overview /
/// Activity / Notes / Files) and surfaces the full payload returned by the
/// opportunities detail endpoint (comments, attachments, contacts, custom
/// fields, teams, aging metadata).
class DealDetailScreen extends ConsumerStatefulWidget {
  final String dealId;

  const DealDetailScreen({super.key, required this.dealId});

  @override
  ConsumerState<DealDetailScreen> createState() => _DealDetailScreenState();
}

class _DealDetailScreenState extends ConsumerState<DealDetailScreen>
    with SingleTickerProviderStateMixin {
  late TabController _tabController;
  final TextEditingController _noteController = TextEditingController();

  Deal? _deal;
  List<CustomFieldDefinition> _customFieldDefinitions = const [];
  bool _isLoading = true;
  bool _isUpdatingStage = false;
  bool _isAddingNote = false;
  bool _isUploadingAttachment = false;
  bool _isInvoicing = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _tabController = TabController(length: 4, vsync: this);
    _fetchDeal();
  }

  @override
  void dispose() {
    _tabController.dispose();
    _noteController.dispose();
    super.dispose();
  }

  Future<void> _fetchDeal() async {
    // Only show the full-page spinner on the first load. Subsequent
    // refreshes (after add/delete comment, stage change, pull-to-refresh,
    // returning from edit) update silently so the user keeps their scroll
    // position and selected tab.
    final isInitialLoad = _deal == null;
    setState(() {
      if (isInitialLoad) _isLoading = true;
      _error = null;
    });

    final detail = await ref
        .read(dealsProvider.notifier)
        .getDealDetail(widget.dealId);

    if (mounted) {
      setState(() {
        _isLoading = false;
        if (detail != null) {
          _deal = detail.deal;
          _customFieldDefinitions = detail.customFieldDefinitions;
        } else if (isInitialLoad) {
          _error = 'Failed to load deal';
        }
      });
    }
  }

  /// The deal's pipeline, once both have loaded. Null while loading, or
  /// when the list does not hold the deal's pipeline: then no stage control
  /// is offered rather than another pipeline's stages.
  DealPipeline? get _pipeline {
    final pipelines = ref.watch(dealPipelinesProvider).value;
    if (pipelines == null || _deal == null) return null;
    return dealPipelineOf(pipelines, _deal!.pipelineId);
  }

  /// The step after the deal's stage: the next open stage in board order,
  /// or the first won stage after the last open one. Lost is never "next".
  DealPipelineStage? _nextStage(DealPipeline? pipeline) {
    if (pipeline == null || _deal == null) return null;
    final steps = pipeline.stages.where((s) => !s.isLost).toList();
    final index = steps.indexWhere((s) => s.code == _deal!.stage);
    if (index < 0 || index >= steps.length - 1 || steps[index].isWon) {
      return null;
    }
    return steps[index + 1];
  }

  @override
  Widget build(BuildContext context) {
    if (_isLoading) {
      return Scaffold(
        backgroundColor: AppColors.surfaceDim,
        appBar: AppBar(
          leading: IconButton(
            icon: const Icon(LucideIcons.chevronLeft),
            onPressed: () => context.pop(),
          ),
        ),
        body: const Center(child: CircularProgressIndicator()),
      );
    }

    if (_error != null || _deal == null) {
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
              const EmptyState(
                icon: LucideIcons.briefcase,
                title: 'Deal not found',
                description: 'This deal may have been deleted',
              ),
              const SizedBox(height: 16),
              TextButton(onPressed: _fetchDeal, child: const Text('Retry')),
            ],
          ),
        ),
      );
    }

    final textScale = MediaQuery.textScalerOf(context).scale(1.0);
    // Header content: title + account chip + amount + badge row. Slightly
    // shorter than lead detail because we don't render avatars or a quick-
    // action row (deals don't expose contact-level email/phone here).
    final expandedHeight = 230 + (60 * (textScale - 1.0).clamp(0.0, 1.0));

    return Scaffold(
      backgroundColor: AppColors.surface,
      body: NestedScrollView(
        headerSliverBuilder: (context, innerBoxIsScrolled) => [
          SliverAppBar(
            expandedHeight: expandedHeight,
            pinned: true,
            backgroundColor: AppColors.primary50,
            leading: IconButton(
              icon: _headerIconBackground(LucideIcons.chevronLeft),
              onPressed: () => context.pop(),
            ),
            actions: [
              IconButton(
                icon: _headerIconBackground(LucideIcons.pencil),
                onPressed: _navigateToEdit,
              ),
              IconButton(
                icon: _headerIconBackground(LucideIcons.moreVertical),
                onPressed: _showMoreOptions,
              ),
            ],
            flexibleSpace: FlexibleSpaceBar(background: _buildHeader()),
            bottom: PreferredSize(
              preferredSize: const Size.fromHeight(48),
              child: Container(
                color: AppColors.surface,
                child: TabBar(
                  controller: _tabController,
                  labelColor: AppColors.primary600,
                  unselectedLabelColor: AppColors.textSecondary,
                  indicatorColor: AppColors.primary600,
                  indicatorWeight: 2,
                  labelStyle: AppTypography.label,
                  isScrollable: true,
                  tabAlignment: TabAlignment.start,
                  tabs: [
                    const Tab(text: 'Overview'),
                    const Tab(text: 'Activity'),
                    Tab(
                      text: _deal!.comments.isEmpty
                          ? 'Notes'
                          : 'Notes (${_deal!.comments.length})',
                    ),
                    Tab(
                      text: _deal!.attachments.isEmpty
                          ? 'Files'
                          : 'Files (${_deal!.attachments.length})',
                    ),
                  ],
                ),
              ),
            ),
          ),
        ],
        body: TabBarView(
          controller: _tabController,
          children: [
            _buildOverviewTab(),
            _buildActivityTab(),
            _buildNotesTab(),
            _buildFilesTab(),
          ],
        ),
      ),
      bottomNavigationBar: _buildStickyBottomBar(),
    );
  }

  Widget _headerIconBackground(IconData icon) {
    return Container(
      padding: const EdgeInsets.all(8),
      decoration: BoxDecoration(
        color: Colors.white.withValues(alpha: 0.9),
        shape: BoxShape.circle,
      ),
      child: Icon(icon, size: 18, color: AppColors.textPrimary),
    );
  }

  Widget _buildHeader() {
    final deal = _deal!;
    return Container(
      decoration: BoxDecoration(
        gradient: LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: [
            AppColors.primary50,
            AppColors.primary100.withValues(alpha: 0.5),
          ],
        ),
      ),
      child: SafeArea(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(20, 56, 20, 12),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisAlignment: MainAxisAlignment.end,
            mainAxisSize: MainAxisSize.min,
            children: [
              Text(
                deal.title,
                style: AppTypography.h2.copyWith(color: AppColors.textPrimary),
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
              ),
              const SizedBox(height: 4),
              // Account name. The mobile app doesn't have an account
              // detail screen yet, so this is display-only. Don't make it
              // tappable until /accounts/<id> exists.
              Row(
                children: [
                  Icon(
                    LucideIcons.building2,
                    size: 14,
                    color: AppColors.textSecondary,
                  ),
                  const SizedBox(width: 6),
                  Flexible(
                    child: Text(
                      deal.companyName,
                      style: AppTypography.body.copyWith(
                        color: AppColors.textSecondary,
                      ),
                      overflow: TextOverflow.ellipsis,
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 8),
              Text(
                _formatDealAmount(deal),
                style: AppTypography.display.copyWith(
                  color: AppColors.primary600,
                  fontSize: 28,
                ),
              ),
              const SizedBox(height: 6),
              _buildHeaderBadges(deal),
            ],
          ),
        ),
      ),
    );
  }

  /// Badges row under the amount: stage + aging warning + closing-soon /
  /// overdue + tag preview with +N overflow.
  Widget _buildHeaderBadges(Deal deal) {
    final chips = <Widget>[];

    // Stage chip, coloured by the stage's place in its pipeline.
    final pipeline = _pipeline;
    final stage = pipeline?.stageByCode(deal.stage);
    final stageColor = stage != null
        ? pipeline!.colorOf(stage)
        : dealStageColor(deal.stageKind, 1);
    chips.add(
      _Chip(
        label: deal.stageLabel,
        bg: stageColor.withValues(alpha: 0.2),
        fg: stageColor,
      ),
    );

    // Past expected / stalled, as the server judged it for this stage.
    if (dealAgingLabel(deal) != null) chips.add(DealAgingBadge(deal: deal));

    // Closing-soon / overdue (only for open deals. Closed deals are by
    // definition past their close date).
    if (!deal.isClosed && deal.closeDate != null) {
      if (deal.isOverdue) {
        chips.add(
          _Chip(
            label: 'Overdue',
            bg: AppColors.danger100,
            fg: AppColors.danger600,
            icon: LucideIcons.alertTriangle,
          ),
        );
      } else if (deal.isClosingSoon) {
        chips.add(
          _Chip(
            label: 'Closes in ${deal.daysUntilClose}d',
            bg: AppColors.warning100,
            fg: AppColors.warning600,
            icon: LucideIcons.alarmClock,
          ),
        );
      }
    }

    // Tag preview: first 2 + "+N more". The +N chip is informational only
    //. Full tag list is in the Overview tab.
    final tags = deal.labels;
    for (final t in tags.take(2)) {
      chips.add(LabelPill(label: t));
    }
    if (tags.length > 2) {
      chips.add(
        _Chip(
          label: '+${tags.length - 2}',
          bg: AppColors.gray100,
          fg: AppColors.textSecondary,
        ),
      );
    }

    return Wrap(spacing: 6, runSpacing: 6, children: chips);
  }

  // ===========================================================================
  // OVERVIEW TAB
  // ===========================================================================

  Widget _buildOverviewTab() {
    return RefreshIndicator(
      onRefresh: _fetchDeal,
      child: _buildOverviewScroll(),
    );
  }

  Widget _buildOverviewScroll() {
    final deal = _deal!;
    final hasNotes = deal.notes != null && deal.notes!.trim().isNotEmpty;
    final hasProducts = deal.products.isNotEmpty;
    final hasContacts = deal.contactsList.isNotEmpty;
    final hasCustomFields = _hasRenderableCustomFields(deal);
    final hasTeams = deal.teamNames.isNotEmpty;
    final hasTags = deal.labels.isNotEmpty;

    return SingleChildScrollView(
      physics: const AlwaysScrollableScrollPhysics(),
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // Stage stepper. A lost deal gets a banner inside the widget.
          if (_pipeline != null)
            StageStepper(
              stages: _pipeline!.stages,
              currentCode: deal.stage,
              onStageChange: _isUpdatingStage ? null : _confirmStageChange,
            ),
          if (deal.daysInStageServer != null ||
              deal.daysInCurrentStage != null) ...[
            const SizedBox(height: 8),
            Text(
              _formatDaysInStage(deal),
              style: AppTypography.caption.copyWith(
                color: AppColors.textTertiary,
              ),
            ),
          ],
          const SizedBox(height: 16),

          // Probability bar. Gives stage-aware copy below the percentage.
          _buildCard(
            title: 'Win Probability',
            child: _buildProbabilityContent(deal),
          ),
          const SizedBox(height: 16),

          // Deal information grid.
          _buildCard(
            title: 'Deal Information',
            child: _buildDealInfoContent(deal),
          ),
          const SizedBox(height: 16),

          // Notes (description), formerly "Description" on the old screen.
          if (hasNotes) ...[
            _buildCard(
              title: 'Notes',
              child: Text(
                deal.notes!,
                style: AppTypography.body.copyWith(
                  color: AppColors.textSecondary,
                  height: 1.5,
                ),
              ),
            ),
            const SizedBox(height: 16),
          ],

          // Line items / Products with backend total when available.
          if (hasProducts) ...[
            _buildCard(
              title: 'Products',
              action: _Chip(
                label: '${deal.products.length}',
                bg: AppColors.gray100,
                fg: AppColors.textSecondary,
              ),
              child: _buildProductsContent(deal),
            ),
            const SizedBox(height: 16),
          ],

          // Contacts list. Backend returns full records.
          if (hasContacts) ...[
            _buildCard(
              title: 'Contacts',
              action: _Chip(
                label: '${deal.contactsList.length}',
                bg: AppColors.gray100,
                fg: AppColors.textSecondary,
              ),
              child: _buildContactsContent(deal),
            ),
            const SizedBox(height: 16),
          ],

          // Custom fields, org schema; only shown when the deal has at
          // least one value populated.
          if (hasCustomFields) ...[
            _buildCard(
              title: 'Custom Fields',
              child: _buildCustomFieldsContent(deal),
            ),
            const SizedBox(height: 16),
          ],

          // Assigned to: every assignee, not just the first.
          _buildCard(title: 'Assigned To', child: _buildAssigneesContent(deal)),
          const SizedBox(height: 16),

          // Teams, backend exposes teams on Opportunity; render names only.
          if (hasTeams) ...[
            _buildCard(
              title: 'Teams',
              child: Wrap(
                spacing: 8,
                runSpacing: 8,
                children: [
                  for (final t in deal.teamNames)
                    _Chip(
                      label: t,
                      bg: AppColors.primary50,
                      fg: AppColors.primary600,
                    ),
                ],
              ),
            ),
            const SizedBox(height: 16),
          ],

          // Tags
          if (hasTags) ...[
            _buildCard(
              title: 'Tags',
              child: Wrap(
                spacing: 8,
                runSpacing: 8,
                children: [for (final t in deal.labels) LabelPill(label: t)],
              ),
            ),
            const SizedBox(height: 16),
          ],

          // Metadata footer: created by / closed by.
          _buildCard(title: 'Metadata', child: _buildMetadataContent(deal)),
          const SizedBox(height: 24),
        ],
      ),
    );
  }

  Widget _buildProbabilityContent(Deal deal) {
    final clamped = deal.probability.clamp(0, 100);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: [
            Text(
              '$clamped%',
              style: AppTypography.h3.copyWith(color: _probabilityColor(deal)),
            ),
            Text(
              _probabilityHint(deal),
              style: AppTypography.caption.copyWith(
                color: AppColors.textTertiary,
              ),
            ),
          ],
        ),
        const SizedBox(height: 8),
        ClipRRect(
          borderRadius: BorderRadius.circular(4),
          child: LinearProgressIndicator(
            value: clamped / 100,
            minHeight: 8,
            backgroundColor: AppColors.gray200,
            valueColor: AlwaysStoppedAnimation(_probabilityColor(deal)),
          ),
        ),
      ],
    );
  }

  Widget _buildDealInfoContent(Deal deal) {
    final rows = <Widget>[
      _InfoRow(
        icon: LucideIcons.wallet,
        label: 'AMOUNT',
        value: _formatDealAmount(deal),
      ),
      _InfoRow(
        icon: LucideIcons.calendar,
        label: 'EXPECTED CLOSE',
        value: deal.closeDate != null
            ? _formatDate(deal.closeDate!)
            : 'Not set',
      ),
      _InfoRow(
        icon: LucideIcons.tag,
        label: 'TYPE',
        value: deal.opportunityType.label,
      ),
      _InfoRow(
        icon: LucideIcons.compass,
        label: 'SOURCE',
        value: deal.leadSource.label,
      ),
      _InfoRow(
        icon: LucideIcons.coins,
        label: 'CURRENCY',
        value: '${deal.currency.label} (${deal.currency.symbol})',
      ),
    ];
    final out = <Widget>[];
    for (var i = 0; i < rows.length; i++) {
      if (i > 0) out.add(const Divider(height: 24));
      out.add(rows[i]);
    }
    return Column(children: out);
  }

  Widget _buildProductsContent(Deal deal) {
    final localTotal = deal.products.fold<double>(
      0,
      (sum, p) => sum + p.netTotal,
    );
    // Backend pre-computes the line-items total. When present, trust it.
    // It accounts for line-item-level rounding the mobile model doesn't.
    final total = deal.lineItemsTotal ?? localTotal;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        for (final product in deal.products)
          Padding(
            padding: const EdgeInsets.only(bottom: 12),
            child: Row(
              children: [
                Container(
                  width: 36,
                  height: 36,
                  decoration: BoxDecoration(
                    color: AppColors.primary100,
                    borderRadius: BorderRadius.circular(8),
                  ),
                  child: Icon(
                    LucideIcons.package,
                    size: 18,
                    color: AppColors.primary600,
                  ),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(product.name, style: AppTypography.label),
                      Text(
                        'Qty: ${product.quantityLabel}',
                        style: AppTypography.caption.copyWith(
                          color: AppColors.textSecondary,
                        ),
                      ),
                    ],
                  ),
                ),
                Text(
                  _formatMoney(product.netTotal, deal.currency),
                  style: AppTypography.label.copyWith(
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ],
            ),
          ),
        const Divider(height: 24),
        Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: [
            Text(
              'TOTAL',
              style: AppTypography.overline.copyWith(
                color: AppColors.textTertiary,
              ),
            ),
            Text(
              _formatMoney(total, deal.currency),
              style: AppTypography.label.copyWith(fontWeight: FontWeight.w600),
            ),
          ],
        ),
      ],
    );
  }

  Widget _buildContactsContent(Deal deal) {
    return Column(
      children: [
        for (var i = 0; i < deal.contactsList.length; i++) ...[
          Row(
            children: [
              UserAvatar(
                name: deal.contactsList[i].fullName.isEmpty
                    ? (deal.contactsList[i].email ?? '?')
                    : deal.contactsList[i].fullName,
                size: AvatarSize.sm,
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      deal.contactsList[i].fullName.isEmpty
                          ? (deal.contactsList[i].email ?? 'Contact')
                          : deal.contactsList[i].fullName,
                      style: AppTypography.label,
                    ),
                    if (deal.contactsList[i].email != null &&
                        deal.contactsList[i].email!.isNotEmpty)
                      Text(
                        deal.contactsList[i].email!,
                        style: AppTypography.caption.copyWith(
                          color: AppColors.textSecondary,
                        ),
                        overflow: TextOverflow.ellipsis,
                      ),
                  ],
                ),
              ),
            ],
          ),
          if (i < deal.contactsList.length - 1) const Divider(height: 24),
        ],
      ],
    );
  }

  bool _hasRenderableCustomFields(Deal deal) {
    if (_customFieldDefinitions.isEmpty) return false;
    for (final def in _customFieldDefinitions) {
      final v = deal.customFieldValues[def.key];
      if (v == null) continue;
      if (v is String && v.trim().isEmpty) continue;
      return true;
    }
    return false;
  }

  Widget _buildCustomFieldsContent(Deal deal) {
    final rows = <Widget>[];
    for (final def in _customFieldDefinitions) {
      final raw = deal.customFieldValues[def.key];
      if (raw == null) continue;
      final display = _formatCustomFieldValue(def, raw);
      if (display.isEmpty) continue;
      if (rows.isNotEmpty) rows.add(const Divider(height: 24));
      rows.add(
        Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              def.label.toUpperCase(),
              style: AppTypography.overline.copyWith(
                color: AppColors.textTertiary,
                fontSize: 10,
              ),
            ),
            const SizedBox(height: 4),
            Text(display, style: AppTypography.body),
          ],
        ),
      );
    }
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: rows);
  }

  String _formatCustomFieldValue(CustomFieldDefinition def, Object? raw) {
    if (raw == null) return '';
    switch (def.fieldType) {
      case CustomFieldType.checkbox:
        return raw == true ? 'Yes' : 'No';
      case CustomFieldType.dropdown:
        final value = raw.toString();
        final match = def.options
            .firstWhere(
              (o) => o.value == value,
              orElse: () => CustomFieldOption(value: value, label: value),
            )
            .label;
        return match;
      case CustomFieldType.date:
        final parsed = DateTime.tryParse(raw.toString());
        return parsed != null ? _formatDate(parsed) : raw.toString();
      case CustomFieldType.number:
      case CustomFieldType.text:
      case CustomFieldType.textarea:
        return raw.toString().trim();
    }
  }

  Widget _buildAssigneesContent(Deal deal) {
    if (deal.assignedToRaw.isEmpty) {
      return Row(
        children: [
          Icon(LucideIcons.userX, size: 20, color: AppColors.gray400),
          const SizedBox(width: 12),
          Text(
            'Unassigned',
            style: AppTypography.body.copyWith(color: AppColors.textSecondary),
          ),
        ],
      );
    }
    return Column(
      children: [
        for (var i = 0; i < deal.assignedToRaw.length; i++) ...[
          _AssigneeRow(profile: deal.assignedToRaw[i]),
          if (i < deal.assignedToRaw.length - 1) const SizedBox(height: 12),
        ],
      ],
    );
  }

  Widget _buildMetadataContent(Deal deal) {
    final rows = <Widget>[
      _InfoRow(
        icon: LucideIcons.userPlus,
        label: 'CREATED BY',
        value: deal.createdByName ?? deal.createdByEmail ?? '—',
      ),
      _InfoRow(
        icon: LucideIcons.clock,
        label: 'CREATED ON',
        value: _formatDate(deal.createdAt),
      ),
    ];
    if (deal.isClosed) {
      rows.add(
        _InfoRow(
          icon: deal.isWon ? LucideIcons.trophy : LucideIcons.xCircle,
          label: deal.isWon ? 'WON BY' : 'CLOSED BY',
          value: deal.closedByName ?? deal.closedByEmail ?? '—',
        ),
      );
      if (deal.closeDate != null) {
        rows.add(
          _InfoRow(
            icon: LucideIcons.calendar,
            label: 'CLOSED ON',
            value: _formatDate(deal.closeDate!),
          ),
        );
      }
    }
    final out = <Widget>[];
    for (var i = 0; i < rows.length; i++) {
      if (i > 0) out.add(const Divider(height: 24));
      out.add(rows[i]);
    }
    return Column(children: out);
  }

  // ===========================================================================
  // ACTIVITY TAB, comments + attachments + created event, newest first.
  // ===========================================================================

  Widget _buildActivityTab() {
    final events = _buildTimelineEvents();
    if (events.isEmpty) {
      return RefreshIndicator(
        onRefresh: _fetchDeal,
        child: ListView(
          physics: const AlwaysScrollableScrollPhysics(),
          children: [
            const SizedBox(height: 120),
            Center(
              child: Column(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  Icon(LucideIcons.clock, size: 48, color: AppColors.gray400),
                  const SizedBox(height: 16),
                  Text('No activity yet', style: AppTypography.h3),
                  const SizedBox(height: 8),
                  Text(
                    'Activity will appear here as it happens',
                    style: AppTypography.body.copyWith(
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
    return RefreshIndicator(
      onRefresh: _fetchDeal,
      child: ListView.builder(
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.fromLTRB(16, 16, 16, 32),
        itemCount: events.length,
        itemBuilder: (context, index) {
          final e = events[index];
          final isLast = index == events.length - 1;
          return _TimelineRow(event: e, isLast: isLast);
        },
      ),
    );
  }

  List<_TimelineEvent> _buildTimelineEvents() {
    final deal = _deal!;
    final events = <_TimelineEvent>[
      for (final c in deal.comments)
        _TimelineEvent(
          kind: _TimelineKind.comment,
          ts: c.commentedOn,
          actor: c.authorName,
          summary: 'commented',
          body: c.comment,
        ),
      for (final a in deal.attachments)
        _TimelineEvent(
          kind: _TimelineKind.attachment,
          ts: a.createdAt ?? deal.createdAt,
          actor: a.createdBy ?? 'Someone',
          summary: 'uploaded ${a.fileName}',
        ),
      _TimelineEvent(
        kind: _TimelineKind.created,
        ts: deal.createdAt,
        actor: deal.createdByName ?? deal.createdByEmail ?? '',
        summary: deal.createdByName != null || deal.createdByEmail != null
            ? 'created the deal'
            : 'Deal created',
      ),
    ];
    events.sort((a, b) => b.ts.compareTo(a.ts));
    return events;
  }

  // ===========================================================================
  // NOTES TAB, comment CRUD.
  // ===========================================================================

  Widget _buildNotesTab() {
    final comments = _deal!.comments;
    return Column(
      children: [
        Expanded(
          child: comments.isEmpty
              ? const EmptyState(
                  icon: LucideIcons.fileText,
                  title: 'No notes yet',
                  description: 'Add a note to keep track of important details',
                )
              : ListView.builder(
                  padding: const EdgeInsets.all(16),
                  itemCount: comments.length,
                  itemBuilder: (context, index) {
                    final comment = comments[index];
                    // Same permissions rule as leads: only the comment's
                    // author or an org admin may edit/delete.
                    final currentEmail = ref
                        .watch(currentUserProvider)
                        ?.email
                        .toLowerCase();
                    final isAdmin =
                        ref.watch(selectedOrgProvider)?.role == 'ADMIN';
                    final isAuthor =
                        currentEmail != null &&
                        comment.commentedByEmail?.toLowerCase() == currentEmail;
                    final canModify = isAuthor || isAdmin;
                    return _NoteCard(
                      text: comment.comment,
                      author: comment.authorName,
                      timestamp: comment.commentedOn,
                      onEdit: canModify
                          ? () => _editComment(comment.id, comment.comment)
                          : null,
                      onDelete: canModify
                          ? () => _deleteComment(comment.id)
                          : null,
                    );
                  },
                ),
        ),
        Container(
          padding: const EdgeInsets.all(16),
          decoration: BoxDecoration(
            color: AppColors.surface,
            border: Border(top: BorderSide(color: AppColors.border)),
          ),
          child: SafeArea(
            child: Row(
              children: [
                Expanded(
                  child: TextField(
                    controller: _noteController,
                    enabled: !_isAddingNote,
                    decoration: InputDecoration(
                      hintText: 'Add a note...',
                      hintStyle: AppTypography.body.copyWith(
                        color: AppColors.textTertiary,
                      ),
                      filled: true,
                      fillColor: AppColors.gray100,
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
                    color: _isAddingNote
                        ? AppColors.gray400
                        : AppColors.primary600,
                    shape: BoxShape.circle,
                  ),
                  child: IconButton(
                    icon: _isAddingNote
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
                    onPressed: _isAddingNote ? null : _addComment,
                  ),
                ),
              ],
            ),
          ),
        ),
      ],
    );
  }

  Future<void> _addComment() async {
    final text = _noteController.text.trim();
    if (text.isEmpty) return;
    setState(() => _isAddingNote = true);
    final result = await ref
        .read(dealsProvider.notifier)
        .addComment(widget.dealId, text);
    if (!mounted) return;
    setState(() => _isAddingNote = false);
    if (result.success) {
      _noteController.clear();
      await _fetchDeal();
      if (mounted) _snack('Note added');
    } else {
      _snack(result.error ?? 'Failed to add note', danger: true);
    }
  }

  Future<void> _editComment(String commentId, String currentText) async {
    final newText = await showDialog<String>(
      context: context,
      builder: (context) => _EditNoteDialog(initialText: currentText),
    );
    if (newText == null || newText.isEmpty || newText == currentText.trim()) {
      return;
    }
    final result = await ref
        .read(dealsProvider.notifier)
        .updateComment(commentId, newText);
    if (!mounted) return;
    if (result.success) {
      await _fetchDeal();
      if (mounted) _snack('Note updated');
    } else {
      _snack(result.error ?? 'Failed to update note', danger: true);
    }
  }

  Future<void> _deleteComment(String commentId) async {
    final result = await ref
        .read(dealsProvider.notifier)
        .deleteComment(commentId);
    if (!mounted) return;
    if (result.success) {
      await _fetchDeal();
      if (mounted) _snack('Note deleted');
    } else {
      _snack(result.error ?? 'Failed to delete note', danger: true);
    }
  }

  // ===========================================================================
  // FILES TAB
  // ===========================================================================

  Widget _buildFilesTab() {
    final attachments = _deal!.attachments;
    if (attachments.isEmpty) {
      return RefreshIndicator(
        onRefresh: _fetchDeal,
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
                  // This used to read "Attachments uploaded from the web appear
                  // here", which was true and was the whole problem.
                  Text(
                    'Attach a contract, a quote, or a photo.',
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
      onRefresh: _fetchDeal,
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
            : AttachmentTile(attachment: attachments[index], onOpen: _openFile),
      ),
    );
  }

  /// Attach a file to this deal.
  ///
  /// The opportunity detail POST answers with the refreshed `attachments`
  /// array, so the tab updates without a refetch.
  Future<void> _pickAndUploadAttachment() async {
    if (_isUploadingAttachment || _deal == null) return;
    setState(() => _isUploadingAttachment = true);

    final result = await pickAndUploadAttachment(
      target: AttachmentTarget.deal,
      recordId: widget.dealId,
    );

    if (!mounted) return;
    setState(() => _isUploadingAttachment = false);

    if (result.cancelled) return;
    if (!result.succeeded) {
      _snack(result.error!);
      return;
    }

    final attachmentsData = result.data?['attachments'];
    if (attachmentsData is List && _deal != null) {
      setState(() {
        _deal = _deal!.copyWith(
          attachments: attachmentsData
              .whereType<Map<String, dynamic>>()
              .map(Attachment.fromJson)
              .toList(),
        );
      });
    } else {
      await _fetchDeal();
    }
  }

  /// Download an attachment through the API, with the token attached.
  ///
  /// It used to hand the file's `/media/` path to the external browser, which
  /// carries no token and so could not fetch it, on a path that has no
  /// per-file authorization anyway.
  Future<void> _openFile(Attachment a) async {
    if (!a.hasFile) {
      _snack('That attachment has no file.');
      return;
    }
    final result = await downloadAttachment(
      url: ApiConfig.attachmentDownload(a.id),
      fileName: a.fileName,
    );
    if (!mounted || result.success) return;
    _snack(result.error ?? 'Could not download that file.');
  }

  // ===========================================================================
  // STICKY BOTTOM BAR
  // ===========================================================================

  Widget? _buildStickyBottomBar() {
    final deal = _deal!;
    final isWon = deal.isWon;
    final isClosed = deal.isClosed;
    final pipeline = _pipeline;
    final next = _nextStage(pipeline);
    final lost = pipeline?.firstLostStage;

    // Closed deals show a status banner, and the stepper handles reopening
    // (any non-current tap moves the deal). A won deal is also what gets
    // billed, so it carries the one action left: raising its invoice. The
    // server copies the lines into a Draft and refuses a deal with none,
    // saying so, which is shown as written.
    if (isClosed) {
      final bg = isWon ? AppColors.success100 : AppColors.danger100;
      final fg = isWon ? AppColors.success600 : AppColors.danger600;
      final icon = isWon ? LucideIcons.trophy : LucideIcons.xCircle;
      final label = isWon ? 'Deal Won!' : 'Deal Lost';
      return Container(
        decoration: BoxDecoration(
          color: AppColors.surface,
          border: Border(top: BorderSide(color: AppColors.border)),
        ),
        child: SafeArea(
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                Container(
                  width: double.infinity,
                  padding: const EdgeInsets.symmetric(vertical: 14),
                  decoration: BoxDecoration(
                    color: bg,
                    borderRadius: BorderRadius.circular(12),
                  ),
                  child: Row(
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: [
                      Icon(icon, color: fg, size: 20),
                      const SizedBox(width: 8),
                      Text(
                        label,
                        style: AppTypography.label.copyWith(
                          color: fg,
                          fontWeight: FontWeight.w600,
                        ),
                      ),
                    ],
                  ),
                ),
                if (isWon) ...[
                  const SizedBox(height: 12),
                  PrimaryButton(
                    label: 'Create invoice',
                    icon: LucideIcons.fileText,
                    isLoading: _isInvoicing,
                    onPressed: _isInvoicing ? null : _createInvoice,
                  ),
                ],
              ],
            ),
          ),
        ),
      );
    }

    return Container(
      decoration: BoxDecoration(
        color: AppColors.surface,
        border: Border(top: BorderSide(color: AppColors.border)),
      ),
      child: SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              if (next != null)
                PrimaryButton(
                  label: _isUpdatingStage
                      ? 'Updating...'
                      : 'Move to ${next.label}',
                  icon: LucideIcons.arrowRight,
                  iconRight: true,
                  onPressed: _isUpdatingStage
                      ? null
                      : () => _confirmStageChange(next),
                ),
              if (next != null && lost != null) const SizedBox(height: 12),
              if (lost != null)
                GhostButton(
                  label: 'Mark as Lost',
                  icon: LucideIcons.xCircle,
                  color: AppColors.danger600,
                  onPressed: _isUpdatingStage
                      ? null
                      : () => _handleMarkLost(lost),
                ),
            ],
          ),
        ),
      ),
    );
  }

  // ===========================================================================
  // ACTIONS
  // ===========================================================================

  /// Raises a Draft invoice from this won deal and opens it.
  Future<void> _createInvoice() async {
    setState(() => _isInvoicing = true);
    final result = await ref
        .read(invoicesProvider.notifier)
        .createFromDeal(widget.dealId);
    if (!mounted) return;
    setState(() => _isInvoicing = false);
    if (result.error != null) {
      _snack(result.error!, danger: true);
      return;
    }
    _snack('Draft invoice created');
    final id = result.invoiceId;
    if (id != null && id.isNotEmpty) {
      context.push('${AppRoutes.invoices}/$id');
    }
  }

  /// Opens the estimate form prefilled from this deal, at any stage.
  void _createEstimate() {
    if (_deal == null) return;
    context.push(AppRoutes.estimateNew, extra: _deal);
  }

  void _navigateToEdit() async {
    final result = await context.push('/deals/${widget.dealId}/edit');
    if (result == true && mounted) {
      _fetchDeal();
    }
  }

  Future<void> _confirmStageChange(DealPipelineStage newStage) async {
    if (_deal == null) return;
    if (newStage.code == _deal!.stage) return;
    final isReopen = _deal!.isLost && newStage.isOpen;
    final dialogTitle = isReopen
        ? 'Reopen as ${newStage.label}?'
        : 'Move to ${newStage.label}?';
    final dialogBody = isReopen
        ? 'This deal is lost. Reopening will move it back into the pipeline.'
        : 'This will update the deal stage.';

    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(dialogTitle),
        content: Text(dialogBody),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Cancel'),
          ),
          TextButton(
            onPressed: () => Navigator.pop(context, true),
            child: Text(isReopen ? 'Reopen' : 'Move'),
          ),
        ],
      ),
    );
    if (confirmed != true) return;
    await _changeStage(newStage);
  }

  Future<void> _handleMarkLost(DealPipelineStage lost) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Mark as Lost?'),
        content: const Text('Are you sure you want to mark this deal as lost?'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Cancel'),
          ),
          TextButton(
            onPressed: () => Navigator.pop(context, true),
            child: Text(
              'Mark Lost',
              style: TextStyle(color: AppColors.danger600),
            ),
          ),
        ],
      ),
    );
    if (confirmed != true) return;
    await _changeStage(lost);
  }

  Future<void> _changeStage(DealPipelineStage newStage) async {
    if (_isUpdatingStage) return;
    setState(() => _isUpdatingStage = true);
    final result = await ref
        .read(dealsProvider.notifier)
        .updateDealStage(widget.dealId, newStage);
    if (!mounted) return;
    setState(() => _isUpdatingStage = false);
    if (result.success) {
      _snack('Deal moved to ${newStage.label}');
      await _fetchDeal();
    } else {
      _snack(result.error ?? 'Failed to update stage', danger: true);
    }
  }

  void _showMoreOptions() {
    // `OpportunityDetailView.delete` allows admins and the deal's creator
    // only. Read and edit are wider (`assert_deal_access` admits assignees
    // too), which is why Edit stays unconditional and Delete does not.
    final canDelete = isAdminOrOwner(
      isAdmin: ref.read(isOrgAdminProvider),
      currentUserKey: ref.read(currentUserProvider)?.email,
      ownerKey: _deal?.createdByEmail,
    );
    showModalBottomSheet(
      context: context,
      backgroundColor: AppColors.surface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (context) => SafeArea(
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
            ListTile(
              leading: Icon(LucideIcons.pencil, color: AppColors.primary600),
              title: const Text('Edit Deal'),
              onTap: () {
                Navigator.pop(context);
                _navigateToEdit();
              },
            ),
            ListTile(
              leading: Icon(LucideIcons.fileText, color: AppColors.primary600),
              title: const Text('Create estimate'),
              onTap: () {
                Navigator.pop(context);
                _createEstimate();
              },
            ),
            if (_deal?.isWon ?? false)
              ListTile(
                leading: Icon(LucideIcons.receipt, color: AppColors.primary600),
                title: const Text('Create invoice'),
                onTap: () {
                  Navigator.pop(context);
                  _createInvoice();
                },
              ),
            if (canDelete)
              ListTile(
                leading: Icon(LucideIcons.trash2, color: AppColors.danger600),
                title: Text(
                  'Delete Deal',
                  style: TextStyle(color: AppColors.danger600),
                ),
                onTap: () {
                  Navigator.pop(context);
                  _confirmDelete();
                },
              ),
            const SizedBox(height: 16),
          ],
        ),
      ),
    );
  }

  Future<void> _confirmDelete() async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Delete Deal?'),
        content: const Text('This action cannot be undone.'),
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
    if (confirmed != true) return;
    final result = await ref
        .read(dealsProvider.notifier)
        .deleteDeal(widget.dealId);
    if (!mounted) return;
    if (result.success) {
      _snack('Deal deleted');
      context.pop();
    } else {
      _snack(result.error ?? 'Failed to delete deal', danger: true);
    }
  }

  // ===========================================================================
  // HELPERS
  // ===========================================================================

  void _snack(String message, {bool danger = false}) {
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(message),
        behavior: SnackBarBehavior.floating,
        backgroundColor: danger ? AppColors.danger600 : null,
      ),
    );
  }

  Widget _buildCard({
    required String title,
    required Widget child,
    Widget? action,
  }) {
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
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Text(
                title.toUpperCase(),
                style: AppTypography.overline.copyWith(
                  color: AppColors.textSecondary,
                ),
              ),
              ?action,
            ],
          ),
          const SizedBox(height: 12),
          child,
        ],
      ),
    );
  }

  Color _probabilityColor(Deal deal) {
    // For closed deals the probability is meaningless, color by outcome.
    if (deal.isWon) return AppColors.success600;
    if (deal.isLost) return AppColors.danger600;
    final p = deal.probability;
    if (p >= 75) return AppColors.success600;
    if (p >= 50) return AppColors.primary600;
    if (p >= 25) return AppColors.warning600;
    return AppColors.gray500;
  }

  /// Stage-aware copy. Avoids the old screen's bug where a brand-new
  /// Prospecting deal at probability=10 read "Low probability, consider
  /// next steps". The default IS 10, that's not a warning.
  String _probabilityHint(Deal deal) {
    if (deal.isWon) return 'Deal won';
    if (deal.isLost) return 'Deal lost';
    final p = deal.probability;
    // If the value still equals the default for the current stage, treat
    // it as "as expected" rather than rating it on the absolute scale.
    if (p > 0 && p == dealStageProbability(deal.stage, deal.stageKind)) {
      return 'Default for ${deal.stageLabel}';
    }
    if (p >= 75) return 'High chance of winning';
    if (p >= 50) return 'Good progress, keep pushing';
    if (p >= 25) return 'Still early stage';
    return 'Low probability';
  }

  /// Format the deal's main amount using the deal's own currency (not the
  /// org default). Opportunities can be in any currency.
  String _formatDealAmount(Deal deal) =>
      _formatMoney(deal.value, deal.currency);

  String _formatMoney(double value, Currency currency) {
    final symbol = currency.symbol;
    if (value.abs() >= 1000000) {
      return '$symbol${(value / 1000000).toStringAsFixed(1)}M';
    }
    if (value.abs() >= 1000) {
      return '$symbol${(value / 1000).toStringAsFixed(0)}K';
    }
    return '$symbol${value.toStringAsFixed(0)}';
  }

  String _formatDate(DateTime date) {
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
    return '${months[date.month - 1]} ${date.day}, ${date.year}';
  }

  String _formatDaysInStage(Deal deal) {
    final days = deal.daysInStageServer ?? deal.daysInCurrentStage;
    if (days == null) return '';
    if (days == 0) return 'Moved into this stage today';
    if (days == 1) return '1 day in this stage';
    return '$days days in this stage';
  }
}

// =============================================================================
// PRIVATE WIDGETS
// =============================================================================

class _Chip extends StatelessWidget {
  final String label;
  final Color bg;
  final Color fg;
  final IconData? icon;
  const _Chip({
    required this.label,
    required this.bg,
    required this.fg,
    this.icon,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
      decoration: BoxDecoration(
        color: bg,
        borderRadius: BorderRadius.circular(12),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          if (icon != null) ...[
            Icon(icon, size: 12, color: fg),
            const SizedBox(width: 4),
          ],
          Text(
            label,
            style: AppTypography.caption.copyWith(
              color: fg,
              fontWeight: FontWeight.w600,
            ),
          ),
        ],
      ),
    );
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

class _AssigneeRow extends StatelessWidget {
  final Map<String, dynamic> profile;
  const _AssigneeRow({required this.profile});

  @override
  Widget build(BuildContext context) {
    final details = profile['user_details'];
    String name = 'Unknown';
    String? email;
    String? pic;
    if (details is Map<String, dynamic>) {
      final n = (details['name'] as String?)?.trim();
      email = (details['email'] as String?)?.trim();
      pic = (details['profile_pic'] as String?)?.trim();
      if (n != null && n.isNotEmpty) {
        name = n;
      } else if (email != null && email.isNotEmpty) {
        name = email.split('@').first;
      }
    }
    return Row(
      children: [
        UserAvatar(name: name, imageUrl: pic, size: AvatarSize.md),
        const SizedBox(width: 12),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                name,
                style: AppTypography.label,
                overflow: TextOverflow.ellipsis,
              ),
              if (email != null && email.isNotEmpty && email != name)
                Text(
                  email,
                  style: AppTypography.caption.copyWith(
                    color: AppColors.textSecondary,
                  ),
                  overflow: TextOverflow.ellipsis,
                ),
            ],
          ),
        ),
      ],
    );
  }
}

class _EditNoteDialog extends StatefulWidget {
  final String initialText;
  const _EditNoteDialog({required this.initialText});

  @override
  State<_EditNoteDialog> createState() => _EditNoteDialogState();
}

class _EditNoteDialogState extends State<_EditNoteDialog> {
  late final TextEditingController _controller;

  @override
  void initState() {
    super.initState();
    _controller = TextEditingController(text: widget.initialText);
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: const Text('Edit Note'),
      content: TextField(
        controller: _controller,
        autofocus: true,
        maxLines: null,
        minLines: 3,
        decoration: const InputDecoration(
          hintText: 'Update your note...',
          border: OutlineInputBorder(),
        ),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(context),
          child: const Text('Cancel'),
        ),
        TextButton(
          onPressed: () => Navigator.pop(context, _controller.text.trim()),
          child: const Text('Save'),
        ),
      ],
    );
  }
}

class _NoteCard extends StatelessWidget {
  final String text;
  final String author;
  final DateTime timestamp;
  final VoidCallback? onEdit;
  final VoidCallback? onDelete;

  const _NoteCard({
    required this.text,
    required this.author,
    required this.timestamp,
    this.onEdit,
    this.onDelete,
  });

  void _showOptions(BuildContext context) {
    if (onEdit == null && onDelete == null) return;
    showModalBottomSheet(
      context: context,
      backgroundColor: AppColors.surface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (context) => SafeArea(
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
            if (onEdit != null)
              ListTile(
                leading: Icon(LucideIcons.pencil, color: AppColors.primary600),
                title: const Text('Edit Note'),
                onTap: () {
                  Navigator.pop(context);
                  onEdit!();
                },
              ),
            if (onDelete != null)
              ListTile(
                leading: Icon(LucideIcons.trash2, color: AppColors.danger600),
                title: Text(
                  'Delete Note',
                  style: TextStyle(color: AppColors.danger600),
                ),
                onTap: () {
                  Navigator.pop(context);
                  _confirmDelete(context);
                },
              ),
            const SizedBox(height: 8),
          ],
        ),
      ),
    );
  }

  Future<void> _confirmDelete(BuildContext context) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Delete Note?'),
        content: const Text('This action cannot be undone.'),
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
    if (confirmed == true) onDelete?.call();
  }

  @override
  Widget build(BuildContext context) {
    final hasActions = onEdit != null || onDelete != null;
    return GestureDetector(
      onLongPress: hasActions ? () => _showOptions(context) : null,
      child: Container(
        margin: const EdgeInsets.only(bottom: 12),
        padding: const EdgeInsets.all(16),
        decoration: BoxDecoration(
          color: AppColors.gray50,
          borderRadius: AppLayout.borderRadiusLg,
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Expanded(
                  child: Text(
                    text,
                    style: AppTypography.body.copyWith(
                      color: AppColors.textPrimary,
                      height: 1.5,
                    ),
                  ),
                ),
                if (hasActions)
                  SizedBox(
                    width: 32,
                    height: 32,
                    child: IconButton(
                      padding: EdgeInsets.zero,
                      iconSize: 18,
                      visualDensity: VisualDensity.compact,
                      icon: Icon(
                        LucideIcons.moreVertical,
                        color: AppColors.textTertiary,
                      ),
                      tooltip: 'Note options',
                      onPressed: () => _showOptions(context),
                    ),
                  ),
              ],
            ),
            const SizedBox(height: 12),
            Row(
              children: [
                UserAvatar(name: author, size: AvatarSize.xs),
                const SizedBox(width: 8),
                Text(
                  author,
                  style: AppTypography.caption.copyWith(
                    fontWeight: FontWeight.w500,
                  ),
                ),
                const SizedBox(width: 8),
                Text(
                  _relTime(timestamp),
                  style: AppTypography.caption.copyWith(
                    color: AppColors.textTertiary,
                  ),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  String _relTime(DateTime dt) {
    final d = DateTime.now().difference(dt);
    if (d.inDays > 30) return '${(d.inDays / 30).floor()}mo ago';
    if (d.inDays > 0) return '${d.inDays}d ago';
    if (d.inHours > 0) return '${d.inHours}h ago';
    if (d.inMinutes > 0) return '${d.inMinutes}m ago';
    return 'just now';
  }
}

enum _TimelineKind { created, comment, attachment }

class _TimelineEvent {
  final _TimelineKind kind;
  final DateTime ts;
  final String actor;
  final String summary;
  final String? body;

  const _TimelineEvent({
    required this.kind,
    required this.ts,
    required this.actor,
    required this.summary,
    this.body,
  });
}

class _TimelineRow extends StatelessWidget {
  final _TimelineEvent event;
  final bool isLast;
  const _TimelineRow({required this.event, required this.isLast});

  @override
  Widget build(BuildContext context) {
    final (icon, tint) = switch (event.kind) {
      _TimelineKind.created => (LucideIcons.sparkles, AppColors.success600),
      _TimelineKind.comment => (
        LucideIcons.messageSquare,
        AppColors.primary600,
      ),
      _TimelineKind.attachment => (LucideIcons.paperclip, AppColors.warning600),
    };
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
                child: Icon(icon, size: 14, color: tint),
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
                    ],
                  ),
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
