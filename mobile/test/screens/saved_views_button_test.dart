import 'dart:convert';

import 'package:bottle_crm/core/theme/theme.dart';
import 'package:bottle_crm/data/models/auth_response.dart';
import 'package:bottle_crm/data/models/models.dart';
import 'package:bottle_crm/providers/accounts_provider.dart';
import 'package:bottle_crm/providers/auth_provider.dart';
import 'package:bottle_crm/providers/contacts_provider.dart';
import 'package:bottle_crm/providers/deal_pipelines_provider.dart';
import 'package:bottle_crm/providers/deals_provider.dart';
import 'package:bottle_crm/providers/invoices_provider.dart';
import 'package:bottle_crm/providers/leads_provider.dart';
import 'package:bottle_crm/providers/lookup_provider.dart';
import 'package:bottle_crm/providers/saved_views_provider.dart';
import 'package:bottle_crm/providers/tickets_provider.dart';
import 'package:bottle_crm/screens/accounts/accounts_list_screen.dart';
import 'package:bottle_crm/screens/contacts/contacts_list_screen.dart';
import 'package:bottle_crm/screens/deals/deals_list_screen.dart';
import 'package:bottle_crm/screens/invoices/invoices_list_screen.dart';
import 'package:bottle_crm/screens/leads/leads_list_screen.dart';
import 'package:bottle_crm/screens/tickets/tickets_list_screen.dart';
import 'package:bottle_crm/services/api_service.dart';
import 'package:bottle_crm/widgets/common/saved_views_button.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;

/// Saved views (G29) on the six record lists, rendered at a real phone.
///
/// The button has to sit on each app bar at 390px, with large text too,
/// without squeezing the title out, and be thumb-sized. The sheet has to fit
/// the same phone, and a view has to land on the screen's own filters.
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late _QueueClient client;

  setUp(() {
    client = _QueueClient();
    ApiService().setClientForTesting(client);
  });

  void usePhone(WidgetTester tester, double textScale) {
    tester.view.devicePixelRatio = 3.0;
    tester.view.physicalSize = const Size(390 * 3, 844 * 3);
    tester.platformDispatcher.textScaleFactorTestValue = textScale;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    addTearDown(tester.platformDispatcher.clearTextScaleFactorTestValue);
  }

  Widget app(Widget screen) => ProviderScope(
    overrides: [
      authProvider.overrideWith(_FakeAuth.new),
      leadsProvider.overrideWith(_FakeLeads.new),
      contactsProvider.overrideWith(_FakeContacts.new),
      accountsProvider.overrideWith(_FakeAccounts.new),
      dealsProvider.overrideWith(_FakeDeals.new),
      dealPipelinesProvider.overrideWith(_FakePipelines.new),
      ticketsProvider.overrideWith(_FakeTickets.new),
      invoicesProvider.overrideWith(_FakeInvoices.new),
      usersProvider.overrideWithValue(const []),
      tagsProvider.overrideWithValue(const []),
      accountOptionsProvider.overrideWithValue(const []),
    ],
    child: MaterialApp(theme: AppTheme.light, home: screen),
  );

  final screens = <String, (String, Widget)>{
    'leads': ('Leads', const LeadsListScreen()),
    'contacts': ('Contacts', const ContactsListScreen()),
    'accounts': ('Accounts', const AccountsListScreen()),
    'deals': ('Deals', const DealsListScreen()),
    'tickets': ('Tickets', const TicketsListScreen()),
    'invoices': ('Invoices', const InvoicesListScreen()),
  };

  for (final textScale in [1.0, 1.3]) {
    for (final entry in screens.entries) {
      final (title, screen) = entry.value;
      testWidgets('${entry.key}: saved views is on the app bar at 390px, '
          'text x$textScale', (tester) async {
        usePhone(tester, textScale);
        await tester.pumpWidget(app(screen));
        await tester.pumpAndSettle();

        expect(tester.takeException(), isNull);
        final box = tester.getRect(find.byType(SavedViewsButton));
        expect(box.width, greaterThanOrEqualTo(44));
        expect(box.height, greaterThanOrEqualTo(44));
        expect(box.right, lessThanOrEqualTo(390));
        // The title keeps room for its own word beside the icons.
        final titleBox = tester.getRect(find.text(title).first);
        expect(titleBox.width, greaterThan(40));
        expect(titleBox.right, lessThanOrEqualTo(box.left + 1));
      });
    }
  }

  group('the sheet', () {
    const view = {
      'id': '99999999-8888-7777-6666-555555555555',
      'module': 'leads',
      'name': 'Hot leads nobody has called back since the trade show',
      'filters': {
        'search': ['ada'],
        'rating': ['HOT'],
        'city': ['Austin'],
      },
    };

    Future<List<Map<String, List<String>>>> openSheet(
      WidgetTester tester, {
      double textScale = 1.0,
      Map<String, Object?> current = const {},
    }) async {
      usePhone(tester, textScale);
      final applied = <Map<String, List<String>>>[];
      await tester.pumpWidget(
        ProviderScope(
          child: MaterialApp(
            theme: AppTheme.light,
            home: Scaffold(
              appBar: AppBar(
                actions: [
                  SavedViewsButton(
                    list: SavedViewList.leads,
                    keys: const {'search', 'rating'},
                    currentQuery: () async => current,
                    onApply: applied.add,
                  ),
                ],
              ),
            ),
          ),
        ),
      );
      await tester.tap(find.byTooltip('Saved views'));
      await tester.pumpAndSettle();
      return applied;
    }

    for (final textScale in [1.0, 1.3]) {
      testWidgets('lists views and fits a phone at text x$textScale', (
        tester,
      ) async {
        client.replies.add((200, _list([view])));
        await openSheet(tester, textScale: textScale);

        expect(tester.takeException(), isNull);
        expect(find.text(view['name'] as String), findsOneWidget);
        expect(
          find.text('1 filter value this screen cannot show'),
          findsOneWidget,
        );
        final save = tester.getRect(
          find.ancestor(
            of: find.text('Save current filters'),
            matching: find.byWidgetPredicate((w) => w is FilledButton),
          ),
        );
        expect(save.height, greaterThanOrEqualTo(44));
        expect(save.right, lessThanOrEqualTo(390));
        final more = tester.getRect(find.byTooltip('More for ${view['name']}'));
        expect(more.height, greaterThanOrEqualTo(44));
      });
    }

    testWidgets('tapping a view applies it and says what it left out', (
      tester,
    ) async {
      client.replies.add((200, _list([view])));
      final applied = await openSheet(tester);

      await tester.tap(find.text(view['name'] as String));
      await tester.pumpAndSettle();

      expect(applied.single['search'], ['ada']);
      expect(applied.single['city'], ['Austin']);
      expect(find.text('Saved views'), findsNothing);
      expect(
        find.text(
          'Showing ${view['name']}. 1 filter value in it cannot be shown here.',
        ),
        findsOneWidget,
      );
    });

    testWidgets('saving sends only the keys this screen has', (tester) async {
      client.replies
        ..add((200, _list([])))
        ..add((201, jsonEncode(view)))
        ..add((200, _list([view])));
      await openSheet(
        tester,
        current: {
          'search': 'ada',
          'rating': 'HOT',
          'sort': 'due_date',
          'limit': '20',
        },
      );
      expect(
        find.text('No saved views yet. Filter the list, then save it here.'),
        findsOneWidget,
      );

      await tester.tap(find.text('Save current filters'));
      await tester.pumpAndSettle();
      await tester.enterText(find.byType(TextField), 'Hot');
      await tester.tap(find.text('Save'));
      await tester.pumpAndSettle();

      expect(client.sent[1].method, 'POST');
      expect(jsonDecode(client.bodies[1]), {
        'module': 'leads',
        'name': 'Hot',
        'filters': {
          'search': ['ada'],
          'rating': ['HOT'],
        },
      });
      expect(find.text(view['name'] as String), findsOneWidget);
    });

    testWidgets("a refusal shows the API's sentence in the sheet", (
      tester,
    ) async {
      client.replies
        ..add((200, _list([view])))
        ..add((
          400,
          '{"name": ["You already have a view with this name for this list."]}',
        ));
      await openSheet(tester);

      await tester.tap(find.text('Save current filters'));
      await tester.pumpAndSettle();
      await tester.enterText(find.byType(TextField), 'dup');
      await tester.tap(find.text('Save'));
      await tester.pumpAndSettle();

      expect(
        find.text('You already have a view with this name for this list.'),
        findsOneWidget,
      );
    });

    testWidgets('rename and delete go through the row menu', (tester) async {
      client.replies
        ..add((200, _list([view])))
        ..add((200, jsonEncode({...view, 'name': 'Warm'})))
        ..add((
          200,
          _list([
            {...view, 'name': 'Warm'},
          ]),
        ))
        ..add((204, ''))
        ..add((200, _list([])));
      await openSheet(tester);

      await tester.tap(find.byTooltip('More for ${view['name']}'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Rename'));
      await tester.pumpAndSettle();
      await tester.enterText(find.byType(TextField), 'Warm');
      await tester.tap(find.text('Save'));
      await tester.pumpAndSettle();
      expect(client.sent[1].method, 'PATCH');
      expect(find.text('Warm'), findsOneWidget);

      await tester.tap(find.byTooltip('More for Warm'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Delete'));
      await tester.pumpAndSettle();
      // Nothing is sent until the confirm.
      expect(client.sent, hasLength(3));
      await tester.tap(find.widgetWithText(TextButton, 'Delete'));
      await tester.pumpAndSettle();
      expect(client.sent[3].method, 'DELETE');
      expect(find.text('Warm'), findsNothing);
    });

    testWidgets('a full list says so instead of offering Save', (tester) async {
      client.replies.add((200, _list([view], limit: 1)));
      await openSheet(tester);

      expect(find.text('Save current filters'), findsNothing);
      expect(
        find.text(
          'This list keeps at most 1 saved views. Delete one to save another.',
        ),
        findsOneWidget,
      );
    });
  });

  testWidgets('leads: a view lands on the search box and the chips', (
    tester,
  ) async {
    usePhone(tester, 1.0);
    client.replies.add((
      200,
      _list([
        {
          'id': '99999999-8888-7777-6666-555555555555',
          'module': 'leads',
          'name': 'Hot',
          'filters': {
            'search': ['ada'],
            'rating': ['HOT'],
            'status': ['assigned', 'in process'],
          },
        },
      ]),
    ));
    await tester.pumpWidget(app(const LeadsListScreen()));
    await tester.pumpAndSettle();

    await tester.tap(find.byTooltip('Saved views'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Hot'));
    await tester.pumpAndSettle();

    final applied = _FakeLeads.last!;
    expect(applied.search, 'ada');
    expect(applied.rating, LeadRating.hot);
    expect(applied.statuses, {LeadStatus.assigned, LeadStatus.inProcess});
    expect(
      tester.widget<TextField>(find.byType(TextField).first).controller!.text,
      'ada',
    );
    expect(find.text('2 statuses'), findsOneWidget);
  });
}

String _list(List<Map<String, Object>> views, {int limit = 25}) =>
    jsonEncode({'saved_views': views, 'limit': limit});

class _QueueClient extends http.BaseClient {
  final List<(int, String)> replies = [];
  final List<http.BaseRequest> sent = [];
  final List<String> bodies = [];

  /// Only saved-views calls are queued and kept; anything else the screens
  /// ask for on the way (the profile, lookups) gets an empty answer.
  @override
  Future<http.StreamedResponse> send(http.BaseRequest request) async {
    final ours = request.url.path.contains('/saved-views/');
    final bytes = await request.finalize().toBytes();
    if (ours) {
      sent.add(request);
      bodies.add(utf8.decode(bytes));
    }
    final (status, body) = !ours
        ? (200, '{}')
        : replies.isEmpty
        ? (200, _list([]))
        : replies.removeAt(0);
    return http.StreamedResponse(
      Stream.value(utf8.encode(body)),
      status,
      request: request,
    );
  }
}

class _FakeAuth extends AuthNotifier {
  @override
  AuthState build() {
    const org = Organization(id: 'org-1', name: 'Org', currencySymbol: r'$');
    return AuthState(
      user: const AuthUser(id: 'u1', email: 'me@example.com'),
      organizations: [org],
      selectedOrganization: org,
      isAuthenticated: true,
    );
  }
}

class _FakeLeads extends LeadsNotifier {
  static LeadFilters? last;

  @override
  Future<LeadsListData> build() async => LeadsListData(hasMore: false);

  @override
  Future<void> setFilters(LeadFilters filters) async => last = filters;
}

class _FakeContacts extends ContactsNotifier {
  @override
  Future<ContactsListData> build() async => const ContactsListData();
}

class _FakeAccounts extends AccountsNotifier {
  @override
  Future<AccountsListData> build() async => const AccountsListData();
}

class _FakeDeals extends DealsNotifier {
  @override
  Future<DealsListData> build() async => DealsListData();

  @override
  Future<void> refresh({String? search, String? stage}) async {}
}

class _FakePipelines extends DealPipelinesNotifier {
  @override
  Future<List<DealPipeline>> build() async => [
    DealPipeline.fromJson({
      'id': 'p1',
      'name': 'Sales',
      'is_default': true,
      'stages': [
        {'id': 's1', 'code': 'PROSPECTING', 'label': 'Prospecting'},
      ],
    }),
  ];
}

class _FakeTickets extends TicketsNotifier {
  @override
  Future<TicketsListData> build() async =>
      TicketsListData(tickets: const [], totalCount: 0, hasMore: false);

  @override
  Future<void> refresh({
    TicketListFilters filters = const TicketListFilters(),
  }) async {}
}

class _FakeInvoices extends InvoicesNotifier {
  @override
  Future<InvoicesListData> build() async => const InvoicesListData();
}
