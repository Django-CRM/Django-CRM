import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:intl/intl.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

import '../../core/theme/theme.dart';
import '../../data/models/calendar_feed.dart';
import '../../providers/calendar_feed_provider.dart';

/// Your task calendar feed, mirroring `/profile/calendar-feed` on the web.
///
/// A private URL that Google Calendar, Outlook or Apple Calendar polls for your
/// open tasks with a due date, each an all-day event carrying the title, the
/// priority and a link into the web app. Never notes, and never a contact,
/// account or other record name.
///
/// **The URL is shown once.** The server keeps only its hash. The URL lives in
/// this screen's state after the request that created it and is gone when the
/// screen closes; nothing logs it or stores it.
class CalendarFeedScreen extends ConsumerStatefulWidget {
  const CalendarFeedScreen({super.key});

  @override
  ConsumerState<CalendarFeedScreen> createState() => _CalendarFeedScreenState();
}

class _CalendarFeedScreenState extends ConsumerState<CalendarFeedScreen> {
  String? _url;
  bool _busy = false;

  void _say(String text) {
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(text)));
  }

  Future<bool> _confirm({
    required String title,
    required String body,
    required String action,
  }) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(title),
        content: Text(body),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('Cancel'),
          ),
          TextButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: Text(action),
          ),
        ],
      ),
    );
    return ok == true;
  }

  Future<void> _issue({required bool replacing}) async {
    if (replacing &&
        !await _confirm(
          title: 'Regenerate the URL?',
          body:
              'The old URL stops working at once, and calendars subscribed to '
              'it stop updating. Subscribe again with the new one.',
          action: 'Regenerate',
        )) {
      return;
    }
    setState(() => _busy = true);
    final result = await ref.read(calendarFeedProvider.notifier).issue();
    if (!mounted) return;
    setState(() {
      _busy = false;
      _url = result.url ?? _url;
    });
    if (result.error != null) _say(result.error!);
  }

  Future<void> _disable() async {
    if (!await _confirm(
      title: 'Turn the calendar feed off?',
      body:
          'The URL stops working and subscribed calendars stop updating. You '
          'can turn it on again later with a new URL.',
      action: 'Turn off',
    )) {
      return;
    }
    setState(() => _busy = true);
    final error = await ref.read(calendarFeedProvider.notifier).disable();
    if (!mounted) return;
    setState(() {
      _busy = false;
      if (error == null) _url = null;
    });
    _say(error ?? 'Calendar feed turned off');
  }

  @override
  Widget build(BuildContext context) {
    final async = ref.watch(calendarFeedProvider);
    return Scaffold(
      backgroundColor: AppColors.surfaceDim,
      appBar: AppBar(
        title: const Text('Calendar feed'),
        backgroundColor: AppColors.surface,
        elevation: 0,
        scrolledUnderElevation: 1,
      ),
      body: async.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (_, _) => _ErrorState(
          onRetry: () => ref.read(calendarFeedProvider.notifier).refresh(),
        ),
        data: (feed) => RefreshIndicator(
          onRefresh: () => ref.read(calendarFeedProvider.notifier).refresh(),
          child: ListView(
            padding: const EdgeInsets.only(bottom: 96),
            children: [
              if (_url != null) _Reveal(url: _url!),
              _Summary(feed: feed),
              if (feed.enabled) ...[
                _Row(
                  label: 'Last fetched',
                  detail: feed.lastUsedAt == null
                      ? 'No calendar app has read it yet.'
                      : 'A calendar app read it on '
                            '${DateFormat.yMMMd().add_jm().format(feed.lastUsedAt!)}.',
                ),
                _Actions(
                  children: [
                    OutlinedButton.icon(
                      style: OutlinedButton.styleFrom(
                        minimumSize: const Size(0, 48),
                      ),
                      onPressed: _busy ? null : () => _issue(replacing: true),
                      icon: const Icon(LucideIcons.refreshCw, size: 16),
                      label: const Text('Regenerate URL'),
                    ),
                    OutlinedButton.icon(
                      style: OutlinedButton.styleFrom(
                        minimumSize: const Size(0, 48),
                      ),
                      onPressed: _busy ? null : _disable,
                      icon: const Icon(LucideIcons.power, size: 16),
                      label: const Text('Turn off'),
                    ),
                  ],
                ),
              ] else
                _Actions(
                  children: [
                    FilledButton(
                      style: FilledButton.styleFrom(
                        minimumSize: const Size(0, 48),
                      ),
                      onPressed: _busy ? null : () => _issue(replacing: false),
                      child: const Text('Turn on calendar feed'),
                    ),
                  ],
                ),
              const _SectionHeader('Subscribe to it'),
              const _Row(
                label: 'Google Calendar',
                detail:
                    'On a computer, open Google Calendar. Beside Other '
                    'calendars choose +, then From URL, paste the feed URL and '
                    'choose Add calendar. Google refreshes it every few hours, '
                    'so changes take a while to appear.',
              ),
              const _Row(
                label: 'Outlook',
                detail:
                    'In Outlook on the web choose Add calendar, then Subscribe '
                    'from web, paste the feed URL and choose Import.',
              ),
              const _Row(
                label: 'Apple Calendar',
                detail:
                    'Choose File, then New Calendar Subscription, and paste '
                    'the feed URL.',
              ),
              const _Footnote(),
            ],
          ),
        ),
      ),
    );
  }
}

/// The one and only place the URL appears.
class _Reveal extends StatelessWidget {
  const _Reveal({required this.url});

  final String url;

  @override
  Widget build(BuildContext context) {
    return Container(
      color: AppColors.surface,
      margin: const EdgeInsets.only(bottom: 1),
      padding: const EdgeInsets.fromLTRB(16, 14, 16, 14),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            'Your feed URL, copy it now',
            style: AppTypography.body.copyWith(fontWeight: FontWeight.w600),
          ),
          const SizedBox(height: 4),
          Text(
            'This is the only time it is shown. If you lose it, regenerate it.',
            style: AppTypography.caption.copyWith(
              color: AppColors.textSecondary,
            ),
          ),
          const SizedBox(height: 10),
          Container(
            width: double.infinity,
            padding: const EdgeInsets.all(12),
            decoration: BoxDecoration(
              color: AppColors.surfaceDim,
              borderRadius: BorderRadius.circular(8),
              border: Border.all(color: AppColors.border),
            ),
            child: SelectableText(
              url,
              style: AppTypography.caption.copyWith(
                fontFamily: 'monospace',
                color: AppColors.textPrimary,
              ),
            ),
          ),
          const SizedBox(height: 10),
          FilledButton.icon(
            style: FilledButton.styleFrom(minimumSize: const Size(0, 48)),
            onPressed: () async {
              await Clipboard.setData(ClipboardData(text: url));
              if (!context.mounted) return;
              ScaffoldMessenger.of(
                context,
              ).showSnackBar(const SnackBar(content: Text('Feed URL copied')));
            },
            icon: const Icon(LucideIcons.copy, size: 16),
            label: const Text('Copy URL'),
          ),
        ],
      ),
    );
  }
}

class _Summary extends StatelessWidget {
  const _Summary({required this.feed});

  final CalendarFeed feed;

  @override
  Widget build(BuildContext context) {
    final created = feed.createdAt;
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
              LucideIcons.calendarDays,
              size: 18,
              color: feed.enabled
                  ? AppColors.success600
                  : AppColors.textSecondary,
            ),
          ),
          const SizedBox(width: 10),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  feed.enabled && created != null
                      ? 'On, URL created ${DateFormat.yMMMd().format(created)}'
                      : 'Off',
                  style: AppTypography.body.copyWith(
                    fontWeight: FontWeight.w600,
                  ),
                ),
                const SizedBox(height: 3),
                Text(
                  'Your tasks that are New or In progress and have a due '
                  'date, from 90 days ago to a year ahead, as all-day events. '
                  'Each shows the title, the priority and a link to the task.',
                  style: AppTypography.caption.copyWith(
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
}

class _Actions extends StatelessWidget {
  const _Actions({required this.children});

  final List<Widget> children;

  @override
  Widget build(BuildContext context) {
    return Container(
      color: AppColors.surface,
      margin: const EdgeInsets.only(bottom: 1),
      padding: const EdgeInsets.fromLTRB(16, 10, 16, 12),
      child: Wrap(spacing: 10, runSpacing: 10, children: children),
    );
  }
}

class _Row extends StatelessWidget {
  const _Row({required this.label, required this.detail});

  final String label;
  final String detail;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      color: AppColors.surface,
      margin: const EdgeInsets.only(bottom: 1),
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            label,
            style: AppTypography.body.copyWith(fontWeight: FontWeight.w500),
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
        'The URL is the key. Anyone who has it can see the titles and '
        'priorities of your open tasks without signing in, so share it with '
        'nobody but your calendar app, and regenerate it if it leaks. It stops '
        'working on its own if you leave the organisation or your account is '
        'deactivated.',
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
              'Could not load your calendar feed',
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
