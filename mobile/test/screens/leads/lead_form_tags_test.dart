import 'package:bottle_crm/core/theme/theme.dart';
import 'package:bottle_crm/data/models/auth_response.dart';
import 'package:bottle_crm/data/models/lead.dart';
import 'package:bottle_crm/data/models/lookup_models.dart';
import 'package:bottle_crm/providers/auth_provider.dart';
import 'package:bottle_crm/providers/leads_provider.dart';
import 'package:bottle_crm/providers/lookup_provider.dart';
import 'package:bottle_crm/screens/leads/lead_form_screen.dart';
import 'package:bottle_crm/services/api_service.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';

/// Editing a lead sends `tags` and `assigned_to` only when that selection
/// changed.
///
/// Lead PATCH leaves an absent list alone, but replaces the whole set with the
/// ACTIVE ids in a present one. The pickers offer active tags and people only,
/// so sending both on every save dropped each archived tag and deactivated
/// assignee from the lead, whatever else the save was for.
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  // One active tag the picker offers, and one archived since that it does not.
  final stored = Lead.fromJson({
    'id': 'lead-1',
    'title': 'Engine enquiry',
    'first_name': 'Ada',
    'last_name': 'Lovelace',
    'email': 'ada@example.com',
    'company_name': 'Analytical Engines',
    'status': 'assigned',
    'tags': [
      {'id': 't1', 'name': 'Priority'},
      {'id': 't-old', 'name': 'Legacy'},
    ],
    'assigned_to': [
      {
        'id': 'p-gone',
        'user_details': {'email': 'gone@example.com'},
      },
    ],
  });
  const offered = [
    TagLookup(id: 't1', name: 'Priority', slug: 'priority', color: 'red'),
    TagLookup(id: 't2', name: 'Renewal', slug: 'renewal', color: 'blue'),
  ];

  Future<_FakeLeads> open(
    WidgetTester tester, {
    List<UserLookup> people = const [],
  }) async {
    final fake = _FakeLeads(stored);
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          authProvider.overrideWith(_FakeAuth.new),
          leadsProvider.overrideWith(() => fake),
          usersProvider.overrideWithValue(people),
          tagsProvider.overrideWithValue(offered),
        ],
        child: MaterialApp.router(
          theme: AppTheme.light,
          routerConfig: GoRouter(
            initialLocation: '/leads/edit',
            routes: [
              GoRoute(
                path: '/leads',
                builder: (_, _) => const Text('list'),
                routes: [
                  GoRoute(
                    path: 'edit',
                    builder: (_, _) => const LeadFormScreen(leadId: 'lead-1'),
                  ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();
    return fake;
  }

  Future<void> save(WidgetTester tester) async {
    await tester.tap(find.text('Update Lead'));
    await tester.pumpAndSettle();
  }

  testWidgets('an edit that leaves tags and assignees alone sends neither', (
    tester,
  ) async {
    final fake = await open(tester);
    await tester.enterText(
      find.widgetWithText(TextFormField, 'Analytical Engines'),
      'Difference Engines',
    );
    await save(tester);

    expect(fake.updates, hasLength(1));
    expect(fake.updates.single.containsKey('tags'), isFalse);
    expect(fake.updates.single.containsKey('assigned_to'), isFalse);
    expect(fake.updates.single['company_name'], 'Difference Engines');
  });

  testWidgets('a changed selection sends the new set', (tester) async {
    final fake = await open(tester);
    final field = find.text('Priority');
    await tester.ensureVisible(field);
    await tester.tap(field);
    await tester.pumpAndSettle();
    await tester.tap(find.text('Renewal'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Done'));
    await tester.pumpAndSettle();
    await save(tester);

    expect(
      (fake.updates.single['tags'] as List).toSet(),
      containsAll(<String>['t1', 't2']),
    );
    // Only the list that changed is sent.
    expect(fake.updates.single.containsKey('assigned_to'), isFalse);
  });

  testWidgets('a changed assignee sends the new set', (tester) async {
    final fake = await open(
      tester,
      people: const [
        UserLookup(
          id: 'p1',
          email: 'grace@example.com',
          name: 'Grace Hopper',
          role: 'USER',
          isActive: true,
        ),
      ],
    );
    final field = find.text('1 selected');
    await tester.ensureVisible(field);
    await tester.tap(field);
    await tester.pumpAndSettle();
    await tester.tap(find.text('Grace Hopper'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Done'));
    await tester.pumpAndSettle();
    await save(tester);

    expect(
      (fake.updates.single['assigned_to'] as List).toSet(),
      containsAll(<String>['p1']),
    );
    expect(fake.updates.single.containsKey('tags'), isFalse);
  });
}

class _FakeAuth extends AuthNotifier {
  @override
  AuthState build() {
    final org = Organization(id: 'org-1', name: 'Test Org', role: 'ADMIN');
    return AuthState(
      user: AuthUser(id: 'u1', email: 'ada@example.com'),
      organizations: [org],
      selectedOrganization: org,
      isAuthenticated: true,
    );
  }
}

class _FakeLeads extends LeadsNotifier {
  _FakeLeads(this.stored);

  final Lead stored;

  /// Every update body, in order.
  final List<Map<String, dynamic>> updates = [];

  @override
  Future<LeadsListData> build() async => const LeadsListData();

  @override
  Future<LeadDetail?> getLeadDetail(String id) async =>
      LeadDetail(lead: stored);

  @override
  Future<ApiResponse<Map<String, dynamic>>> updateLead(
    String id,
    Map<String, dynamic> leadData,
  ) async {
    updates.add(leadData);
    return const ApiResponse(success: true, data: {}, statusCode: 200);
  }
}
