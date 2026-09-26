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
import 'package:bottle_crm/providers/tickets_provider.dart';
import 'package:bottle_crm/screens/accounts/accounts_list_screen.dart';
import 'package:bottle_crm/screens/contacts/contacts_list_screen.dart';
import 'package:bottle_crm/screens/deals/deals_list_screen.dart';
import 'package:bottle_crm/screens/invoices/invoices_list_screen.dart';
import 'package:bottle_crm/screens/leads/leads_list_screen.dart';
import 'package:bottle_crm/screens/tickets/tickets_list_screen.dart';
import 'package:bottle_crm/services/api_service.dart';
import 'package:bottle_crm/widgets/common/export_csv_button.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;

/// "Export CSV" on the six record lists, rendered at a real phone.
///
/// The action has to be reachable at 390px, with large text too, beside the
/// actions each app bar already carries, and a thumb has to be able to hit
/// it. A tap has to send the screen's own filters.
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

  final screens = <String, Widget>{
    'leads': const LeadsListScreen(),
    'contacts': const ContactsListScreen(),
    'accounts': const AccountsListScreen(),
    'deals': const DealsListScreen(),
    'tickets': const TicketsListScreen(),
    'invoices': const InvoicesListScreen(),
  };

  for (final textScale in [1.0, 1.3]) {
    for (final entry in screens.entries) {
      testWidgets('${entry.key}: export is on the app bar at 390px, '
          'text x$textScale', (tester) async {
        usePhone(tester, textScale);
        await tester.pumpWidget(app(entry.value));
        await tester.pumpAndSettle();

        expect(tester.takeException(), isNull);
        expect(find.byTooltip('Export CSV'), findsOneWidget);
        // The whole button, which includes the padding Material adds around a
        // 40px icon to make a 48px target, not just the tooltip's box.
        final box = tester.getRect(find.byType(ExportCsvButton));
        expect(box.width, greaterThanOrEqualTo(44));
        expect(box.height, greaterThanOrEqualTo(44));
        expect(box.right, lessThanOrEqualTo(390));
      });
    }
  }

  group('a tap sends the screen query and reports the outcome', () {
    late _RecordingClient client;

    setUp(() {
      client = _RecordingClient();
      ApiService().setClientForTesting(client);
    });

    testWidgets('deals ask for the pipeline the list shows', (tester) async {
      usePhone(tester, 1.0);
      await tester.pumpWidget(app(const DealsListScreen()));
      await tester.pumpAndSettle();

      await tester.tap(find.byTooltip('Export CSV'));
      await tester.pumpAndSettle();

      final url = client.sent!.url;
      expect(url.path, endsWith('/opportunities/export/'));
      expect(url.queryParameters['pipeline'], 'p1');
      // The fake answers 400, so the failure is what gets said.
      expect(find.text('That filter is not valid.'), findsOneWidget);
    });

    testWidgets('accounts ask for the half the list shows', (tester) async {
      usePhone(tester, 1.0);
      await tester.pumpWidget(app(const AccountsListScreen()));
      await tester.pumpAndSettle();

      await tester.tap(find.byTooltip('Export CSV'));
      await tester.pumpAndSettle();

      expect(client.sent!.url.path, endsWith('/accounts/export/'));
      expect(client.sent!.url.queryParameters['is_active'], 'true');
    });
  });
}

class _RecordingClient extends http.BaseClient {
  http.BaseRequest? sent;

  @override
  Future<http.StreamedResponse> send(http.BaseRequest request) async {
    sent = request;
    return http.StreamedResponse(
      Stream.value(utf8.encode('{"detail": "That filter is not valid."}')),
      400,
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
  @override
  Future<LeadsListData> build() async => LeadsListData(hasMore: false);
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
