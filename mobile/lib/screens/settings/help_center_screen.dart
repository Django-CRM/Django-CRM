import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../core/theme/theme.dart';
import '../../data/models/help_center_settings.dart';
import '../../providers/help_center_provider.dart';

/// The public help center: one switch and one address, mirroring
/// `/settings/help-center` on the web.
///
/// The public pages themselves are web-only by design (they are indexable web
/// pages for the org's customers); this screen is the admin's switch for them
/// and a way to open the live page in the browser.
///
/// Any member can read it. The form is shown only when the API's `can_edit`
/// says so, which is a courtesy: `HelpCenterSettingsView.patch` refuses a
/// member whatever this screen shows.
class HelpCenterScreen extends ConsumerWidget {
  const HelpCenterScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final async = ref.watch(helpCenterSettingsProvider);
    return Scaffold(
      backgroundColor: AppColors.surfaceDim,
      appBar: AppBar(
        title: const Text('Help center'),
        backgroundColor: AppColors.surface,
        elevation: 0,
        scrolledUnderElevation: 1,
      ),
      body: async.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (_, _) => _ErrorState(
          onRetry: () =>
              ref.read(helpCenterSettingsProvider.notifier).refresh(),
        ),
        data: (settings) => RefreshIndicator(
          onRefresh: () =>
              ref.read(helpCenterSettingsProvider.notifier).refresh(),
          child: ListView(
            padding: const EdgeInsets.only(bottom: 96),
            children: [
              _Summary(settings: settings),
              const _SectionHeader('Current setting'),
              _Row(
                label: 'Public help center',
                detail: 'Off means every public page answers "not found".',
                value: settings.enabled ? 'On' : 'Off',
              ),
              _Row(
                label: 'Address',
                detail: settings.publicUrl ?? 'Not chosen yet',
              ),
              if (settings.isLive) _OpenLink(url: settings.publicUrl!),
              if (settings.canEdit) ...[
                const _SectionHeader('Change'),
                _HelpCenterForm(settings: settings),
              ],
              const _Footnote(),
            ],
          ),
        ),
      ),
    );
  }
}

class _Summary extends StatelessWidget {
  const _Summary({required this.settings});

  final HelpCenterSettings settings;

  @override
  Widget build(BuildContext context) {
    return Container(
      color: AppColors.surface,
      padding: const EdgeInsets.fromLTRB(16, 14, 16, 14),
      margin: const EdgeInsets.only(bottom: 1),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Padding(
            padding: const EdgeInsets.only(top: 2),
            child: Icon(
              LucideIcons.globe,
              size: 18,
              color: settings.isLive
                  ? AppColors.success600
                  : AppColors.textSecondary,
            ),
          ),
          const SizedBox(width: 10),
          Expanded(
            child: Text(
              settings.isLive
                  ? 'Public, and open to search engines.'
                  : 'Off. Nothing is published.',
              style: AppTypography.body,
            ),
          ),
        ],
      ),
    );
  }
}

class _Row extends StatelessWidget {
  const _Row({required this.label, required this.detail, this.value});

  final String label;
  final String detail;
  final String? value;

  @override
  Widget build(BuildContext context) {
    return Container(
      color: AppColors.surface,
      margin: const EdgeInsets.only(bottom: 1),
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  label,
                  style: AppTypography.body.copyWith(
                    fontWeight: FontWeight.w500,
                  ),
                ),
              ),
              if (value != null) ...[
                const SizedBox(width: 12),
                Text(
                  value!,
                  style: AppTypography.body.copyWith(
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ],
            ],
          ),
          const SizedBox(height: 3),
          Text(
            detail,
            style: AppTypography.caption.copyWith(
              color: AppColors.textSecondary,
            ),
          ),
        ],
      ),
    );
  }
}

class _OpenLink extends StatelessWidget {
  const _OpenLink({required this.url});

  final String url;

  Future<void> _open(BuildContext context) async {
    final uri = Uri.tryParse(url);
    final opened =
        uri != null &&
        (uri.scheme == 'https' || uri.scheme == 'http') &&
        await launchUrl(uri, mode: LaunchMode.externalApplication);
    if (!opened && context.mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Could not open the public page')),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    return Container(
      color: AppColors.surface,
      margin: const EdgeInsets.only(bottom: 1),
      padding: const EdgeInsets.fromLTRB(16, 8, 16, 12),
      child: Align(
        alignment: Alignment.centerLeft,
        child: OutlinedButton.icon(
          style: OutlinedButton.styleFrom(minimumSize: const Size(0, 48)),
          onPressed: () => _open(context),
          icon: const Icon(LucideIcons.externalLink, size: 16),
          label: const Text('Open the public page'),
        ),
      ),
    );
  }
}

/// Admin-only. Sends both fields together; every rule on the address is the
/// API's, and its sentence is shown under the field.
class _HelpCenterForm extends ConsumerStatefulWidget {
  const _HelpCenterForm({required this.settings});

  final HelpCenterSettings settings;

  @override
  ConsumerState<_HelpCenterForm> createState() => _HelpCenterFormState();
}

class _HelpCenterFormState extends ConsumerState<_HelpCenterForm> {
  late bool _enabled = widget.settings.enabled;
  late final TextEditingController _slug = TextEditingController(
    text: widget.settings.slug ?? '',
  );
  bool _saving = false;
  String? _error;

  @override
  void dispose() {
    _slug.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    setState(() {
      _saving = true;
      _error = null;
    });
    final error = await ref
        .read(helpCenterSettingsProvider.notifier)
        .save(enabled: _enabled, slug: _slug.text);
    if (!mounted) return;
    setState(() {
      _saving = false;
      _error = error;
    });
    if (error == null) {
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(const SnackBar(content: Text('Help center saved')));
    }
  }

  @override
  Widget build(BuildContext context) {
    // Material, not a coloured Container: the switch row paints its ink on
    // the nearest Material, and a ColoredBox in between would hide it.
    return Material(
      color: AppColors.surface,
      child: Padding(
        padding: const EdgeInsets.fromLTRB(16, 4, 16, 16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            SwitchListTile(
              contentPadding: EdgeInsets.zero,
              title: const Text('Public help center'),
              subtitle: const Text(
                'Publish approved, published articles for anyone to read.',
              ),
              value: _enabled,
              onChanged: _saving ? null : (v) => setState(() => _enabled = v),
            ),
            const SizedBox(height: 8),
            TextField(
              controller: _slug,
              enabled: !_saving,
              autocorrect: false,
              enableSuggestions: false,
              maxLength: 50,
              textInputAction: TextInputAction.done,
              decoration: InputDecoration(
                labelText: 'Address',
                prefixText: '/help-center/',
                hintText: 'your-company',
                helperText:
                    '3 to 50 lowercase letters, numbers and single hyphens. '
                    'Required before the help center can be switched on.',
                helperMaxLines: 3,
                errorText: _error,
                errorMaxLines: 3,
              ),
            ),
            const SizedBox(height: 12),
            FilledButton(
              style: FilledButton.styleFrom(minimumSize: const Size(0, 48)),
              onPressed: _saving ? null : _save,
              child: Text(_saving ? 'Saving...' : 'Save'),
            ),
          ],
        ),
      ),
    );
  }
}

class _SectionHeader extends StatelessWidget {
  const _SectionHeader(this.title);

  final String title;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 22, 16, 8),
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

class _Footnote extends StatelessWidget {
  const _Footnote();

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 20, 16, 0),
      child: Text(
        'Only articles that are both approved and published are shown, the '
        'same ones customers see in the portal. Drafts, tags, linked tickets '
        'and who wrote an article never are. Search engines can list these '
        'pages.',
        style: AppTypography.caption.copyWith(color: AppColors.textTertiary),
      ),
    );
  }
}

class _ErrorState extends StatelessWidget {
  const _ErrorState({required this.onRetry});

  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(
              LucideIcons.triangleAlert,
              size: 40,
              color: AppColors.textTertiary,
            ),
            const SizedBox(height: 16),
            Text(
              'Could not load the help center',
              style: AppTypography.body,
              textAlign: TextAlign.center,
            ),
            const SizedBox(height: 16),
            OutlinedButton(onPressed: onRetry, child: const Text('Try again')),
          ],
        ),
      ),
    );
  }
}
