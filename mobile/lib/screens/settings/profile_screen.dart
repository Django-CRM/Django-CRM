import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:intl/intl.dart';
import 'package:go_router/go_router.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';
import '../../core/theme/theme.dart';
import '../../data/models/profile.dart';
import '../../providers/profile_provider.dart';
import '../../providers/settings_provider.dart';
import '../../routes/app_router.dart';
import '../../widgets/common/common.dart';
import '../../widgets/forms/unsaved_changes.dart';

/// Profile view/edit screen.
///
/// API contract: PATCH /api/profile/ accepts `name` (on User) and `phone`
/// (on Profile). Everything else is read-only by API design.
class ProfileScreen extends ConsumerStatefulWidget {
  const ProfileScreen({super.key});

  @override
  ConsumerState<ProfileScreen> createState() => _ProfileScreenState();
}

class _ProfileScreenState extends ConsumerState<ProfileScreen> {
  bool _editing = false;
  bool _saving = false;
  final _nameController = TextEditingController();
  final _phoneController = TextEditingController();

  @override
  void dispose() {
    _nameController.dispose();
    _phoneController.dispose();
    super.dispose();
  }

  void _enterEdit(Profile profile) {
    _nameController.text = profile.user.name ?? '';
    _phoneController.text = profile.phone ?? '';
    setState(() => _editing = true);
  }

  /// Only counts while the fields are actually on screen. Outside edit mode
  /// the controllers still hold whatever was last typed, and prompting about
  /// that on the way out would be nonsense.
  bool get _hasUnsavedChanges {
    if (!_editing) return false;
    final profile = ref.read(profileProvider).value;
    if (profile == null) return false;
    return _nameController.text.trim() != (profile.user.name ?? '') ||
        _phoneController.text.trim() != (profile.phone ?? '');
  }

  void _cancelEdit() {
    FocusScope.of(context).unfocus();
    setState(() => _editing = false);
  }

  Future<void> _save(Profile current) async {
    final name = _nameController.text.trim();
    final phone = _phoneController.text.trim();
    final nameChanged = name != (current.user.name ?? '');
    final phoneChanged = phone != (current.phone ?? '');
    if (!nameChanged && !phoneChanged) {
      _cancelEdit();
      return;
    }

    FocusScope.of(context).unfocus();
    setState(() => _saving = true);
    final ok = await ref
        .read(profileProvider.notifier)
        .save(
          name: nameChanged ? name : null,
          phone: phoneChanged ? phone : null,
        );
    if (!mounted) return;
    setState(() {
      _saving = false;
      if (ok) _editing = false;
    });
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(ok ? 'Profile updated' : 'Could not update profile'),
        behavior: SnackBarBehavior.floating,
        backgroundColor: ok ? AppColors.success600 : AppColors.danger600,
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final async = ref.watch(profileProvider);
    return UnsavedChangesGuard(
      hasUnsavedChanges: () => _hasUnsavedChanges,
      isSaving: _saving,
      child: Scaffold(
        backgroundColor: AppColors.surfaceDim,
        appBar: AppBar(
          title: const Text('Profile'),
          backgroundColor: AppColors.surface,
          elevation: 0,
          scrolledUnderElevation: 1,
          leading: IconButton(
            icon: const Icon(LucideIcons.chevronLeft),
            onPressed: _saving
                ? null
                : () =>
                      leaveForm(context, hasUnsavedChanges: _hasUnsavedChanges),
          ),
          actions: [
            if (async.value != null && !_editing)
              Padding(
                padding: const EdgeInsets.only(right: 8),
                child: IconButton(
                  onPressed: () => _enterEdit(async.value!),
                  icon: const Icon(LucideIcons.edit3, size: 20),
                  tooltip: 'Edit profile',
                  color: AppColors.primary600,
                ),
              ),
          ],
        ),
        body: async.when(
          loading: () => const Center(child: CircularProgressIndicator()),
          error: (e, _) => _buildError(e),
          data: (profile) => _buildBody(profile),
        ),
      ),
    );
  }

  Widget _buildError(Object e) {
    return Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(LucideIcons.alertCircle, size: 40, color: AppColors.gray400),
          const SizedBox(height: 12),
          Text('Could not load profile', style: AppTypography.label),
          const SizedBox(height: 4),
          Text(
            '$e',
            textAlign: TextAlign.center,
            style: AppTypography.caption.copyWith(
              color: AppColors.textSecondary,
            ),
          ),
          const SizedBox(height: 12),
          TextButton(
            onPressed: () => ref.read(profileProvider.notifier).refresh(),
            child: const Text('Retry'),
          ),
        ],
      ),
    );
  }

  Widget _buildBody(Profile profile) {
    return RefreshIndicator(
      onRefresh: () => ref.read(profileProvider.notifier).refresh(),
      child: ListView(
        children: [
          _buildHeader(profile),
          const SizedBox(height: 16),
          if (_editing) _buildEditForm(profile) else _buildReadOnly(profile),
          const SizedBox(height: 24),
        ],
      ),
    );
  }

  Widget _buildHeader(Profile profile) {
    final displayName = profile.user.name?.trim().isNotEmpty == true
        ? profile.user.name!
        : profile.user.displayName;
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 28),
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
      child: Column(
        children: [
          UserAvatar(
            name: displayName,
            imageUrl: profile.user.profilePic,
            size: AvatarSize.xxl,
          ),
          const SizedBox(height: 14),
          Text(
            displayName,
            style: AppTypography.h2,
            textAlign: TextAlign.center,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
          ),
          const SizedBox(height: 4),
          Text(
            profile.user.email,
            style: AppTypography.body.copyWith(color: AppColors.textSecondary),
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
          ),
          if (profile.role != null) ...[
            const SizedBox(height: 8),
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
              decoration: BoxDecoration(
                color: AppColors.primary100,
                borderRadius: BorderRadius.circular(12),
              ),
              child: Text(
                profile.role!,
                style: AppTypography.labelSmall.copyWith(
                  color: AppColors.primary700,
                  letterSpacing: 0.4,
                ),
              ),
            ),
          ],
          if (!_editing) ...[
            const SizedBox(height: 16),
            OutlinedButton.icon(
              onPressed: () => _enterEdit(profile),
              icon: const Icon(LucideIcons.edit3, size: 16),
              label: const Text('Edit profile'),
              style: OutlinedButton.styleFrom(
                foregroundColor: AppColors.primary700,
                backgroundColor: AppColors.surface,
                side: BorderSide(color: AppColors.primary200),
                padding: const EdgeInsets.symmetric(
                  horizontal: 20,
                  vertical: 10,
                ),
                shape: RoundedRectangleBorder(
                  borderRadius: BorderRadius.circular(20),
                ),
              ),
            ),
          ],
        ],
      ),
    );
  }

  Widget _buildReadOnly(Profile profile) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        _sectionHeader('Personal'),
        _InfoRow(
          icon: LucideIcons.user,
          label: 'Name',
          value: profile.user.name?.trim().isNotEmpty == true
              ? profile.user.name!
              : '—',
        ),
        _InfoRow(
          icon: LucideIcons.mail,
          label: 'Email',
          value: profile.user.email,
        ),
        _InfoRow(
          icon: LucideIcons.phone,
          label: 'Phone',
          value: profile.phone?.trim().isNotEmpty == true
              ? profile.phone!
              : '—',
        ),
        _sectionHeader('Organization'),
        if (profile.org != null)
          _InfoRow(
            icon: LucideIcons.building2,
            label: 'Organization',
            value: profile.org!.name,
          ),
        if (profile.role != null)
          _InfoRow(
            icon: LucideIcons.shield,
            label: 'Role',
            value: profile.role!,
          ),
        if (profile.dateOfJoining != null)
          _InfoRow(
            icon: LucideIcons.calendar,
            label: 'Joined',
            value: DateFormat.yMMMMd().format(profile.dateOfJoining!),
          ),
        // The web profile page shows these two; this screen did not, so the
        // same person read differently depending on which client they opened.
        _InfoRow(
          icon: LucideIcons.users,
          label: 'Teams',
          value: profile.teams.isEmpty ? 'None' : profile.teams.join(', '),
        ),
        _InfoRow(
          icon: LucideIcons.logIn,
          label: 'Last signed in',
          value: profile.lastLogin == null
              ? 'Not recorded'
              : DateFormat.yMMMMd().add_jm().format(
                  profile.lastLogin!.toLocal(),
                ),
        ),
        _sectionHeader('Access'),
        _InfoRow(
          icon: LucideIcons.target,
          label: 'Sales',
          value: profile.hasSalesAccess ? 'Enabled' : 'Disabled',
        ),
        _InfoRow(
          icon: LucideIcons.megaphone,
          label: 'Marketing',
          value: profile.hasMarketingAccess ? 'Enabled' : 'Disabled',
        ),
        _sectionHeader('Your account'),
        // Your OWN tokens, not the settings screen: that one is the admin's
        // org-wide oversight list and answers a member 403.
        _LinkRow(
          icon: LucideIcons.keyRound,
          label: 'API tokens',
          detail: 'Each one signs in as you, with your role',
          trailing: _tokenCount(),
          onTap: () => context.push(AppRoutes.profileTokens),
        ),
        _LinkRow(
          icon: LucideIcons.calendarDays,
          label: 'Calendar feed',
          detail:
              'Your open tasks in Google Calendar, Outlook or Apple Calendar',
          onTap: () => context.push(AppRoutes.profileCalendarFeed),
        ),
        _sectionHeader('Where your work shows up'),
        _LinkRow(
          icon: LucideIcons.target,
          label: 'Goals',
          detail: 'Your quota and how it is pacing',
          onTap: () => context.push(AppRoutes.goals),
        ),
        _LinkRow(
          icon: LucideIcons.clock,
          label: 'Timesheet',
          detail: 'Hours you have logged this week',
          onTap: () => context.push(AppRoutes.timesheet),
        ),
        _LinkRow(
          icon: LucideIcons.circleCheck,
          label: 'Tasks',
          detail: 'What is assigned to you',
          onTap: () => context.push(AppRoutes.tasks),
        ),
      ],
    );
  }

  /// How many of your tokens are live, or null while it is still loading.
  ///
  /// Read from the same self-scoped provider the tokens screen uses, so
  /// opening one warms the other rather than asking twice.
  String? _tokenCount() {
    final tokens = ref.watch(myAccessTokensProvider).value;
    if (tokens == null) return null;
    return '${tokens.where((t) => t.isLive).length}';
  }

  Widget _buildEditForm(Profile profile) {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          _sectionHeader('Editable fields', padding: false),
          const SizedBox(height: 8),
          Text(
            'Name and phone are editable. Email and access flags are managed by your administrator.',
            style: AppTypography.caption.copyWith(
              color: AppColors.textSecondary,
            ),
          ),
          const SizedBox(height: 16),
          TextField(
            controller: _nameController,
            textInputAction: TextInputAction.next,
            maxLength: 255,
            decoration: const InputDecoration(
              labelText: 'Full name',
              border: OutlineInputBorder(),
              counterText: '',
              prefixIcon: Icon(LucideIcons.user, size: 18),
            ),
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _phoneController,
            keyboardType: TextInputType.phone,
            textInputAction: TextInputAction.done,
            decoration: const InputDecoration(
              labelText: 'Phone',
              border: OutlineInputBorder(),
              prefixIcon: Icon(LucideIcons.phone, size: 18),
            ),
          ),
          const SizedBox(height: 20),
          Row(
            children: [
              Expanded(
                child: OutlinedButton(
                  onPressed: _saving ? null : _cancelEdit,
                  style: OutlinedButton.styleFrom(
                    minimumSize: const Size.fromHeight(48),
                    shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(10),
                    ),
                  ),
                  child: const Text('Cancel'),
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: FilledButton(
                  onPressed: _saving ? null : () => _save(profile),
                  style: FilledButton.styleFrom(
                    backgroundColor: AppColors.gray900,
                    minimumSize: const Size.fromHeight(48),
                    shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(10),
                    ),
                  ),
                  child: _saving
                      ? const SizedBox(
                          width: 18,
                          height: 18,
                          child: CircularProgressIndicator(
                            strokeWidth: 2,
                            valueColor: AlwaysStoppedAnimation<Color>(
                              Colors.white,
                            ),
                          ),
                        )
                      : const Text('Save changes'),
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }

  Widget _sectionHeader(String title, {bool padding = true}) {
    return Padding(
      padding: EdgeInsets.fromLTRB(padding ? 16 : 0, 16, 16, 6),
      child: Text(
        title.toUpperCase(),
        style: AppTypography.overline.copyWith(
          color: AppColors.textSecondary,
          letterSpacing: 1.2,
        ),
      ),
    );
  }
}

/// A row that goes somewhere. Same shape as `_InfoRow` so the list reads as
/// one thing, with a 56px minimum height because it is a tap target and the
/// info rows around it are not.
class _LinkRow extends StatelessWidget {
  final IconData icon;
  final String label;
  final String detail;
  final String? trailing;
  final VoidCallback onTap;

  const _LinkRow({
    required this.icon,
    required this.label,
    required this.detail,
    required this.onTap,
    this.trailing,
  });

  @override
  Widget build(BuildContext context) {
    return Material(
      color: AppColors.surface,
      child: InkWell(
        onTap: onTap,
        child: Container(
          constraints: const BoxConstraints(minHeight: 56),
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
          decoration: BoxDecoration(
            border: Border(bottom: BorderSide(color: AppColors.gray100)),
          ),
          child: Row(
            children: [
              Icon(icon, size: 18, color: AppColors.textSecondary),
              const SizedBox(width: 14),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      label,
                      style: AppTypography.body.copyWith(
                        fontWeight: FontWeight.w500,
                      ),
                    ),
                    const SizedBox(height: 2),
                    Text(
                      detail,
                      style: AppTypography.caption.copyWith(
                        color: AppColors.textSecondary,
                      ),
                    ),
                  ],
                ),
              ),
              if (trailing != null) ...[
                Text(
                  trailing!,
                  style: AppTypography.body.copyWith(
                    fontWeight: FontWeight.w600,
                  ),
                ),
                const SizedBox(width: 8),
              ],
              Icon(
                LucideIcons.chevronRight,
                size: 18,
                color: AppColors.textTertiary,
              ),
            ],
          ),
        ),
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
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
      decoration: BoxDecoration(
        color: AppColors.surface,
        border: Border(bottom: BorderSide(color: AppColors.gray100)),
      ),
      child: Row(
        children: [
          Icon(icon, size: 18, color: AppColors.textSecondary),
          const SizedBox(width: 14),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  label,
                  style: AppTypography.caption.copyWith(
                    color: AppColors.textSecondary,
                  ),
                ),
                const SizedBox(height: 2),
                Text(
                  value,
                  style: AppTypography.body.copyWith(
                    fontWeight: FontWeight.w500,
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
