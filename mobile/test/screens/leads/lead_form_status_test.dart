import 'package:bottle_crm/core/theme/theme.dart';
import 'package:bottle_crm/data/models/auth_response.dart';
import 'package:bottle_crm/data/models/lead.dart';
import 'package:bottle_crm/providers/auth_provider.dart';
import 'package:bottle_crm/providers/leads_provider.dart';
import 'package:bottle_crm/providers/lookup_provider.dart';
import 'package:bottle_crm/screens/leads/lead_form_screen.dart';
import 'package:bottle_crm/services/api_service.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';

/// Editing a lead sends `status` only when it changed.
///
/// `LeadCreateSerializer.validate_status` refuses ANY status on a converted lead,
/// the same value included, because re-sending "converted" would run the
/// conversion a second time. The form used to send `status` on every save, so
/// no other field of a converted lead could be edited from the phone.
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  Lead lead(String status) => Lead.fromJson({
    'id': 'lead-1',
    'title': 'Engine enquiry',
    'first_name': 'Ada',
    'last_name': 'Lovelace',
    'email': 'ada@example.com',
    'company_name': 'Analytical Engines',
    'status': status,
  });

  Future<_FakeLeads> open(WidgetTester tester, Lead stored) async {
    final fake = _FakeLeads(stored);
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          authProvider.overrideWith(_FakeAuth.new),
          leadsProvider.overrideWith(() => fake),
          usersProvider.overrideWithValue(const []),
          tagsProvider.overrideWithValue(const []),
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

  testWidgets('editing another field of a converted lead sends no status', (
    tester,
  ) async {
    final fake = await open(tester, lead('converted'));
    await tester.enterText(
      find.widgetWithText(TextFormField, 'Analytical Engines'),
      'Difference Engines',
    );
    await save(tester);

    expect(fake.updates, hasLength(1));
    expect(fake.updates.single.containsKey('status'), isFalse);
    expect(fake.updates.single['company_name'], 'Difference Engines');
    // Saved, so the form closed back to the list.
    expect(find.text('list'), findsOneWidget);
  });

  testWidgets('an unchanged status is left out on any lead', (tester) async {
    final fake = await open(tester, lead('assigned'));
    await save(tester);
    expect(fake.updates.single.containsKey('status'), isFalse);
  });

  testWidgets('a changed status is still sent', (tester) async {
    final fake = await open(tester, lead('assigned'));
    final field = find.text('Assigned');
    await tester.ensureVisible(field.first);
    await tester.tap(field.first);
    await tester.pumpAndSettle();
    await tester.tap(find.text('In Process').last);
    await tester.pumpAndSettle();
    await save(tester);

    expect(fake.updates.single['status'], 'in process');
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

  /// Every update body, in order. Answers success, the way the API does once
  /// no status is sent for a converted lead.
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
