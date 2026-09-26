import 'package:bottle_crm/data/models/models.dart';
import 'package:bottle_crm/providers/deal_pipelines_provider.dart';
import 'package:bottle_crm/providers/deals_provider.dart';
import 'package:bottle_crm/providers/invoice_extras_provider.dart';
import 'package:bottle_crm/providers/invoices_provider.dart';
import 'package:bottle_crm/providers/lookup_provider.dart';
import 'package:bottle_crm/routes/app_router.dart';
import 'package:bottle_crm/screens/deals/deal_detail_screen.dart';
import 'package:bottle_crm/screens/invoices/new_estimate_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

/// Raising an estimate, blank or from a deal, and an invoice from a won deal.
///
/// The server decides who may bill which account and cite which deal, and it
/// refuses the rest with the same message whether the record is hidden or
/// missing. These tests pin the client half: the form starts from what the
/// deal carries, never submits an id its pickers do not offer, and the deal
/// screen offers "Create invoice" only where the server can say yes.
Deal _deal({String stageKind = 'open', List<String> contacts = const []}) =>
    Deal.fromJson({
      'id': 'd1',
      'name': 'Renewal',
      'stage': stageKind == 'won' ? 'SIGNED' : 'TALKING',
      'stage_label': stageKind == 'won' ? 'Signed' : 'Talking',
      'stage_kind': stageKind,
      'amount': '300',
      'currency': 'EUR',
      'account': {'id': 'acc-1', 'name': 'Northwind'},
      'contacts': [
        for (final id in contacts)
          {'id': id, 'first_name': id, 'last_name': 'Person'},
      ],
      'line_items': [
        {'id': 'li1', 'name': 'Seats', 'quantity': 3, 'unit_price': '100.00'},
      ],
      'created_at': '2026-09-01T00:00:00Z',
    });

/// Decimals as the API sends them (strings), with a line discount on each.
Deal _discountedDeal() => Deal.fromJson({
  'id': 'd1',
  'name': 'Renewal',
  'stage': 'TALKING',
  'stage_kind': 'open',
  'currency': 'EUR',
  'account': {'id': 'acc-1', 'name': 'Northwind'},
  'contacts': [
    {'id': 'c1', 'first_name': 'c1', 'last_name': 'Person'},
  ],
  'line_items': [
    {
      'id': 'li1',
      'name': 'Seats',
      'quantity': '2.00',
      'unit_price': '100.00',
      'discount_type': 'PERCENTAGE',
      'discount_value': '10.00',
    },
    {
      'id': 'li2',
      'name': 'Setup',
      'quantity': '1.00',
      'unit_price': '50.00',
      'discount_type': 'FIXED',
      'discount_value': '5.00',
    },
  ],
  'created_at': '2026-09-01T00:00:00Z',
});

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  void usePhone(WidgetTester tester, double textScale) {
    tester.view.devicePixelRatio = 3.0;
    tester.view.physicalSize = const Size(390 * 3, 844 * 3);
    tester.platformDispatcher.textScaleFactorTestValue = textScale;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    addTearDown(tester.platformDispatcher.clearTextScaleFactorTestValue);
  }

  group('the estimate form', () {
    late _FakeEstimates estimates;

    setUp(() => estimates = _FakeEstimates());

    Future<void> pumpForm(
      WidgetTester tester, {
      Deal? fromDeal,
      double textScale = 1.0,
    }) async {
      usePhone(tester, textScale);
      final router = GoRouter(
        initialLocation: '/start',
        routes: [
          GoRoute(path: '/start', builder: (_, _) => const Text('started')),
          GoRoute(
            path: '/form',
            builder: (_, _) => NewEstimateScreen(fromDeal: fromDeal),
          ),
        ],
      );
      await tester.pumpWidget(
        ProviderScope(
          overrides: [
            accountsLookupProvider.overrideWith(_FakeAccounts.new),
            contactsLookupProvider.overrideWith(_FakeContacts.new),
            opportunityEntityOptionsProvider.overrideWithValue(
              const AsyncValue.data([EntityLookup(id: 'd1', label: 'Renewal')]),
            ),
            productsProvider.overrideWith(_FakeProducts.new),
            estimatesProvider.overrideWith(() => estimates),
          ],
          child: MaterialApp.router(routerConfig: router),
        ),
      );
      await tester.pumpAndSettle();
      router.push('/form');
      await tester.pumpAndSettle();
    }

    FilledButton submit(WidgetTester tester) => tester.widget<FilledButton>(
      find.widgetWithText(FilledButton, 'Create draft'),
    );

    for (final scale in [1.0, 1.3]) {
      testWidgets('lays out at 390px, text x$scale', (tester) async {
        await pumpForm(
          tester,
          fromDeal: _deal(contacts: ['c1']),
          textScale: scale,
        );
        expect(tester.takeException(), isNull);
      });
    }

    testWidgets('a blank form will not submit', (tester) async {
      await pumpForm(tester);

      expect(submit(tester).onPressed, isNull);
      expect(find.text('Pick an account first'), findsOneWidget);
    });

    testWidgets('from a deal it starts with the deal and sends it all', (
      tester,
    ) async {
      // `hidden` is on the deal but not offered by the contact picker, so the
      // first contact the caller can open is used instead.
      await pumpForm(tester, fromDeal: _deal(contacts: ['hidden', 'c1']));

      expect(find.text('Northwind'), findsOneWidget);
      expect(find.text('c1 Person'), findsOneWidget);
      expect(find.text('Renewal'), findsOneWidget);
      expect(find.text('Estimate for Renewal'), findsOneWidget);
      expect(find.text('Seats'), findsOneWidget);

      await tester.tap(find.widgetWithText(FilledButton, 'Create draft'));
      await tester.pumpAndSettle();

      final sent = estimates.created.single;
      expect(sent['account_id'], 'acc-1');
      expect(sent['contact_id'], 'c1');
      expect(sent['opportunity_id'], 'd1');
      expect(sent['currency'], 'EUR');
      expect(sent['title'], 'Estimate for Renewal');
      expect(sent['expiry_date'], isNotNull);
      expect(sent['line_items'], [
        {
          'name': 'Seats',
          'description': '',
          'quantity': '3.0',
          'unit_price': '100.00',
          'order': 0,
        },
      ]);
      // Server-derived fields are never sent.
      expect(sent.keys, isNot(contains('status')));
      expect(find.text('started'), findsOneWidget);
    });

    testWidgets(
      'from a deal each line keeps its decimal quantity and discount',
      (tester) async {
        // What the API actually sends: decimals as strings. "2.00" used to parse
        // as a quantity of 1, and the discount was dropped, so the estimate
        // started at the list price of one unit.
        await pumpForm(tester, fromDeal: _discountedDeal());

        // 2 x 100 less 10% = 180, 1 x 50 less 5 = 45.
        expect(find.text('2 x €100.00, less 10%'), findsOneWidget);
        expect(find.text('€180.00'), findsOneWidget);
        expect(find.text('€45.00'), findsOneWidget);
        expect(find.text('€225.00'), findsOneWidget);

        await tester.tap(find.widgetWithText(FilledButton, 'Create draft'));
        await tester.pumpAndSettle();

        final lines = estimates.created.single['line_items'] as List;
        expect(lines.first, containsPair('quantity', '2.0'));
        expect(lines.first, containsPair('discount_type', 'PERCENTAGE'));
        expect(lines.first, containsPair('discount_value', '10.00'));
        expect(lines.last, containsPair('discount_type', 'FIXED'));
        expect(lines.last, containsPair('discount_value', '5.00'));
      },
    );

    testWidgets('discounted deal lines lay out at 390px, text x1.3', (
      tester,
    ) async {
      await pumpForm(tester, fromDeal: _discountedDeal(), textScale: 1.3);
      expect(tester.takeException(), isNull);
    });

    testWidgets('a deal with only hidden contacts leaves the contact unset', (
      tester,
    ) async {
      await pumpForm(tester, fromDeal: _deal(contacts: ['hidden']));

      expect(submit(tester).onPressed, isNull);
    });

    testWidgets('the server refusal is shown and nothing navigates', (
      tester,
    ) async {
      estimates.refusal = 'Account not found, or you do not have access to it.';
      await pumpForm(tester, fromDeal: _deal(contacts: ['c1']));

      await tester.tap(find.widgetWithText(FilledButton, 'Create draft'));
      await tester.pumpAndSettle();

      expect(
        find.text('Account not found, or you do not have access to it.'),
        findsOneWidget,
      );
      expect(find.text('started'), findsNothing);
    });
  });

  group('the deal screen', () {
    late _FakeInvoices invoices;

    setUp(() => invoices = _FakeInvoices());

    Future<void> pumpDeal(
      WidgetTester tester,
      Deal deal, {
      double textScale = 1.0,
    }) async {
      usePhone(tester, textScale);
      final router = GoRouter(
        initialLocation: '/deals/d1',
        routes: [
          GoRoute(
            path: '/deals/:id',
            builder: (_, s) =>
                DealDetailScreen(dealId: s.pathParameters['id']!),
          ),
          GoRoute(
            path: '${AppRoutes.invoices}/:id',
            builder: (_, s) => Text('invoice ${s.pathParameters['id']}'),
          ),
          GoRoute(
            path: AppRoutes.estimateNew,
            builder: (_, s) => Text('estimate from ${(s.extra as Deal?)?.id}'),
          ),
        ],
      );
      await tester.pumpWidget(
        ProviderScope(
          overrides: [
            dealsProvider.overrideWith(() => _FakeDeals(deal)),
            dealPipelinesProvider.overrideWith(_FakePipelines.new),
            invoicesProvider.overrideWith(() => invoices),
          ],
          child: MaterialApp.router(routerConfig: router),
        ),
      );
      await tester.pumpAndSettle();
    }

    for (final scale in [1.0, 1.3]) {
      testWidgets('a won deal fits its invoice action at 390px, x$scale', (
        tester,
      ) async {
        await pumpDeal(tester, _deal(stageKind: 'won'), textScale: scale);
        expect(find.text('Create invoice'), findsOneWidget);
        expect(tester.takeException(), isNull);
      });
    }

    testWidgets('an open deal offers no invoice', (tester) async {
      await pumpDeal(tester, _deal());

      expect(find.text('Create invoice'), findsNothing);
    });

    testWidgets('creating the invoice opens it', (tester) async {
      await pumpDeal(tester, _deal(stageKind: 'won'));

      await tester.tap(find.text('Create invoice'));
      await tester.pumpAndSettle();

      expect(invoices.fromDeals, ['d1']);
      expect(find.text('invoice inv-1'), findsOneWidget);
    });

    testWidgets('a refusal is shown as the server wrote it', (tester) async {
      invoices.refusal = 'Opportunity has no products/line items to invoice';
      await pumpDeal(tester, _deal(stageKind: 'won'));

      await tester.tap(find.text('Create invoice'));
      await tester.pump();

      expect(
        find.text('Opportunity has no products/line items to invoice'),
        findsOneWidget,
      );
    });

    testWidgets('any deal can start an estimate from the menu', (tester) async {
      await pumpDeal(tester, _deal());

      await tester.tap(find.byIcon(LucideIcons.moreVertical));
      await tester.pumpAndSettle();
      expect(find.text('Create invoice'), findsNothing);
      await tester.tap(find.text('Create estimate'));
      await tester.pumpAndSettle();

      expect(find.text('estimate from d1'), findsOneWidget);
    });
  });
}

class _FakeAccounts extends AccountsLookupNotifier {
  @override
  Future<List<AccountLookup>> build() async => const [
    AccountLookup(id: 'acc-1', name: 'Northwind'),
  ];
}

class _FakeContacts extends ContactsLookupNotifier {
  @override
  Future<List<ContactLookup>> build() async => const [
    ContactLookup(id: 'c1', firstName: 'c1', lastName: 'Person'),
    ContactLookup(
      id: 'c2',
      firstName: 'Bound',
      lastName: 'Elsewhere',
      accountId: 'acc-2',
    ),
  ];
}

class _FakeProducts extends ProductsNotifier {
  @override
  Future<List<Product>> build() async => const [];
}

class _FakeEstimates extends EstimatesNotifier {
  final List<Map<String, dynamic>> created = [];
  String? refusal;

  @override
  Future<List<Estimate>> build() async => const [];

  @override
  Future<String?> create(Map<String, dynamic> payload) async {
    if (refusal != null) return refusal;
    created.add(payload);
    return null;
  }
}

class _FakeInvoices extends InvoicesNotifier {
  final List<String> fromDeals = [];
  String? refusal;

  @override
  Future<InvoicesListData> build() async => const InvoicesListData();

  @override
  Future<({String? invoiceId, String? error})> createFromDeal(
    String dealId,
  ) async {
    if (refusal != null) return (invoiceId: null, error: refusal);
    fromDeals.add(dealId);
    return (invoiceId: 'inv-1', error: null);
  }
}

class _FakeDeals extends DealsNotifier {
  _FakeDeals(this.deal);

  final Deal deal;

  @override
  Future<DealsListData> build() async => const DealsListData();

  @override
  Future<DealDetail?> getDealDetail(String id) async => DealDetail(deal: deal);
}

class _FakePipelines extends DealPipelinesNotifier {
  @override
  Future<List<DealPipeline>> build() async => const [];
}
