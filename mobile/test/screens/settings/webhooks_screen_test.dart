import 'package:bottle_crm/data/models/webhook.dart';
import 'package:bottle_crm/providers/auth_provider.dart';
import 'package:bottle_crm/providers/webhooks_provider.dart';
import 'package:bottle_crm/screens/settings/webhook_detail_screen.dart';
import 'package:bottle_crm/screens/settings/webhook_form_sheet.dart';
import 'package:bottle_crm/screens/settings/webhooks_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

/// Outbound webhooks on the phone: list, detail and the form sheet, each at a
/// 390px phone with 1.0x and 1.3x text (Flutter reports an overflow as an
/// exception `takeException` returns), plus the member gate and the parts of
/// the models the screens lean on.
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  void useViewport(
    WidgetTester tester, {
    Size size = const Size(390, 844),
    double textScale = 1.0,
  }) {
    tester.view.devicePixelRatio = 3.0;
    tester.view.physicalSize = Size(size.width * 3, size.height * 3);
    tester.platformDispatcher.textScaleFactorTestValue = textScale;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    addTearDown(tester.platformDispatcher.clearTextScaleFactorTestValue);
  }

  const catalogue = [
    WebhookEventGroup(
      module: 'lead',
      label: 'Leads',
      events: ['lead.created', 'lead.updated', 'lead.deleted'],
    ),
    WebhookEventGroup(
      module: 'ticket',
      label: 'Tickets',
      events: ['ticket.created', 'ticket.updated', 'ticket.comment_added'],
    ),
  ];

  const live = WebhookEndpoint(
    id: 'w1',
    url:
        'https://hooks.zapier.com/hooks/catch/1234567/abcdefghijklmnopqrstu/'
        'a-very-long-path-that-must-not-overflow',
    description: 'Zapier: new leads into the regional sales spreadsheet',
    events: ['lead.created', 'ticket.comment_added'],
    secretHint: 'whsec_...a1b2',
  );
  const gone = WebhookEndpoint(
    id: 'w2',
    url: 'https://hooks.slack.com/services/T0/B0/X',
    format: WebhookEndpoint.formatSlack,
    events: ['lead.created'],
    isActive: false,
    disabledReason: 'The endpoint answered 410 Gone, so it was turned off.',
    secretHint: 'whsec_...zz99',
  );
  const state = WebhooksState(
    endpoints: [live, gone],
    catalogue: catalogue,
    limit: 10,
  );

  final deliveries = WebhookDeliveryPage(
    count: 45,
    deliveries: [
      WebhookDelivery.fromJson(const {
        'id': 'd1',
        'event': 'ticket.comment_added',
        'status': 'failed',
        'attempts': 6,
        'response_status': 503,
        'error': 'The endpoint answered HTTP 503.',
      }),
      WebhookDelivery.fromJson(const {
        'id': 'd2',
        'event': 'ping',
        'status': 'pending',
        'attempts': 0,
      }),
      WebhookDelivery.fromJson(const {
        'id': 'd3',
        'event': 'lead.created',
        'status': 'succeeded',
        'attempts': 1,
        'response_status': 200,
      }),
    ],
  );

  Future<void> pump(
    WidgetTester tester,
    Widget home, {
    bool admin = true,
    double textScale = 1.0,
    Size size = const Size(390, 844),
  }) async {
    useViewport(tester, size: size, textScale: textScale);
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          isOrgAdminProvider.overrideWithValue(admin),
          webhooksProvider.overrideWith(() => _FakeWebhooks(state)),
          webhookDeliveriesProvider.overrideWith(
            (ref, key) async => deliveries,
          ),
        ],
        child: MaterialApp(home: home),
      ),
    );
    await tester.pumpAndSettle();
  }

  for (final scale in [1.0, 1.3]) {
    testWidgets('list fits a 390px phone at ${scale}x text', (tester) async {
      await pump(tester, const WebhooksScreen(), textScale: scale);
      expect(tester.takeException(), isNull);
      expect(find.text(live.description), findsOneWidget);
      expect(find.text('Sending'), findsOneWidget);
      expect(find.text('Turned off'), findsOneWidget);
      expect(find.text('New webhook'), findsOneWidget);
    });

    testWidgets('detail fits a 390px phone at ${scale}x text', (tester) async {
      await pump(
        tester,
        const WebhookDetailScreen(webhookId: 'w1'),
        textScale: scale,
      );
      expect(tester.takeException(), isNull);
      expect(find.text('Send test'), findsOneWidget);
      expect(find.text('Rotate secret'), findsOneWidget);
      await tester.scrollUntilVisible(
        find.text('Older'),
        300,
        scrollable: find.byType(Scrollable).first,
      );
      expect(tester.takeException(), isNull);
    });

    testWidgets('form sheet fits a 390px phone at ${scale}x text', (
      tester,
    ) async {
      await pump(
        tester,
        const Scaffold(body: WebhookFormSheet(catalogue: catalogue)),
        textScale: scale,
      );
      expect(tester.takeException(), isNull);
      expect(find.text('Comment added'), findsOneWidget);
    });
  }

  testWidgets('list and detail hold up at tablet width', (tester) async {
    await pump(
      tester,
      const WebhooksScreen(),
      size: const Size(834, 1112),
      textScale: 1.3,
    );
    expect(tester.takeException(), isNull);
  });

  testWidgets('a member sees the notice and never the list', (tester) async {
    await pump(tester, const WebhooksScreen(), admin: false);
    expect(find.text('Administrators only'), findsOneWidget);
    expect(find.text(live.description), findsNothing);
    expect(find.text('New webhook'), findsNothing);
  });

  testWidgets('a member opening a detail link gets the notice too', (
    tester,
  ) async {
    await pump(
      tester,
      const WebhookDetailScreen(webhookId: 'w1'),
      admin: false,
    );
    expect(find.text('Administrators only'), findsOneWidget);
    expect(find.text('Send test'), findsNothing);
  });

  testWidgets('only settled deliveries offer Redeliver', (tester) async {
    await pump(tester, const WebhookDetailScreen(webhookId: 'w1'));
    await tester.scrollUntilVisible(
      find.text('Older'),
      300,
      scrollable: find.byType(Scrollable).first,
    );
    // Failed and succeeded, not the pending ping.
    expect(find.text('Redeliver'), findsNWidgets(2));
  });

  testWidgets('a turned-off endpoint offers no test and no redeliver', (
    tester,
  ) async {
    await pump(tester, const WebhookDetailScreen(webhookId: 'w2'));
    expect(find.text('Send test'), findsNothing);
    expect(find.text('Turn on'), findsOneWidget);
    expect(find.textContaining('410 Gone'), findsOneWidget);
    expect(find.text('Redeliver'), findsNothing);
  });

  testWidgets('the form refuses http and an empty event list', (tester) async {
    await pump(
      tester,
      const Scaffold(body: WebhookFormSheet(catalogue: catalogue)),
    );
    final url = find.widgetWithText(TextField, 'Endpoint URL');
    final submit = find.text('Add webhook');

    await tester.enterText(url, 'http://hooks.example.com/in');
    await tester.ensureVisible(submit);
    await tester.pumpAndSettle();
    await tester.tap(submit);
    await tester.pump();
    expect(find.text('The URL must start with https://.'), findsOneWidget);

    await tester.ensureVisible(url);
    await tester.pumpAndSettle();
    await tester.enterText(url, 'https://hooks.example.com/in');
    await tester.ensureVisible(submit);
    await tester.pumpAndSettle();
    await tester.tap(submit);
    await tester.pump();
    expect(find.text('Choose at least one event.'), findsOneWidget);
  });

  group('models', () {
    test('an endpoint never carries a secret, even if one is sent', () {
      final e = WebhookEndpoint.fromJson(const {
        'id': 'x',
        'url': 'https://h.example.com/',
        'events': ['lead.created'],
        'format': 'slack',
        'is_active': true,
        'secret_hint': 'whsec_...abcd',
        'secret': 'whsec_full_value',
      });
      expect(e.secretHint, 'whsec_...abcd');
      expect(e.formatLabel, 'Slack message');
      expect(e.toString().contains('whsec_full_value'), isFalse);
    });

    test('the payload carries four keys and nothing else', () {
      final body = webhookPayload((
        url: ' https://h.example.com/ ',
        description: ' Zapier ',
        format: 'json',
        events: const ['lead.created'],
      ));
      expect(body.keys.toSet(), {'url', 'description', 'format', 'events'});
      expect(body['url'], 'https://h.example.com/');
      expect(body['description'], 'Zapier');
    });

    test('event labels read as words', () {
      expect(webhookEventLabel('ticket.comment_added'), 'Comment added');
      expect(webhookEventLabel('deal.won'), 'Won');
    });

    test('a pending delivery cannot be redelivered', () {
      expect(deliveries.deliveries[1].canRedeliver, isFalse);
      expect(deliveries.deliveries[0].canRedeliver, isTrue);
      expect(deliveries.deliveries[0].statusLabel, 'Failed');
    });

    test('limit gate', () {
      expect(state.atLimit, isFalse);
      expect(const WebhooksState(endpoints: [live], limit: 1).atLimit, isTrue);
    });
  });
}

class _FakeWebhooks extends WebhooksNotifier {
  _FakeWebhooks(this._state);

  final WebhooksState _state;

  @override
  Future<WebhooksState> build() async => _state;
}
