import 'dart:convert';

import 'package:bottle_crm/data/models/account.dart';
import 'package:bottle_crm/data/models/contact.dart';
import 'package:bottle_crm/data/models/models.dart';
import 'package:bottle_crm/providers/accounts_provider.dart';
import 'package:bottle_crm/providers/auth_provider.dart';
import 'package:bottle_crm/providers/contacts_provider.dart';
import 'package:bottle_crm/providers/deal_pipelines_provider.dart';
import 'package:bottle_crm/providers/deals_provider.dart';
import 'package:bottle_crm/providers/duplicates_provider.dart';
import 'package:bottle_crm/providers/leads_provider.dart';
import 'package:bottle_crm/screens/accounts/account_detail_screen.dart';
import 'package:bottle_crm/screens/contacts/contact_detail_screen.dart';
import 'package:bottle_crm/screens/deals/deal_detail_screen.dart';
import 'package:bottle_crm/screens/leads/lead_detail_screen.dart';
import 'package:bottle_crm/services/api_service.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';
import 'package:http/http.dart' as http;

/// Delete on the lead, contact, account and deal screens follows the server's
/// `can_delete`, and nothing else.
///
/// The screens used to work the rule out themselves (admin flag, or the
/// creator's email matching the signed-in user's). Here the admin flag is set
/// against the server's answer on purpose, so a screen that still read it
/// would fail: an admin told "no" sees no Delete, a member told "yes" does.
class _NoDuplicates extends DuplicatesApi {
  @override
  Future<RecordDuplicates> forRecord(DuplicateModule module, String id) async =>
      const RecordDuplicates();
}

class _Contacts extends ContactsNotifier {
  _Contacts(this.canDelete);
  final bool canDelete;
  @override
  Future<ContactsListData> build() async => const ContactsListData();
  @override
  Future<Contact?> getContact(String id, {void Function()? onNotFound}) async =>
      Contact.fromJson({
        'id': id,
        'first_name': 'Asha',
        'last_name': 'Raman-Venkataraghavan',
        'created_by': {'email': 'me@example.com'},
      }, canDelete: canDelete);
}

class _Accounts extends AccountsNotifier {
  _Accounts(this.canDelete);
  final bool canDelete;
  @override
  Future<AccountsListData> build() async => const AccountsListData();
  @override
  Future<Account?> getAccount(String id, {void Function()? onNotFound}) async =>
      Account.fromJson({
        'id': id,
        'name': 'Northwind Traders and Distribution',
        'created_by': {'email': 'me@example.com'},
      }, canDelete: canDelete);
}

class _Leads extends LeadsNotifier {
  _Leads(this.canDelete);
  final bool canDelete;
  @override
  Future<LeadsListData> build() async => const LeadsListData();
  @override
  Future<LeadDetail?> getLeadDetail(
    String id, {
    void Function()? onNotFound,
  }) async => LeadDetail(
    lead: Lead.fromJson({
      'id': id,
      'first_name': 'Asha',
      'last_name': 'Raman',
      'status': 'assigned',
      'created_by': {'email': 'me@example.com'},
    }),
    canDelete: canDelete,
  );
}

class _Deals extends DealsNotifier {
  _Deals(this.canDelete);
  final bool canDelete;
  @override
  Future<DealsListData> build() async => const DealsListData();
  @override
  Future<DealDetail?> getDealDetail(
    String id, {
    void Function()? onNotFound,
  }) async => DealDetail(
    deal: Deal.fromJson({
      'id': id,
      'name': 'Renewal for Northwind Traders and Distribution',
      'stage': 'TALKING',
      'stage_label': 'Talking',
      'stage_kind': 'open',
      'amount': '300',
      'currency': 'EUR',
      'created_by': {'email': 'me@example.com'},
      'created_at': '2026-09-01T00:00:00Z',
    }),
    canDelete: canDelete,
  );
}

class _Pipelines extends DealPipelinesNotifier {
  @override
  Future<List<DealPipeline>> build() async => const [];
}

class _FakeClient extends http.BaseClient {
  String body = '{}';
  @override
  Future<http.StreamedResponse> send(http.BaseRequest request) async =>
      http.StreamedResponse(
        Stream.value(utf8.encode(body)),
        200,
        request: request,
      );
}

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

  Future<void> pump(
    WidgetTester tester,
    Widget screen, {
    required bool canDelete,
    required double textScale,
  }) async {
    usePhone(tester, textScale);
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          contactsProvider.overrideWith(() => _Contacts(canDelete)),
          accountsProvider.overrideWith(() => _Accounts(canDelete)),
          leadsProvider.overrideWith(() => _Leads(canDelete)),
          dealsProvider.overrideWith(() => _Deals(canDelete)),
          dealPipelinesProvider.overrideWith(_Pipelines.new),
          duplicatesApiProvider.overrideWithValue(_NoDuplicates()),
          // Deliberately the opposite of what the server said.
          isOrgAdminProvider.overrideWithValue(!canDelete),
          currentUserProvider.overrideWithValue(null),
        ],
        child: MaterialApp(home: screen),
      ),
    );
    await tester.pumpAndSettle();
  }

  for (final scale in [1.0, 1.3]) {
    for (final canDelete in [true, false]) {
      final offered = canDelete ? findsOneWidget : findsNothing;

      testWidgets('contact: can_delete=$canDelete, text scale $scale', (
        tester,
      ) async {
        await pump(
          tester,
          const ContactDetailScreen(contactId: 'c1'),
          canDelete: canDelete,
          textScale: scale,
        );
        expect(tester.takeException(), isNull);
        expect(find.byTooltip('Delete contact'), offered);
      });

      testWidgets('account: can_delete=$canDelete, text scale $scale', (
        tester,
      ) async {
        await pump(
          tester,
          const AccountDetailScreen(accountId: 'a1'),
          canDelete: canDelete,
          textScale: scale,
        );
        expect(tester.takeException(), isNull);
        expect(find.byTooltip('Delete account'), offered);
      });

      testWidgets('lead: can_delete=$canDelete, text scale $scale', (
        tester,
      ) async {
        await pump(
          tester,
          const LeadDetailScreen(leadId: 'l1'),
          canDelete: canDelete,
          textScale: scale,
        );
        expect(tester.takeException(), isNull);
        await tester.tap(find.byIcon(LucideIcons.moreVertical));
        await tester.pumpAndSettle();
        expect(find.text('Delete Lead'), offered);
      });

      testWidgets('deal: can_delete=$canDelete, text scale $scale', (
        tester,
      ) async {
        await pump(
          tester,
          const DealDetailScreen(dealId: 'd1'),
          canDelete: canDelete,
          textScale: scale,
        );
        expect(tester.takeException(), isNull);
        await tester.tap(find.byIcon(LucideIcons.moreVertical).first);
        await tester.pumpAndSettle();
        expect(find.text('Delete Deal'), offered);
      });
    }
  }

  group('the providers read can_delete, and default to false', () {
    late _FakeClient client;
    late ProviderContainer container;

    setUp(() {
      client = _FakeClient();
      ApiService().setClientForTesting(client);
      container = ProviderContainer();
    });
    tearDown(() => container.dispose());

    for (final (sent, expected) in [
      (', "can_delete": true', true),
      (', "can_delete": false', false),
      ('', false),
    ]) {
      test('contact, account, lead and deal with `$sent`', () async {
        client.body =
            '{"contact_obj": {"id": "c1", "first_name": "A", "last_name": "B"}$sent}';
        final contact = await container
            .read(contactsProvider.notifier)
            .getContact('c1');
        expect(contact?.canDelete, expected);

        client.body = '{"account_obj": {"id": "a1", "name": "N"}$sent}';
        final account = await container
            .read(accountsProvider.notifier)
            .getAccount('a1');
        expect(account?.canDelete, expected);

        client.body =
            '{"lead_obj": {"id": "l1", "first_name": "A", "status": "assigned"}$sent}';
        final lead = await container
            .read(leadsProvider.notifier)
            .getLeadDetail('l1');
        expect(lead?.canDelete, expected);

        client.body =
            '{"opportunity_obj": {"id": "d1", "name": "D", "stage": "TALKING"}$sent}';
        final deal = await container
            .read(dealsProvider.notifier)
            .getDealDetail('d1');
        expect(deal?.canDelete, expected);
      });
    }
  });
}
