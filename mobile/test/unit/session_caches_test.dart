import 'dart:convert';
import 'dart:io';

import 'package:bottle_crm/providers/accounts_provider.dart';
import 'package:bottle_crm/providers/analytics_provider.dart';
import 'package:bottle_crm/providers/approvals_provider.dart';
import 'package:bottle_crm/providers/auth_provider.dart';
import 'package:bottle_crm/providers/board_provider.dart';
import 'package:bottle_crm/providers/calendar_feed_provider.dart';
import 'package:bottle_crm/providers/contacts_provider.dart';
import 'package:bottle_crm/providers/dashboard_provider.dart';
import 'package:bottle_crm/providers/deal_pipelines_provider.dart';
import 'package:bottle_crm/providers/deals_provider.dart';
import 'package:bottle_crm/providers/documents_provider.dart';
import 'package:bottle_crm/providers/goals_provider.dart';
import 'package:bottle_crm/providers/help_center_provider.dart';
import 'package:bottle_crm/providers/invoice_extras_provider.dart';
import 'package:bottle_crm/providers/invoices_provider.dart';
import 'package:bottle_crm/providers/lead_board_provider.dart';
import 'package:bottle_crm/providers/lead_pipelines_provider.dart';
import 'package:bottle_crm/providers/leads_provider.dart';
import 'package:bottle_crm/providers/lookup_provider.dart';
import 'package:bottle_crm/providers/notifications_provider.dart';
import 'package:bottle_crm/providers/profile_provider.dart';
import 'package:bottle_crm/providers/settings_provider.dart';
import 'package:bottle_crm/providers/solutions_provider.dart';
import 'package:bottle_crm/providers/support_provider.dart';
import 'package:bottle_crm/providers/tasks_provider.dart';
import 'package:bottle_crm/providers/team_provider.dart';
import 'package:bottle_crm/providers/ticket_board_provider.dart';
import 'package:bottle_crm/providers/tickets_provider.dart';
import 'package:bottle_crm/providers/time_report_provider.dart';
import 'package:bottle_crm/providers/timesheet_provider.dart';
import 'package:bottle_crm/providers/web_forms_provider.dart';
import 'package:bottle_crm/providers/webhooks_provider.dart';
import 'package:bottle_crm/services/api_service.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_riverpod/misc.dart' show ProviderListenable;
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';

/// Signing out, or switching org, has to drop every cache the ended session
/// filled. The kept-alive providers (not autoDispose) are never disposed by
/// Riverpod, so one missing from `AuthNotifier._dropSessionCaches` shows the
/// previous member's or the previous org's rows to whoever comes next. The
/// deal board did exactly that: it was added without joining the list, and
/// after a sign-out the default Deals view kept the old session's cards and
/// their `can_move`.
///
/// Two halves: each provider below really is read again after a sign-out,
/// and the source scan fails when a new kept-alive provider is added under
/// `lib/providers/` without being named here (and so, in practice, without
/// joining the drop).

/// Kept-alive providers holding org- or member-scoped data, keyed by the name
/// the scan finds. A family is subscribed through one member.
final Map<String, ProviderListenable<Object?>> _sessionScoped = {
  'accountsProvider': accountsProvider,
  'analyticsProvider': analyticsProvider,
  'boardProvider': boardProvider,
  'calendarFeedProvider': calendarFeedProvider,
  'contactsProvider': contactsProvider,
  'dashboardProvider': dashboardProvider,
  'dealPipelinesProvider': dealPipelinesProvider,
  'dealsProvider': dealsProvider,
  'dealBoardProvider': dealBoardProvider,
  'documentsProvider': documentsProvider,
  'goalsProvider': goalsProvider,
  'goalHistoryProvider': goalHistoryProvider,
  'goalProvider': goalProvider('g1'),
  'helpCenterSettingsProvider': helpCenterSettingsProvider,
  'estimatesProvider': estimatesProvider,
  'recurringProvider': recurringProvider,
  'productsProvider': productsProvider,
  'invoiceTemplatesProvider': invoiceTemplatesProvider,
  'invoiceReportsProvider': invoiceReportsProvider,
  'invoicesProvider': invoicesProvider,
  'leadBoardProvider': leadBoardProvider,
  'leadPipelinesProvider': leadPipelinesProvider,
  'leadsProvider': leadsProvider,
  'accountsLookupProvider': accountsLookupProvider,
  'contactsLookupProvider': contactsLookupProvider,
  'usersLookupProvider': usersLookupProvider,
  'teamsLookupProvider': teamsLookupProvider,
  'tagsLookupProvider': tagsLookupProvider,
  'leadsLookupProvider': leadsLookupProvider,
  'opportunitiesLookupProvider': opportunitiesLookupProvider,
  'ticketsLookupProvider': ticketsLookupProvider,
  'customFieldDefinitionsProvider': customFieldDefinitionsProvider('Lead'),
  'notificationsProvider': notificationsProvider,
  'profileProvider': profileProvider,
  'customFieldsProvider': customFieldsProvider,
  'macrosProvider': macrosProvider,
  'activeMacrosProvider': activeMacrosProvider,
  'tagSettingsProvider': tagSettingsProvider,
  'routingRulesProvider': routingRulesProvider,
  'escalationProvider': escalationProvider,
  'businessHoursProvider': businessHoursProvider,
  'reopenPolicyProvider': reopenPolicyProvider,
  'accessTokensProvider': accessTokensProvider,
  'myAccessTokensProvider': myAccessTokensProvider,
  'orgSettingsProvider': orgSettingsProvider,
  'mailboxesProvider': mailboxesProvider,
  'approvalRulesProvider': approvalRulesProvider,
  'solutionsProvider': solutionsProvider,
  'supportTicketsProvider': supportTicketsProvider,
  'tasksProvider': tasksProvider,
  'teamProvider': teamProvider,
  'ticketBoardProvider': ticketBoardProvider,
  'ticketsProvider': ticketsProvider,
  'timeReportProvider': timeReportProvider,
  'timesheetProvider': timesheetProvider,
  'webFormsProvider': webFormsProvider,
  'webFormDetailProvider': webFormDetailProvider('f1'),
  'webhooksProvider': webhooksProvider,
  'webhookDeliveriesProvider': webhookDeliveriesProvider((id: 'w1', offset: 0)),
};

/// Session-scoped too, but its build fetches nothing: it is filled on demand,
/// so a refetch cannot be observed and its own test checks it is emptied.
const _filledOnDemand = 'approvalsProvider';

/// Kept-alive providers that hold nothing belonging to a session, each with
/// the reason. Adding a name here is a claim someone should be able to check.
const Map<String, String> _notSessionScoped = {
  'authProvider': 'the session itself; sign-out resets it',
  'documentsArchivedProvider': 'a view toggle, no records',
  'timeReportFiltersProvider': 'a date window and grouping, no records',
  'timesheetRangeProvider': 'which week is on screen, no records',
  'orgPacksProvider': 'the global pack catalogue, served without an org',
  'orgTimezonesProvider': 'the IANA zone list, the same for everyone',
};

class _FakeClient extends http.BaseClient {
  final List<http.BaseRequest> sent = [];

  Iterable<http.BaseRequest> get reads => sent.where((r) => r.method == 'GET');

  @override
  Future<http.StreamedResponse> send(http.BaseRequest request) async {
    sent.add(request);
    return http.StreamedResponse(
      Stream.value(
        utf8.encode(
          '{"approvals": [{"id": "a1", "state": "PENDING"}], "results": []}',
        ),
      ),
      200,
      request: request,
    );
  }
}

Future<void> _settle() async {
  for (var i = 0; i < 30; i++) {
    await Future<void>.delayed(Duration.zero);
  }
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late _FakeClient client;
  late ProviderContainer container;

  setUp(() {
    FlutterSecureStorage.setMockInitialValues({});
    SharedPreferences.setMockInitialValues({});
    client = _FakeClient();
    ApiService().setClientForTesting(client);
    container = ProviderContainer();
  });

  tearDown(() => container.dispose());

  group('sign-out reads every session cache again', () {
    for (final entry in _sessionScoped.entries) {
      test(entry.key, () async {
        container.listen(entry.value, (_, _) {});
        await _settle();
        expect(
          client.reads,
          isNotEmpty,
          reason: '${entry.key} never loaded, so this test proves nothing',
        );

        client.sent.clear();
        await container.read(authProvider.notifier).signOut();
        await _settle();

        expect(
          client.reads,
          isNotEmpty,
          reason: '${entry.key} kept the ended session\'s data',
        );
      });
    }
  });

  test('sign-out empties the approvals list', () async {
    container.listen(approvalsProvider, (_, _) {});
    await container
        .read(approvalsProvider.notifier)
        .fetch(const ApprovalsQuery());
    expect(container.read(approvalsProvider), hasLength(1));

    await container.read(authProvider.notifier).signOut();

    expect(container.read(approvalsProvider), isEmpty);
  });

  test('every kept-alive provider is either dropped or exempt', () {
    // A top-level `final x = <Kind>Provider...` whose declaration does not say
    // autoDispose. Plain `Provider`s are left out: they are synchronous, so
    // they can only derive from another provider, and rebuild with it.
    final declaration = RegExp(
      r'^final (\w+)\s*=\s*((?:AsyncNotifier|Notifier|Future|Stream)Provider[^<(]*)',
      multiLine: true,
    );
    final found = <String>{};
    for (final file in Directory('lib/providers').listSync()) {
      if (file is! File || !file.path.endsWith('.dart')) continue;
      for (final m in declaration.allMatches(file.readAsStringSync())) {
        if (!m.group(2)!.contains('autoDispose')) found.add(m.group(1)!);
      }
    }

    expect(found, isNotEmpty);
    final unaccounted = found
        .where(
          (name) =>
              !_sessionScoped.containsKey(name) &&
              name != _filledOnDemand &&
              !_notSessionScoped.containsKey(name),
        )
        .toList();
    expect(
      unaccounted,
      isEmpty,
      reason:
          'Add each to AuthNotifier._dropSessionCaches and to _sessionScoped '
          'here, or say in _notSessionScoped why it holds no session data.',
    );
    // And nothing listed here has quietly gone away.
    final stale = {
      ..._sessionScoped.keys,
      _filledOnDemand,
      ..._notSessionScoped.keys,
    }.where((name) => !found.contains(name)).toList();
    expect(stale, isEmpty);
  });

  test('the drop names every session-scoped provider', () {
    final source = File('lib/providers/auth_provider.dart').readAsStringSync();
    final body = source.substring(
      source.indexOf('void _dropSessionCaches()'),
      source.indexOf('void clearError()'),
    );
    final missing = [
      ..._sessionScoped.keys,
      _filledOnDemand,
    ].where((name) => !body.contains('ref.invalidate($name);')).toList();
    expect(missing, isEmpty);
  });
}
