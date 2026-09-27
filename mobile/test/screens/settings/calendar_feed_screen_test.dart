import 'package:bottle_crm/data/models/calendar_feed.dart';
import 'package:bottle_crm/providers/calendar_feed_provider.dart';
import 'package:bottle_crm/screens/settings/calendar_feed_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

/// Your task calendar feed on the phone.
///
/// Rendered at 390px and at 1.3x text, where a row that only just fits at 1.0
/// overflows and Flutter reports it as an exception. Beyond layout: the URL
/// appears only in the response to turning the feed on, regenerating asks
/// first, and turning it off drops the URL from the screen.
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  void useViewport(
    WidgetTester tester, {
    required Size size,
    double textScale = 1.0,
  }) {
    tester.view.devicePixelRatio = 3.0;
    tester.view.physicalSize = Size(size.width * 3, size.height * 3);
    tester.platformDispatcher.textScaleFactorTestValue = textScale;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    addTearDown(tester.platformDispatcher.clearTextScaleFactorTestValue);
  }

  const phone = Size(390, 844);
  const tablet = Size(834, 1112);
  const url =
      'https://api.example.com/api/public/calendar/'
      'bcrm_cal_AbCdEfGhIjKlMnOpQrStUvWxYz0123456789-_abcdefg.ics';

  final on = CalendarFeed(
    enabled: true,
    createdAt: DateTime(2026, 9, 27, 10),
    lastUsedAt: DateTime(2026, 9, 27, 11, 30),
  );
  const off = CalendarFeed();

  Future<_FakeFeed> pump(
    WidgetTester tester,
    CalendarFeed feed, {
    Size size = phone,
    double textScale = 1.0,
  }) async {
    useViewport(tester, size: size, textScale: textScale);
    final fake = _FakeFeed(feed);
    await tester.pumpWidget(
      ProviderScope(
        overrides: [calendarFeedProvider.overrideWith(() => fake)],
        child: const MaterialApp(home: CalendarFeedScreen()),
      ),
    );
    await tester.pumpAndSettle();
    return fake;
  }

  for (final scale in [1.0, 1.3]) {
    testWidgets('off fits a 390px phone at ${scale}x text', (tester) async {
      await pump(tester, off, textScale: scale);
      expect(tester.takeException(), isNull);
      expect(find.text('Turn on calendar feed'), findsOneWidget);
    });

    testWidgets(
      'on, with the URL shown, fits a 390px phone at ${scale}x text',
      (tester) async {
        await pump(tester, off, textScale: scale);
        await tester.tap(find.text('Turn on calendar feed'));
        await tester.pumpAndSettle();
        expect(tester.takeException(), isNull);
        expect(find.text(url), findsOneWidget);
        await tester.scrollUntilVisible(
          find.text('Turn off'),
          200,
          scrollable: find.byType(Scrollable).first,
        );
        expect(tester.takeException(), isNull);
      },
    );
  }

  testWidgets('holds up at tablet width', (tester) async {
    await pump(tester, on, size: tablet, textScale: 1.3);
    expect(tester.takeException(), isNull);
  });

  testWidgets('an existing feed never shows a URL', (tester) async {
    await pump(tester, on);
    expect(find.textContaining('https://'), findsNothing);
    expect(find.text('Regenerate URL'), findsOneWidget);
    expect(find.text('Turn off'), findsOneWidget);
    expect(find.text('Turn on calendar feed'), findsNothing);
  });

  testWidgets('turning it on shows the URL once, with copy', (tester) async {
    final fake = await pump(tester, off);
    await tester.tap(find.text('Turn on calendar feed'));
    await tester.pumpAndSettle();
    expect(fake.issued, 1);
    expect(find.text(url), findsOneWidget);
    expect(find.text('Copy URL'), findsOneWidget);
  });

  testWidgets('regenerate asks first, and cancel changes nothing', (
    tester,
  ) async {
    final fake = await pump(tester, on);
    await tester.tap(find.text('Regenerate URL'));
    await tester.pumpAndSettle();
    expect(find.text('Regenerate the URL?'), findsOneWidget);
    await tester.tap(find.text('Cancel'));
    await tester.pumpAndSettle();
    expect(fake.issued, 0);

    await tester.tap(find.text('Regenerate URL'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Regenerate'));
    await tester.pumpAndSettle();
    expect(fake.issued, 1);
    expect(find.text(url), findsOneWidget);
  });

  testWidgets('turning it off asks first and drops the URL', (tester) async {
    final fake = await pump(tester, off);
    await tester.tap(find.text('Turn on calendar feed'));
    await tester.pumpAndSettle();
    await tester.scrollUntilVisible(
      find.text('Turn off'),
      200,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.tap(find.text('Turn off'));
    await tester.pumpAndSettle();
    expect(find.text('Turn the calendar feed off?'), findsOneWidget);
    await tester.tap(find.widgetWithText(TextButton, 'Turn off'));
    await tester.pumpAndSettle();
    expect(fake.disabled, 1);
    expect(find.text(url), findsNothing);
    expect(find.text('Turn on calendar feed'), findsOneWidget);
  });

  group('CalendarFeed.fromJson', () {
    test('reads the API payload', () {
      final feed = CalendarFeed.fromJson(const {
        'enabled': true,
        'created_at': '2026-09-27T10:00:00Z',
        'last_used_at': null,
      });
      expect(feed.enabled, isTrue);
      expect(feed.createdAt, DateTime.utc(2026, 9, 27, 10).toLocal());
      expect(feed.lastUsedAt, isNull);
    });

    test('a disabled payload is off', () {
      final feed = CalendarFeed.fromJson(const {
        'enabled': false,
        'created_at': null,
        'last_used_at': null,
      });
      expect(feed.enabled, isFalse);
      expect(feed.createdAt, isNull);
    });
  });
}

class _FakeFeed extends CalendarFeedNotifier {
  _FakeFeed(this._feed);

  final CalendarFeed _feed;
  int issued = 0;
  int disabled = 0;

  @override
  Future<CalendarFeed> build() async => _feed;

  @override
  Future<({String? error, String? url})> issue() async {
    issued += 1;
    state = AsyncValue.data(
      CalendarFeed(enabled: true, createdAt: DateTime(2026, 9, 27, 12)),
    );
    return (
      error: null,
      url:
          'https://api.example.com/api/public/calendar/'
          'bcrm_cal_AbCdEfGhIjKlMnOpQrStUvWxYz0123456789-_abcdefg.ics',
    );
  }

  @override
  Future<String?> disable() async {
    disabled += 1;
    state = const AsyncValue.data(CalendarFeed());
    return null;
  }
}
