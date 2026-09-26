import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';
import 'package:url_launcher/url_launcher.dart';
import '../../core/permissions.dart';
import '../../core/theme/theme.dart';
import '../../data/models/models.dart';
import '../../data/models/lead_board.dart' show LeadStageRef;
import '../../providers/auth_provider.dart';
import '../../providers/leads_provider.dart';
import '../../providers/lookup_provider.dart';
import '../../config/api_config.dart';
import '../../services/attachment_upload.dart';
import '../../widgets/common/common.dart';

/// Lead Detail Screen
/// Shows lead info with tabs: Overview, Timeline, Notes
class LeadDetailScreen extends ConsumerStatefulWidget {
  final String leadId;

  const LeadDetailScreen({super.key, required this.leadId});

  @override
  ConsumerState<LeadDetailScreen> createState() => _LeadDetailScreenState();
}

class _LeadDetailScreenState extends ConsumerState<LeadDetailScreen>
    with SingleTickerProviderStateMixin {
  late TabController _tabController;
  final TextEditingController _noteController = TextEditingController();

  Lead? _lead;
  List<CustomFieldDefinition> _customFieldDefinitions = const [];
  List<AssignableUser> _assignableUsers = const [];
  LeadStageRef? _stage;
  bool _isLoading = true;
  bool _isAddingNote = false;
  bool _isUploadingAttachment = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    // 4 tabs: Overview, Timeline, Notes, Files. Files always renders so users
    // can recover from "where did that PDF go?" without leaving the app.
    _tabController = TabController(length: 4, vsync: this);
    _fetchLead();
  }

  @override
  void dispose() {
    _tabController.dispose();
    _noteController.dispose();
    super.dispose();
  }

  Future<void> _fetchLead() async {
    // Only show the full-page spinner on the first load. Subsequent refreshes
    // (after add/delete note, status change, pull-to-refresh, returning from
    // edit) update silently so the user keeps their scroll position and
    // selected tab instead of seeing the page blank out.
    final isInitialLoad = _lead == null;
    setState(() {
      if (isInitialLoad) _isLoading = true;
      _error = null;
    });

    final detail = await ref
        .read(leadsProvider.notifier)
        .getLeadDetail(widget.leadId);

    if (mounted) {
      setState(() {
        _isLoading = false;
        if (detail != null) {
          _lead = detail.lead;
          _customFieldDefinitions = detail.customFieldDefinitions;
          _assignableUsers = detail.assignableUsers;
          _stage = detail.stage;
        } else if (isInitialLoad) {
          _error = 'Failed to load lead';
        }
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    // Loading state
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

    // Error or not found state
    if (_lead == null || _error != null) {
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
              Icon(LucideIcons.userX, size: 48, color: AppColors.gray400),
              const SizedBox(height: 16),
              Text('Lead not found', style: AppTypography.h3),
              const SizedBox(height: 8),
              Text(
                _error ?? 'This lead may have been deleted',
                style: AppTypography.body.copyWith(
                  color: AppColors.textSecondary,
                ),
              ),
              const SizedBox(height: 16),
              TextButton(onPressed: _fetchLead, child: const Text('Retry')),
            ],
          ),
        ),
      );
    }

    // Compact header that scales with the user's text size setting without
    // resorting to shrinking content via FittedBox. The natural content
    // (avatar + name + company + status row + quick actions) needs ~285px
    // at default text scale; we add headroom for accessibility scales.
    final textScale = MediaQuery.textScalerOf(context).scale(1.0);
    final expandedHeight = 285 + (60 * (textScale - 1.0).clamp(0.0, 1.0));

    return Scaffold(
      backgroundColor: AppColors.surface,
      body: NestedScrollView(
        headerSliverBuilder: (context, innerBoxIsScrolled) => [
          // App Bar + Header
          SliverAppBar(
            expandedHeight: expandedHeight,
            pinned: true,
            backgroundColor: AppColors.primary50,
            leading: IconButton(
              icon: Container(
                padding: const EdgeInsets.all(8),
                decoration: BoxDecoration(
                  color: Colors.white.withValues(alpha: 0.9),
                  shape: BoxShape.circle,
                ),
                child: const Icon(
                  LucideIcons.chevronLeft,
                  size: 20,
                  color: AppColors.textPrimary,
                ),
              ),
              onPressed: () => context.pop(),
            ),
            actions: [
              IconButton(
                icon: Container(
                  padding: const EdgeInsets.all(8),
                  decoration: BoxDecoration(
                    color: Colors.white.withValues(alpha: 0.9),
                    shape: BoxShape.circle,
                  ),
                  child: const Icon(
                    LucideIcons.pencil,
                    size: 18,
                    color: AppColors.textPrimary,
                  ),
                ),
                onPressed: () async {
                  final result = await context.push(
                    '/leads/${widget.leadId}/edit',
                  );
                  if (result == true && mounted) {
                    _fetchLead(); // Refresh after edit
                  }
                },
              ),
              IconButton(
                icon: Container(
                  padding: const EdgeInsets.all(8),
                  decoration: BoxDecoration(
                    color: Colors.white.withValues(alpha: 0.9),
                    shape: BoxShape.circle,
                  ),
                  child: const Icon(
                    LucideIcons.moreVertical,
                    size: 18,
                    color: AppColors.textPrimary,
                  ),
                ),
                onPressed: () => _showMoreOptions(),
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
                    const Tab(text: 'Timeline'),
                    const Tab(text: 'Notes'),
                    Tab(
                      text: _lead!.attachments.isEmpty
                          ? 'Files'
                          : 'Files (${_lead!.attachments.length})',
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
            _buildTimelineTab(),
            _buildNotesTab(),
            _buildFilesTab(),
          ],
        ),
      ),
    );
  }

  Widget _buildHeader() {
    final lead = _lead!;
    // Salutation only, matching `Lead.__str__` on the server, which composes
    // salutation + first + last. `title` is the lead's subject ("Website
    // Inquiry"), not an honorific, and prefixing it here turned Eric Scott
    // into "Consultation Request Eric Scott" on 21 of this org's 22 leads.
    final salutation = lead.salutation?.trim() ?? '';
    final displayName = salutation.isEmpty
        ? lead.name
        : '$salutation ${lead.name}';
    final subject = lead.title?.trim() ?? '';

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
        bottom: false,
        // FlexibleSpaceBar shrinks the background height as the SliverAppBar
        // collapses on scroll, wrapping the content in a non-scrollable
        // SingleChildScrollView lets it clip cleanly instead of throwing a
        // RenderFlex-overflow assertion mid-collapse.
        child: SingleChildScrollView(
          physics: const NeverScrollableScrollPhysics(),
          padding: const EdgeInsets.fromLTRB(24, 56, 24, 12),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            mainAxisSize: MainAxisSize.min,
            children: [
              UserAvatar(name: lead.name, size: AvatarSize.lg),
              const SizedBox(height: 10),
              Text(
                displayName,
                style: AppTypography.h2.copyWith(color: AppColors.textPrimary),
                textAlign: TextAlign.center,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
              ),
              const SizedBox(height: 2),
              Text(
                lead.company,
                style: AppTypography.caption.copyWith(
                  color: AppColors.textSecondary,
                ),
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
              ),
              // The subject keeps its place on the screen, below the name
              // rather than inside it.
              if (subject.isNotEmpty) ...[
                const SizedBox(height: 2),
                Text(
                  subject,
                  style: AppTypography.caption.copyWith(
                    color: AppColors.textTertiary,
                  ),
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
              ],
              const SizedBox(height: 10),
              // Status badge is tap-to-change so users don't have to dig
              // through the kebab for a workflow they hit constantly.
              Row(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  InkWell(
                    borderRadius: BorderRadius.circular(6),
                    onTap: _showStatusChange,
                    child: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        StatusBadge.fromLeadStatus(lead.status),
                        const SizedBox(width: 2),
                        Icon(
                          LucideIcons.chevronDown,
                          size: 14,
                          color: AppColors.textTertiary,
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(width: 8),
                  _RatingChip(rating: lead.rating),
                ],
              ),
              if (_stage != null) ...[
                const SizedBox(height: 8),
                _StageLine(stage: _stage!),
              ],
              const SizedBox(height: 12),
              Row(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  _QuickActionButton(
                    icon: LucideIcons.phone,
                    label: 'Call',
                    enabled: _hasPhone,
                    onTap: () => _launchPhone(lead.phone),
                  ),
                  const SizedBox(width: 24),
                  _QuickActionButton(
                    icon: LucideIcons.mail,
                    label: 'Email',
                    enabled: _hasEmail,
                    onTap: () => _launchEmail(lead.email),
                  ),
                  const SizedBox(width: 24),
                  _QuickActionButton(
                    icon: LucideIcons.messageSquare,
                    label: 'Message',
                    enabled: _hasPhone,
                    onTap: () => _launchSms(lead.phone),
                  ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildOverviewTab() {
    return RefreshIndicator(
      onRefresh: _fetchLead,
      child: _buildOverviewScroll(),
    );
  }

  Widget _buildOverviewScroll() {
    final lead = _lead!;
    final hasDeal =
        (lead.opportunityAmount != null && lead.opportunityAmount! > 0) ||
        lead.probability != null ||
        lead.closeDate != null;
    final hasAddress = [
      lead.addressLine,
      lead.city,
      lead.state,
      lead.postcode,
      lead.country,
    ].any((s) => s != null && s.trim().isNotEmpty);
    final hasDates =
        lead.lastContacted != null ||
        lead.nextFollowUp != null ||
        lead.updatedAt != null;

    return SingleChildScrollView(
      physics: const AlwaysScrollableScrollPhysics(),
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // Deal card, most valuable info on a lead. Shown first when present.
          if (hasDeal) ...[
            _buildCard(title: 'Deal', child: _buildDealContent(lead)),
            const SizedBox(height: 16),
          ],

          // Contact Information
          _buildCard(
            title: 'Contact Information',
            child: Column(children: _buildContactRows(lead)),
          ),

          const SizedBox(height: 16),

          // Address
          if (hasAddress) ...[
            _buildCard(
              title: 'Address',
              child: _InfoRow(
                icon: LucideIcons.mapPin,
                label: 'LOCATION',
                value: _formatAddress(lead),
              ),
            ),
            const SizedBox(height: 16),
          ],

          // Dates
          if (hasDates) ...[
            _buildCard(title: 'Dates', child: _buildDatesContent(lead)),
            const SizedBox(height: 16),
          ],

          // Assigned To, render all assignees, not just the first one. The
          // backend lets a lead belong to multiple users and the count alone
          // changes who's responsible for follow-up.
          _buildCard(title: 'Assigned To', child: _buildAssigneesContent(lead)),

          const SizedBox(height: 16),

          // Custom fields, org-defined schema, key/value pairs stored on the
          // lead under `custom_fields`. Only shown when the org has at least
          // one definition AND the lead has at least one value.
          if (_hasRenderableCustomFields(lead)) ...[
            _buildCard(
              title: 'Custom Fields',
              child: _buildCustomFieldsContent(lead),
            ),
            const SizedBox(height: 16),
          ],

          // Tags
          if (lead.tags.isNotEmpty) ...[
            _buildCard(
              title: 'Tags',
              child: Wrap(
                spacing: 8,
                runSpacing: 8,
                children: lead.tags
                    .map((tag) => LabelPill(label: tag))
                    .toList(),
              ),
            ),
            const SizedBox(height: 16),
          ],

          // Description Preview
          if (lead.description != null && lead.description!.isNotEmpty)
            _buildCard(
              title: 'Description',
              child: Text(
                lead.description!.length > 200
                    ? '${lead.description!.substring(0, 200)}…'
                    : lead.description!,
                style: AppTypography.body.copyWith(
                  color: AppColors.textSecondary,
                  height: 1.5,
                ),
              ),
            ),

          const SizedBox(height: 24),
        ],
      ),
    );
  }

  Widget _buildDealContent(Lead lead) {
    final rows = <Widget>[];
    if (lead.opportunityAmount != null && lead.opportunityAmount! > 0) {
      rows.add(
        _InfoRow(
          icon: LucideIcons.dollarSign,
          label: 'DEAL VALUE',
          value: _formatMoney(lead.opportunityAmount!, lead.currency),
        ),
      );
    }
    if (lead.probability != null) {
      if (rows.isNotEmpty) rows.add(const Divider(height: 24));
      rows.add(_buildProbabilityRow(lead.probability!));
    }
    if (lead.closeDate != null) {
      if (rows.isNotEmpty) rows.add(const Divider(height: 24));
      rows.add(
        _InfoRow(
          icon: LucideIcons.calendar,
          label: 'CLOSE DATE',
          value: _formatDate(lead.closeDate!),
        ),
      );
    }
    return Column(children: rows);
  }

  Widget _buildProbabilityRow(int probability) {
    final clamped = probability.clamp(0, 100);
    return Row(
      children: [
        Icon(LucideIcons.target, size: 20, color: AppColors.gray400),
        const SizedBox(width: 12),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                'PROBABILITY',
                style: AppTypography.overline.copyWith(
                  color: AppColors.textTertiary,
                  fontSize: 10,
                ),
              ),
              const SizedBox(height: 4),
              Row(
                children: [
                  Text('$probability%', style: AppTypography.body),
                  const SizedBox(width: 12),
                  Expanded(
                    child: ClipRRect(
                      borderRadius: BorderRadius.circular(3),
                      child: LinearProgressIndicator(
                        value: clamped / 100,
                        minHeight: 6,
                        backgroundColor: AppColors.gray100,
                        valueColor: AlwaysStoppedAnimation(
                          AppColors.primary600,
                        ),
                      ),
                    ),
                  ),
                ],
              ),
            ],
          ),
        ),
      ],
    );
  }

  List<Widget> _buildContactRows(Lead lead) {
    final rows = <Widget>[];
    void addRow(Widget w) {
      if (rows.isNotEmpty) rows.add(const Divider(height: 24));
      rows.add(w);
    }

    addRow(
      _InfoRow(
        icon: LucideIcons.mail,
        label: 'EMAIL',
        value: _hasEmail ? lead.email : 'Not provided',
        onTap: _hasEmail ? () => _launchEmail(lead.email) : null,
      ),
    );
    addRow(
      _InfoRow(
        icon: LucideIcons.phone,
        label: 'PHONE',
        value: _hasPhone ? lead.phone! : 'Not provided',
        onTap: _hasPhone ? () => _launchPhone(lead.phone!) : null,
      ),
    );
    if (lead.jobTitle != null && lead.jobTitle!.trim().isNotEmpty) {
      addRow(
        _InfoRow(
          icon: LucideIcons.briefcase,
          label: 'JOB TITLE',
          value: lead.jobTitle!,
        ),
      );
    }
    if (lead.website != null && lead.website!.trim().isNotEmpty) {
      addRow(
        _InfoRow(
          icon: LucideIcons.globe,
          label: 'WEBSITE',
          value: lead.website!,
          onTap: () => _launchWeb(lead.website!),
        ),
      );
    }
    if (lead.linkedinUrl != null && lead.linkedinUrl!.trim().isNotEmpty) {
      addRow(
        _InfoRow(
          icon: LucideIcons.link,
          label: 'LINKEDIN',
          value: lead.linkedinUrl!,
          onTap: () => _launchWeb(lead.linkedinUrl!),
        ),
      );
    }
    addRow(
      _InfoRow(
        icon: LucideIcons.compass,
        label: 'SOURCE',
        value: lead.source.displayName,
      ),
    );
    if (lead.industry != null && lead.industry!.trim().isNotEmpty) {
      addRow(
        _InfoRow(
          icon: LucideIcons.building,
          label: 'INDUSTRY',
          value: lead.industry!,
        ),
      );
    }
    addRow(
      _InfoRow(
        icon: LucideIcons.calendar,
        label: 'CREATED',
        value: _formatDate(lead.createdAt),
      ),
    );
    return rows;
  }

  Widget _buildDatesContent(Lead lead) {
    final rows = <Widget>[];
    void addRow(Widget w) {
      if (rows.isNotEmpty) rows.add(const Divider(height: 24));
      rows.add(w);
    }

    if (lead.lastContacted != null) {
      addRow(
        _InfoRow(
          icon: LucideIcons.clock,
          label: 'LAST CONTACT',
          value: _formatDate(lead.lastContacted!),
        ),
      );
    }
    if (lead.nextFollowUp != null) {
      addRow(
        _InfoRow(
          icon: LucideIcons.calendarClock,
          label: 'NEXT FOLLOW-UP',
          value: _formatDate(lead.nextFollowUp!),
        ),
      );
    }
    if (lead.updatedAt != null) {
      addRow(
        _InfoRow(
          icon: LucideIcons.refreshCw,
          label: 'UPDATED',
          value: _formatDate(lead.updatedAt!),
        ),
      );
    }
    return Column(children: rows);
  }

  Widget _buildAssigneesContent(Lead lead) {
    final assignees = lead.assignedTo ?? const <Map<String, dynamic>>[];
    if (assignees.isEmpty) {
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
        for (var i = 0; i < assignees.length; i++) ...[
          _buildAssigneeRow(assignees[i]),
          if (i < assignees.length - 1) const SizedBox(height: 12),
        ],
      ],
    );
  }

  Widget _buildAssigneeRow(Map<String, dynamic> a) {
    final details = a['user_details'];
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

  bool _hasRenderableCustomFields(Lead lead) {
    if (_customFieldDefinitions.isEmpty) return false;
    for (final def in _customFieldDefinitions) {
      final v = lead.customFieldValues[def.key];
      if (v == null) continue;
      if (v is String && v.trim().isEmpty) continue;
      return true;
    }
    return false;
  }

  Widget _buildCustomFieldsContent(Lead lead) {
    final rows = <Widget>[];
    for (final def in _customFieldDefinitions) {
      final raw = lead.customFieldValues[def.key];
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

  Widget _buildFilesTab() {
    final attachments = _lead!.attachments;
    if (attachments.isEmpty) {
      return RefreshIndicator(
        onRefresh: _fetchLead,
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
                  // This used to read "Attachments uploaded from the web
                  // appear here", which was true and was the whole problem.
                  Text(
                    'Attach a document, a business card, or a photo.',
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
      onRefresh: _fetchLead,
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

  /// Attach a file to this lead.
  ///
  /// The lead detail POST answers with the refreshed `attachments` array, so
  /// the tab updates without a refetch.
  Future<void> _pickAndUploadAttachment() async {
    if (_isUploadingAttachment || _lead == null) return;
    setState(() => _isUploadingAttachment = true);

    final result = await pickAndUploadAttachment(
      target: AttachmentTarget.lead,
      recordId: widget.leadId,
    );

    if (!mounted) return;
    setState(() => _isUploadingAttachment = false);

    if (result.cancelled) return;
    if (!result.succeeded) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(result.error!),
          behavior: SnackBarBehavior.floating,
          backgroundColor: AppColors.danger600,
        ),
      );
      return;
    }

    final attachmentsData = result.data?['attachments'];
    if (attachmentsData is List && _lead != null) {
      setState(() {
        _lead = _lead!.copyWith(
          attachments: attachmentsData
              .whereType<Map<String, dynamic>>()
              .map(Attachment.fromJson)
              .toList(),
        );
      });
    } else {
      await _fetchLead();
    }
  }

  /// Download an attachment through the API, with the token attached. See the
  /// note on `Attachment.filePath` for why the stored path is not a URL.
  Future<void> _openFile(Attachment a) async {
    if (!a.hasFile) {
      _snackBar('That attachment has no file.');
      return;
    }
    final result = await downloadAttachment(
      url: ApiConfig.attachmentDownload(a.id),
      fileName: a.fileName,
    );
    if (!mounted || result.success) return;
    _snackBar(result.error ?? 'Could not download that file.');
  }

  void _snackBar(String message) {
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text(message), behavior: SnackBarBehavior.floating),
    );
  }

  String _formatAddress(Lead lead) {
    final parts = <String>[
      if (lead.addressLine != null && lead.addressLine!.trim().isNotEmpty)
        lead.addressLine!.trim(),
      [
        if (lead.city != null && lead.city!.trim().isNotEmpty)
          lead.city!.trim(),
        if (lead.state != null && lead.state!.trim().isNotEmpty)
          lead.state!.trim(),
        if (lead.postcode != null && lead.postcode!.trim().isNotEmpty)
          lead.postcode!.trim(),
      ].join(' '),
      if (lead.country != null && lead.country!.trim().isNotEmpty)
        lead.country!.trim(),
    ].where((p) => p.isNotEmpty).toList();
    return parts.join('\n');
  }

  String _formatMoney(double amount, String? currency) {
    final code = (currency == null || currency.isEmpty) ? 'USD' : currency;
    final prefix = switch (code) {
      'USD' => '\$',
      'EUR' => '€',
      'GBP' => '£',
      'INR' => '₹',
      'JPY' => '¥',
      _ => '',
    };
    String body;
    if (amount.abs() >= 1000000) {
      body = '${(amount / 1000000).toStringAsFixed(1)}M';
    } else if (amount.abs() >= 10000) {
      body = '${(amount / 1000).toStringAsFixed(0)}K';
    } else if (amount.abs() >= 1000) {
      body = '${(amount / 1000).toStringAsFixed(1)}K';
    } else {
      body = amount.toStringAsFixed(0);
    }
    return prefix.isEmpty ? '$code $body' : '$prefix$body';
  }

  Future<void> _launchWeb(String url) async {
    var normalized = url.trim();
    if (!normalized.contains('://')) normalized = 'https://$normalized';
    final uri = Uri.tryParse(normalized);
    if (uri == null) return;
    await _launch(uri);
  }

  Widget _buildTimelineTab() {
    final events = _buildTimelineEvents();
    if (events.isEmpty) {
      return RefreshIndicator(
        onRefresh: _fetchLead,
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
      onRefresh: _fetchLead,
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

  /// Merge known activity sources into a single, newest-first list:
  /// comments, attachments, and the lead-created event.
  List<_TimelineEvent> _buildTimelineEvents() {
    final lead = _lead!;
    final events = <_TimelineEvent>[
      for (final c in lead.comments)
        _TimelineEvent(
          kind: _TimelineKind.comment,
          ts: c.commentedOn,
          actor: c.authorName,
          summary: 'commented',
          body: c.comment,
        ),
      for (final a in lead.attachments)
        if (a.createdAt != null)
          _TimelineEvent(
            kind: _TimelineKind.attachment,
            ts: a.createdAt!,
            actor: a.createdBy ?? '',
            summary: 'uploaded ${a.fileName}',
          ),
      _TimelineEvent(
        kind: _TimelineKind.created,
        ts: lead.createdAt,
        actor: '',
        summary: 'Lead created',
      ),
    ]..sort((a, b) => b.ts.compareTo(a.ts));
    return events;
  }

  Widget _buildNotesTab() {
    final comments = _lead!.comments;

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
                    // Backend rule (LeadCommentView): only the author or an
                    // org admin can edit/delete. The comment's commented_by
                    // serializer exposes user_details.email, which we match
                    // against the authenticated user's email.
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

        // Add Note Input
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

    final response = await ref
        .read(leadsProvider.notifier)
        .addComment(widget.leadId, text);

    if (!mounted) return;
    setState(() => _isAddingNote = false);

    if (!response.success) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(response.message ?? 'Failed to add note'),
          behavior: SnackBarBehavior.floating,
          backgroundColor: AppColors.danger600,
        ),
      );
      return;
    }

    _noteController.clear();

    // The POST returns the updated comments list, use it directly instead
    // of refetching. The previous refetch path could silently fail (provider
    // catches and returns null on any parse error), leaving the new comment
    // invisible even though the server saved it.
    final commentsData = response.data?['comments'];
    if (_lead != null && commentsData is List) {
      final newComments = commentsData
          .whereType<Map<String, dynamic>>()
          .map(Comment.fromJson)
          .toList();
      setState(() {
        _lead = _lead!.copyWith(comments: newComments);
      });
    } else {
      // Response missing comments (shouldn't happen), fall back to refetch
      // so the user doesn't end up staring at stale data.
      await _fetchLead();
    }

    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(
        content: Text('Note added'),
        behavior: SnackBarBehavior.floating,
      ),
    );
  }

  Future<void> _editComment(String commentId, String currentText) async {
    final newText = await showDialog<String>(
      context: context,
      builder: (context) => _EditNoteDialog(initialText: currentText),
    );

    if (newText == null || newText.isEmpty || newText == currentText.trim()) {
      return;
    }

    final response = await ref
        .read(leadsProvider.notifier)
        .updateComment(commentId, newText);

    if (!mounted) return;
    if (!response.success) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(response.message ?? 'Failed to update note'),
          behavior: SnackBarBehavior.floating,
          backgroundColor: AppColors.danger600,
        ),
      );
      return;
    }

    // PATCH returns only {error, message}, patch the comment locally.
    if (_lead != null) {
      final updated = _lead!.comments.map((c) {
        return c.id == commentId
            ? Comment(
                id: c.id,
                comment: newText,
                commentedOn: c.commentedOn,
                commentedById: c.commentedById,
                commentedByName: c.commentedByName,
                commentedByEmail: c.commentedByEmail,
              )
            : c;
      }).toList();
      setState(() {
        _lead = _lead!.copyWith(comments: updated);
      });
    }

    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(
        content: Text('Note updated'),
        behavior: SnackBarBehavior.floating,
      ),
    );
  }

  Future<void> _deleteComment(String commentId) async {
    final response = await ref
        .read(leadsProvider.notifier)
        .deleteComment(commentId);

    if (!mounted) return;
    if (!response.success) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(response.message ?? 'Failed to delete note'),
          behavior: SnackBarBehavior.floating,
          backgroundColor: AppColors.danger600,
        ),
      );
      return;
    }

    // DELETE returns only {error, message}, remove locally.
    if (_lead != null) {
      final filtered = _lead!.comments.where((c) => c.id != commentId).toList();
      setState(() {
        _lead = _lead!.copyWith(comments: filtered);
      });
    }

    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(
        content: Text('Note deleted'),
        behavior: SnackBarBehavior.floating,
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
                title,
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

  void _showMoreOptions() {
    final isConverted = _lead!.status == LeadStatus.converted;
    // `LeadDetailView.delete` allows admins and the lead's creator only, so an
    // assignee who did not create it must not be offered the action.
    final canDelete = isAdminOrOwner(
      isAdmin: ref.read(isOrgAdminProvider),
      currentUserKey: ref.read(currentUserProvider)?.email,
      ownerKey: _lead!.createdByEmail,
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
              leading: const Icon(LucideIcons.users),
              title: const Text('Assign Users'),
              onTap: () {
                Navigator.pop(context);
                _showAssignSheet();
              },
            ),
            ListTile(
              leading: const Icon(LucideIcons.tags),
              title: const Text('Edit Tags'),
              onTap: () {
                Navigator.pop(context);
                _showTagsSheet();
              },
            ),
            ListTile(
              leading: const Icon(LucideIcons.calendarClock),
              title: const Text('Set Follow-up'),
              onTap: () {
                Navigator.pop(context);
                _pickFollowUpDate();
              },
            ),
            ListTile(
              leading: const Icon(LucideIcons.share2),
              title: const Text('Share'),
              onTap: () {
                Navigator.pop(context);
                _shareLead();
              },
            ),
            if (!isConverted)
              ListTile(
                leading: Icon(
                  LucideIcons.gitBranch,
                  color: AppColors.success600,
                ),
                title: Text(
                  'Convert Lead',
                  style: TextStyle(color: AppColors.success700),
                ),
                onTap: () {
                  Navigator.pop(context);
                  _confirmConvert();
                },
              ),
            if (canDelete)
              ListTile(
                leading: Icon(LucideIcons.trash2, color: AppColors.danger600),
                title: Text(
                  'Delete Lead',
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

  // ----- New actions (batch E) -----

  Future<void> _showAssignSheet() async {
    if (_assignableUsers.isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text('No assignable users available'),
          behavior: SnackBarBehavior.floating,
        ),
      );
      return;
    }
    final selected = Set<String>.from(_lead!.assignedToIds);
    final saved = await showModalBottomSheet<Set<String>>(
      context: context,
      isScrollControlled: true,
      backgroundColor: AppColors.surface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (sheetContext) {
        return _MultiSelectSheet<AssignableUser>(
          title: 'Assign Users',
          items: _assignableUsers,
          initialSelected: selected,
          idOf: (u) => u.id,
          labelOf: (u) => u.label,
          subtitleOf: (u) => null,
          leadingOf: (u) => UserAvatar(
            name: u.label,
            imageUrl: u.profilePic,
            size: AvatarSize.xs,
          ),
        );
      },
    );
    if (saved == null) return;
    await _patchLead({
      'assigned_to': saved.toList(),
    }, successMessage: 'Assignees updated');
  }

  Future<void> _showTagsSheet() async {
    var tags = ref.read(tagsProvider);
    if (tags.isEmpty) {
      // First open of a session. Async notifier hasn't materialised yet.
      // Wait for the next emission instead of opening an empty sheet.
      await ref.read(tagsLookupProvider.future);
      if (!mounted) return;
      tags = ref.read(tagsProvider);
    }
    if (tags.isEmpty) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text('No tags defined for this org'),
          behavior: SnackBarBehavior.floating,
        ),
      );
      return;
    }
    final selected = Set<String>.from(_lead!.tagIds);
    if (!mounted) return;
    final saved = await showModalBottomSheet<Set<String>>(
      context: context,
      isScrollControlled: true,
      backgroundColor: AppColors.surface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (sheetContext) {
        return _MultiSelectSheet<TagLookup>(
          title: 'Edit Tags',
          items: tags,
          initialSelected: selected,
          idOf: (t) => t.id,
          labelOf: (t) => t.name,
          subtitleOf: (t) => null,
          leadingOf: (_) =>
              Icon(LucideIcons.tag, size: 14, color: AppColors.gray500),
        );
      },
    );
    if (saved == null) return;
    await _patchLead({'tags': saved.toList()}, successMessage: 'Tags updated');
  }

  Future<void> _pickFollowUpDate() async {
    final now = DateTime.now();
    final initial =
        _lead!.nextFollowUp ?? DateTime(now.year, now.month, now.day + 7);
    final picked = await showDatePicker(
      context: context,
      initialDate: initial.isBefore(now) ? now : initial,
      firstDate: DateTime(now.year - 1),
      lastDate: DateTime(now.year + 3),
      helpText: 'Set next follow-up',
    );
    if (picked == null) return;
    final iso =
        '${picked.year.toString().padLeft(4, '0')}-${picked.month.toString().padLeft(2, '0')}-${picked.day.toString().padLeft(2, '0')}';
    await _patchLead({
      'next_follow_up': iso,
    }, successMessage: 'Follow-up set for ${_formatDate(picked)}');
  }

  Future<void> _shareLead() async {
    final lead = _lead!;
    // No share_plus dep, copy a useful text block to the clipboard. The user
    // can paste it anywhere (email, Slack, notes).
    final summary = [
      lead.name,
      if (lead.companyName.isNotEmpty) lead.companyName,
      if (lead.jobTitle != null && lead.jobTitle!.isNotEmpty) lead.jobTitle,
      if (_hasEmail) lead.email,
      if (_hasPhone) lead.phone,
    ].whereType<String>().join('\n');
    await Clipboard.setData(ClipboardData(text: summary));
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(
        content: Text('Lead details copied to clipboard'),
        behavior: SnackBarBehavior.floating,
      ),
    );
  }

  void _confirmConvert() {
    showDialog(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: const Text('Convert Lead?'),
        content: const Text(
          'This creates an Account, Contact, and Opportunity from this lead, '
          'then marks the lead as converted. This cannot be undone from the app.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(dialogContext),
            child: const Text('Cancel'),
          ),
          TextButton(
            onPressed: () async {
              Navigator.pop(dialogContext);
              await _convertLead();
            },
            child: Text(
              'Convert',
              style: TextStyle(color: AppColors.success700),
            ),
          ),
        ],
      ),
    );
  }

  Future<void> _convertLead() async {
    final response = await ref
        .read(leadsProvider.notifier)
        .updateLeadStatus(_lead!.id, LeadStatus.converted);
    if (!mounted) return;
    if (response.success) {
      await _fetchLead();
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text('Lead converted'),
          behavior: SnackBarBehavior.floating,
        ),
      );
    } else {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(response.message ?? 'Failed to convert lead'),
          behavior: SnackBarBehavior.floating,
          backgroundColor: AppColors.danger600,
        ),
      );
    }
  }

  Future<void> _patchLead(
    Map<String, dynamic> body, {
    required String successMessage,
  }) async {
    final response = await ref
        .read(leadsProvider.notifier)
        .updateLead(_lead!.id, body);
    if (!mounted) return;
    if (response.success) {
      await _fetchLead();
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(successMessage),
          behavior: SnackBarBehavior.floating,
        ),
      );
    } else {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(response.message ?? 'Update failed'),
          behavior: SnackBarBehavior.floating,
          backgroundColor: AppColors.danger600,
        ),
      );
    }
  }

  void _showStatusChange() {
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
            Padding(
              padding: const EdgeInsets.all(16),
              child: Text('Change Status', style: AppTypography.h3),
            ),
            ...LeadStatus.values.map(
              (status) => ListTile(
                leading: Container(
                  width: 12,
                  height: 12,
                  decoration: BoxDecoration(
                    color: _getStatusColor(status),
                    shape: BoxShape.circle,
                  ),
                ),
                title: Text(status.displayName),
                trailing: _lead!.status == status
                    ? Icon(LucideIcons.check, color: AppColors.primary600)
                    : null,
                onTap: () async {
                  Navigator.pop(context);
                  if (_lead!.status == status) return;

                  final messenger = ScaffoldMessenger.of(context);
                  final response = await ref
                      .read(leadsProvider.notifier)
                      .updateLeadStatus(_lead!.id, status);

                  if (mounted) {
                    if (response.success) {
                      setState(() {
                        _lead = _lead!.copyWith(status: status);
                      });
                      messenger.showSnackBar(
                        SnackBar(
                          content: Text(
                            'Status changed to ${status.displayName}',
                          ),
                          behavior: SnackBarBehavior.floating,
                        ),
                      );
                    } else {
                      messenger.showSnackBar(
                        SnackBar(
                          content: Text(
                            response.message ?? 'Failed to update status',
                          ),
                          behavior: SnackBarBehavior.floating,
                          backgroundColor: AppColors.danger600,
                        ),
                      );
                    }
                  }
                },
              ),
            ),
            const SizedBox(height: 16),
          ],
        ),
      ),
    );
  }

  void _confirmDelete() {
    showDialog(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: const Text('Delete Lead?'),
        content: const Text(
          'This action cannot be undone. Are you sure you want to delete this lead?',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(dialogContext),
            child: const Text('Cancel'),
          ),
          TextButton(
            onPressed: () async {
              Navigator.pop(dialogContext);
              await _deleteLead();
            },
            child: Text('Delete', style: TextStyle(color: AppColors.danger600)),
          ),
        ],
      ),
    );
  }

  Future<void> _deleteLead() async {
    final response = await ref
        .read(leadsProvider.notifier)
        .deleteLead(widget.leadId);

    if (mounted) {
      if (response.success) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text('Lead deleted'),
            behavior: SnackBarBehavior.floating,
          ),
        );
        context.pop();
      } else {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text(response.message ?? 'Failed to delete lead'),
            behavior: SnackBarBehavior.floating,
            backgroundColor: AppColors.danger600,
          ),
        );
      }
    }
  }

  Color _getStatusColor(LeadStatus status) {
    // Use the color defined in the enum
    return status.color;
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

  bool get _hasPhone => _lead?.phone != null && _lead!.phone!.trim().isNotEmpty;
  bool get _hasEmail => (_lead?.email.trim().isNotEmpty ?? false);

  Future<void> _launch(Uri uri, {String? failureLabel}) async {
    // canLaunchUrl returns false on Android 11+ for tel:/mailto:/sms: unless
    // <queries> is declared, so we skip the gate and surface failures via
    // the snackbar, matches the pattern used in LeadCard.
    final ok = await launchUrl(uri);
    if (!ok && mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(failureLabel ?? 'Could not open ${uri.scheme}'),
          behavior: SnackBarBehavior.floating,
        ),
      );
    }
  }

  Future<void> _launchPhone(String? phone) async {
    if (phone == null || phone.trim().isEmpty) return;
    await _launch(Uri(scheme: 'tel', path: phone));
  }

  Future<void> _launchEmail(String email) async {
    if (email.trim().isEmpty) return;
    await _launch(Uri(scheme: 'mailto', path: email));
  }

  Future<void> _launchSms(String? phone) async {
    if (phone == null || phone.trim().isEmpty) return;
    await _launch(Uri(scheme: 'sms', path: phone));
  }
}

/// Quick action button for header
class _QuickActionButton extends StatelessWidget {
  final IconData icon;
  final String label;
  final VoidCallback onTap;
  final bool enabled;

  const _QuickActionButton({
    required this.icon,
    required this.label,
    required this.onTap,
    this.enabled = true,
  });

  @override
  Widget build(BuildContext context) {
    final bg = enabled ? AppColors.primary100 : AppColors.gray100;
    final fg = enabled ? AppColors.primary600 : AppColors.gray400;
    return GestureDetector(
      onTap: enabled ? onTap : null,
      child: Opacity(
        opacity: enabled ? 1 : 0.6,
        child: Column(
          children: [
            Container(
              width: 48,
              height: 48,
              decoration: BoxDecoration(color: bg, shape: BoxShape.circle),
              child: Icon(icon, color: fg, size: 22),
            ),
            const SizedBox(height: 6),
            Text(
              label,
              style: AppTypography.caption.copyWith(
                color: AppColors.textSecondary,
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// Info row widget
class _InfoRow extends StatelessWidget {
  final IconData icon;
  final String label;
  final String value;
  final VoidCallback? onTap;

  const _InfoRow({
    required this.icon,
    required this.label,
    required this.value,
    this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: Row(
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
                Text(
                  value,
                  style: AppTypography.body.copyWith(
                    color: onTap != null
                        ? AppColors.primary600
                        : AppColors.textPrimary,
                  ),
                ),
              ],
            ),
          ),
          if (onTap != null)
            Icon(
              LucideIcons.externalLink,
              size: 16,
              color: AppColors.primary600,
            ),
        ],
      ),
    );
  }
}

/// Dialog for editing an existing note. Owns its TextEditingController so the
/// lifecycle stays tied to the dialog's State, disposing the controller
/// inline after `await showDialog(...)` races the dialog's exit animation
/// (TextField still listens during the dismiss tween) and crashes.
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

/// Note card widget
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

    if (confirmed == true) {
      onDelete?.call();
    }
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
                  _formatTimeAgo(timestamp),
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

/// Generic multi-select bottom sheet used by the Assign and Tags actions.
/// Returns the selected ids (or null if the user dismisses).
class _MultiSelectSheet<T> extends StatefulWidget {
  final String title;
  final List<T> items;
  final Set<String> initialSelected;
  final String Function(T) idOf;
  final String Function(T) labelOf;
  final String? Function(T) subtitleOf;
  final Widget Function(T) leadingOf;

  const _MultiSelectSheet({
    required this.title,
    required this.items,
    required this.initialSelected,
    required this.idOf,
    required this.labelOf,
    required this.subtitleOf,
    required this.leadingOf,
  });

  @override
  State<_MultiSelectSheet<T>> createState() => _MultiSelectSheetState<T>();
}

class _MultiSelectSheetState<T> extends State<_MultiSelectSheet<T>> {
  late Set<String> _selected;
  String _query = '';

  @override
  void initState() {
    super.initState();
    _selected = {...widget.initialSelected};
  }

  @override
  Widget build(BuildContext context) {
    final filtered = _query.isEmpty
        ? widget.items
        : widget.items
              .where(
                (i) => widget
                    .labelOf(i)
                    .toLowerCase()
                    .contains(_query.toLowerCase()),
              )
              .toList();
    return DraggableScrollableSheet(
      expand: false,
      maxChildSize: 0.9,
      initialChildSize: 0.65,
      builder: (context, scrollController) {
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
              padding: const EdgeInsets.fromLTRB(16, 16, 16, 0),
              child: Row(
                children: [
                  Expanded(child: Text(widget.title, style: AppTypography.h3)),
                  TextButton(
                    // Themed buttons carry an infinite minimum width, which a Row
                    // does not bound. Without this the row fails to lay out and the
                    // screen paints nothing. See AppLayout.buttonMinSizeInRow.
                    style: TextButton.styleFrom(
                      minimumSize: AppLayout.buttonMinSizeInRow,
                    ),
                    onPressed: () =>
                        Navigator.pop<Set<String>>(context, _selected),
                    child: const Text('Save'),
                  ),
                ],
              ),
            ),
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 12, 16, 8),
              child: TextField(
                onChanged: (v) => setState(() => _query = v),
                decoration: InputDecoration(
                  hintText: 'Search…',
                  prefixIcon: const Icon(LucideIcons.search, size: 16),
                  filled: true,
                  fillColor: AppColors.gray100,
                  contentPadding: const EdgeInsets.symmetric(
                    horizontal: 12,
                    vertical: 0,
                  ),
                  border: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(10),
                    borderSide: BorderSide.none,
                  ),
                ),
              ),
            ),
            Expanded(
              child: ListView.builder(
                controller: scrollController,
                itemCount: filtered.length,
                itemBuilder: (context, index) {
                  final item = filtered[index];
                  final id = widget.idOf(item);
                  final isSelected = _selected.contains(id);
                  final subtitle = widget.subtitleOf(item);
                  return CheckboxListTile(
                    value: isSelected,
                    onChanged: (v) => setState(() {
                      if (v == true) {
                        _selected.add(id);
                      } else {
                        _selected.remove(id);
                      }
                    }),
                    secondary: widget.leadingOf(item),
                    title: Text(widget.labelOf(item)),
                    subtitle: subtitle != null ? Text(subtitle) : null,
                    controlAffinity: ListTileControlAffinity.trailing,
                  );
                },
              ),
            ),
          ],
        );
      },
    );
  }
}

class _RatingChip extends StatelessWidget {
  final LeadRating rating;
  const _RatingChip({required this.rating});

  @override
  Widget build(BuildContext context) {
    final color = rating.color;
    final icon = switch (rating) {
      LeadRating.cold => LucideIcons.snowflake,
      LeadRating.warm => LucideIcons.thermometer,
      LeadRating.hot => LucideIcons.flame,
    };
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.12),
        borderRadius: BorderRadius.circular(6),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, size: 12, color: color),
          const SizedBox(width: 4),
          Text(
            rating.displayName,
            style: AppTypography.caption.copyWith(
              color: color,
              fontWeight: FontWeight.w600,
            ),
          ),
        ],
      ),
    );
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

/// Which pipeline stage the lead is in. Shown only when it is in one; the
/// stage itself changes on the lead board.
class _StageLine extends StatelessWidget {
  const _StageLine({required this.stage});

  final LeadStageRef stage;

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisAlignment: MainAxisAlignment.center,
      children: [
        Container(
          width: 8,
          height: 8,
          decoration: BoxDecoration(color: stage.color, shape: BoxShape.circle),
        ),
        const SizedBox(width: 6),
        Flexible(
          child: Text(
            stage.pipelineName.isEmpty
                ? stage.name
                : '${stage.name} in ${stage.pipelineName}',
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: AppTypography.caption.copyWith(
              color: AppColors.textSecondary,
            ),
          ),
        ),
      ],
    );
  }
}
