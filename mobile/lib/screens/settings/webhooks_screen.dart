import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

import '../../core/theme/theme.dart';
import '../../data/models/webhook.dart';
import '../../providers/auth_provider.dart';
import '../../providers/webhooks_provider.dart';
import '../../routes/app_router.dart';
import 'webhook_form_sheet.dart';

/// Outbound webhooks: the list, mirroring `/settings/webhooks` on the web.
///
/// **Admin-only to read.** Every `/api/webhooks/` route answers a member 403,
/// because a hook URL is often a credential of its own and every endpoint
/// receives copies of records. The screen gates on role rather than issuing a
/// request it knows will fail.
class WebhooksScreen extends ConsumerWidget {
  const WebhooksScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final isAdmin = ref.watch(isOrgAdminProvider);
    final async = isAdmin ? ref.watch(webhooksProvider) : null;
    final canAdd = async?.value != null && !async!.value!.atLimit;

    return Scaffold(
      backgroundColor: AppColors.surfaceDim,
      appBar: AppBar(
        title: const Text('Webhooks'),
        backgroundColor: AppColors.surface,
        elevation: 0,
        scrolledUnderElevation: 1,
      ),
      body: !isAdmin ? const WebhooksMemberNotice() : const _AdminBody(),
      floatingActionButton: canAdd
          ? FloatingActionButton.extended(
              onPressed: () => _create(context, ref),
              icon: const Icon(LucideIcons.plus),
              label: const Text('New webhook'),
            )
          : null,
    );
  }

  Future<void> _create(BuildContext context, WidgetRef ref) async {
    final catalogue = ref.read(webhooksProvider).value?.catalogue ?? const [];
    final draft = await showWebhookFormSheet(context, catalogue: catalogue);
    if (draft == null || !context.mounted) return;
    final result = await ref.read(webhooksProvider.notifier).create(draft);
    if (!context.mounted) return;
    if (result.error != null) {
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(SnackBar(content: Text(result.error!)));
      return;
    }
    if (result.secret != null) {
      await showWebhookSecret(
        context,
        title: 'Webhook added, copy its signing secret now',
        secret: result.secret!,
      );
    }
  }
}

/// The one place a signing secret is shown. Nothing keeps a copy after the
/// dialog closes, and nothing logs it on the way here.
Future<void> showWebhookSecret(
  BuildContext context, {
  required String title,
  required String secret,
}) {
  return showDialog<void>(
    context: context,
    barrierDismissible: false,
    builder: (ctx) => AlertDialog(
      title: Text(title),
      content: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'This is the only time the signing secret is shown. Store it '
              'where your receiver can read it; rotate it if it is lost.',
              style: AppTypography.caption.copyWith(
                color: AppColors.textSecondary,
              ),
            ),
            const SizedBox(height: 14),
            Container(
              width: double.infinity,
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(
                color: AppColors.surfaceDim,
                borderRadius: BorderRadius.circular(8),
                border: Border.all(color: AppColors.border),
              ),
              child: SelectableText(
                secret,
                style: AppTypography.caption.copyWith(
                  fontFamily: 'monospace',
                  color: AppColors.textPrimary,
                ),
              ),
            ),
          ],
        ),
      ),
      actions: [
        TextButton.icon(
          onPressed: () async {
            await Clipboard.setData(ClipboardData(text: secret));
            if (!ctx.mounted) return;
            ScaffoldMessenger.of(
              ctx,
            ).showSnackBar(const SnackBar(content: Text('Secret copied')));
          },
          icon: const Icon(LucideIcons.copy, size: 16),
          label: const Text('Copy'),
        ),
        TextButton(
          onPressed: () => Navigator.pop(ctx),
          child: const Text('Done'),
        ),
      ],
    ),
  );
}

class WebhooksMemberNotice extends StatelessWidget {
  const WebhooksMemberNotice({super.key});

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(LucideIcons.lock, size: 40, color: AppColors.textTertiary),
            const SizedBox(height: 16),
            Text(
              'Administrators only',
              style: AppTypography.h3,
              textAlign: TextAlign.center,
            ),
            const SizedBox(height: 8),
            Text(
              'Webhooks send copies of this organization\'s records to other '
              'services, and a hook URL is often a secret of its own, so only '
              'administrators can see or change them.',
              style: AppTypography.body.copyWith(
                color: AppColors.textSecondary,
              ),
              textAlign: TextAlign.center,
            ),
          ],
        ),
      ),
    );
  }
}

class _AdminBody extends ConsumerWidget {
  const _AdminBody();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final async = ref.watch(webhooksProvider);
    return async.when(
      loading: () => const Center(child: CircularProgressIndicator()),
      error: (_, _) => WebhookErrorState(
        message: 'Could not load webhooks',
        onRetry: () => ref.read(webhooksProvider.notifier).refresh(),
      ),
      data: (state) => RefreshIndicator(
        onRefresh: () => ref.read(webhooksProvider.notifier).refresh(),
        child: ListView(
          padding: const EdgeInsets.only(bottom: 96),
          children: [
            if (state.endpoints.isEmpty)
              const _Empty()
            else
              for (final endpoint in state.endpoints)
                _EndpointRow(endpoint: endpoint),
            if (state.atLimit)
              Padding(
                padding: const EdgeInsets.fromLTRB(16, 14, 16, 0),
                child: Text(
                  'An organization can have at most ${state.limit} webhooks. '
                  'Remove one to add another.',
                  style: AppTypography.caption.copyWith(
                    color: AppColors.textSecondary,
                  ),
                ),
              ),
            const _Footnote(),
          ],
        ),
      ),
    );
  }
}

class _EndpointRow extends StatelessWidget {
  const _EndpointRow({required this.endpoint});

  final WebhookEndpoint endpoint;

  @override
  Widget build(BuildContext context) {
    return Material(
      color: AppColors.surface,
      child: InkWell(
        onTap: () => context.push(AppRoutes.settingsWebhook(endpoint.id)),
        child: Container(
          constraints: const BoxConstraints(minHeight: 56),
          padding: const EdgeInsets.fromLTRB(16, 12, 16, 12),
          decoration: BoxDecoration(
            border: Border(bottom: BorderSide(color: AppColors.gray100)),
          ),
          child: Row(
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      endpoint.title,
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                      style: AppTypography.body.copyWith(
                        fontWeight: FontWeight.w500,
                      ),
                    ),
                    const SizedBox(height: 3),
                    Text(
                      endpoint.url,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: AppTypography.caption.copyWith(
                        color: AppColors.textSecondary,
                      ),
                    ),
                    const SizedBox(height: 3),
                    Text(
                      '${endpoint.formatLabel} · ${endpoint.events.length} '
                      '${endpoint.events.length == 1 ? 'event' : 'events'}',
                      style: AppTypography.caption.copyWith(
                        color: AppColors.textTertiary,
                      ),
                    ),
                  ],
                ),
              ),
              const SizedBox(width: 10),
              WebhookStatePill(endpoint: endpoint),
            ],
          ),
        ),
      ),
    );
  }
}

class WebhookStatePill extends StatelessWidget {
  const WebhookStatePill({super.key, required this.endpoint});

  final WebhookEndpoint endpoint;

  @override
  Widget build(BuildContext context) {
    final (label, color) = endpoint.isActive
        ? ('Sending', AppColors.success600)
        : endpoint.disabledReason.isNotEmpty
        ? ('Turned off', AppColors.warning600)
        : ('Off', AppColors.textSecondary);
    return Container(
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
    );
  }
}

class _Empty extends StatelessWidget {
  const _Empty();

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(24, 40, 24, 16),
      child: Column(
        children: [
          Icon(LucideIcons.webhook, size: 40, color: AppColors.textTertiary),
          const SizedBox(height: 14),
          Text(
            'No webhooks yet',
            style: AppTypography.h3,
            textAlign: TextAlign.center,
          ),
          const SizedBox(height: 8),
          Text(
            'A webhook posts to a URL whenever a lead, deal, ticket or other '
            'record changes. Point one at Zapier, n8n, Slack or your own code.',
            style: AppTypography.body.copyWith(color: AppColors.textSecondary),
            textAlign: TextAlign.center,
          ),
        ],
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
        'Every JSON delivery carries an X-BottleCRM-Signature header: an '
        'HMAC-SHA256 of the timestamp and the raw body, keyed with the '
        'webhook\'s secret. A failed delivery is retried five times over about '
        'eight and a half hours, and an endpoint that answers 410 Gone is '
        'turned off.',
        style: AppTypography.caption.copyWith(color: AppColors.textTertiary),
      ),
    );
  }
}

class WebhookErrorState extends StatelessWidget {
  const WebhookErrorState({
    super.key,
    required this.message,
    required this.onRetry,
  });

  final String message;
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
              message,
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
