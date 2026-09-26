import 'package:bottle_crm/core/theme/theme.dart';
import 'package:bottle_crm/data/models/crm_document.dart';
import 'package:bottle_crm/data/models/lookup_models.dart';
import 'package:bottle_crm/providers/auth_provider.dart';
import 'package:bottle_crm/providers/documents_provider.dart';
import 'package:bottle_crm/providers/lookup_provider.dart';
import 'package:bottle_crm/screens/documents/document_form_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';

/// Saving a document edit must not shrink who can open it.
///
/// `DocumentDetailView.put` replaces `shared_to` and `teams` with the body's
/// lists, and keeps a person deactivated since only when the body names them
/// again (they have no checkbox, as the picker lists active people only). So a
/// change of sharing must carry the inactive id, and a save that leaves
/// sharing alone leaves both lists out (null, which the provider turns into a
/// PATCH), which also covers a people or teams list that never arrived.
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  const people = [
    UserLookup(
      id: 'p1',
      email: 'ada@example.com',
      name: 'Ada Lovelace',
      role: 'USER',
      isActive: true,
    ),
    UserLookup(
      id: 'p2',
      email: 'grace@example.com',
      name: 'Grace Hopper',
      role: 'USER',
      isActive: true,
    ),
  ];
  const teams = [TeamLookup(id: 't1', name: 'Support')];

  // The form's own list, not the title field's inner scrollable.
  final form = find
      .descendant(of: find.byType(ListView), matching: find.byType(Scrollable))
      .first;

  Future<_FakeDocuments> saveAfter(
    WidgetTester tester, {
    List<UserLookup> people = people,
    List<TeamLookup> teams = teams,
    String? tick,
  }) async {
    final fake = _FakeDocuments();
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          documentsProvider.overrideWith(() => fake),
          isOrgAdminProvider.overrideWithValue(true),
          myEmailProvider.overrideWithValue('ada@example.com'),
          usersProvider.overrideWithValue(people),
          teamsProvider.overrideWithValue(teams),
        ],
        child: MaterialApp.router(
          theme: AppTheme.light,
          routerConfig: GoRouter(
            initialLocation: '/documents/d1',
            routes: [
              GoRoute(
                path: '/documents',
                builder: (_, _) => const Text('list'),
                routes: [
                  GoRoute(
                    path: 'd1',
                    builder: (_, _) =>
                        const DocumentFormScreen(documentId: 'd1'),
                  ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();
    if (tick != null) {
      final box = find.text(tick);
      await tester.scrollUntilVisible(box, 120, scrollable: form);
      await tester.tap(box);
      await tester.pumpAndSettle();
    }
    final save = find.text('Save changes');
    await tester.scrollUntilVisible(save, 120, scrollable: form);
    // `scrollUntilVisible` stops once any part is on screen, which can leave
    // the button's centre just below the edge.
    await tester.ensureVisible(save);
    await tester.pumpAndSettle();
    await tester.tap(save);
    await tester.pumpAndSettle();
    return fake;
  }

  testWidgets('a rename leaves sharing out, so the inactive share survives', (
    tester,
  ) async {
    final fake = await saveAfter(tester);
    expect(fake.saves, hasLength(1));
    expect(fake.saves.single.sharedTo, isNull);
    expect(fake.saves.single.teams, isNull);
    expect(fake.saves.single.title, 'Signed contract');
  });

  testWidgets('says the inactive share is there, instead of hiding it', (
    tester,
  ) async {
    final fake = _FakeDocuments();
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          documentsProvider.overrideWith(() => fake),
          isOrgAdminProvider.overrideWithValue(true),
          myEmailProvider.overrideWithValue('ada@example.com'),
          usersProvider.overrideWithValue(people),
          teamsProvider.overrideWithValue(teams),
        ],
        child: MaterialApp(
          theme: AppTheme.light,
          home: const DocumentFormScreen(documentId: 'd1'),
        ),
      ),
    );
    await tester.pumpAndSettle();
    final hint = find.textContaining('one person no longer active');
    await tester.scrollUntilVisible(hint, 120, scrollable: form);
    expect(hint, findsOneWidget);
    // A change of sharing resubmits the share and the server keeps it, so the
    // old warning that changing sharing would drop it is no longer true.
    expect(find.textContaining('Saving keeps that share.'), findsOneWidget);
    expect(find.textContaining('unless you change'), findsNothing);
  });

  testWidgets('changing a share sends both lists, the inactive id included', (
    tester,
  ) async {
    final fake = await saveAfter(tester, tick: 'Grace Hopper');
    final save = fake.saves.single;
    expect(save.sharedTo?.toSet(), {'p1', 'p-gone', 'p2'});
    expect(save.teams, ['t1']);
  });

  testWidgets('with no people or teams list, a save still keeps every share', (
    tester,
  ) async {
    final fake = await saveAfter(tester, people: const [], teams: const []);
    expect(fake.saves.single.sharedTo, isNull);
    expect(fake.saves.single.teams, isNull);
  });
}

class _Save {
  _Save(this.title, this.sharedTo, this.teams);
  final String title;
  final List<String>? sharedTo;
  final List<String>? teams;
}

/// One document Ada uploaded, shared with Ada (active), with somebody since
/// deactivated (absent from the picker list), and with a team.
class _FakeDocuments extends DocumentsNotifier {
  final List<_Save> saves = [];

  @override
  Future<DocumentsData> build() async {
    final documents = [
      CrmDocument.fromJson(const {
        'id': 'd1',
        'title': 'Signed contract',
        'document_file': 'documents/2026/signed-contract.pdf',
        'status': 'active',
        'shared_to': [
          {
            'id': 'p1',
            'user_details': {
              'name': 'Ada Lovelace',
              'email': 'ada@example.com',
            },
          },
          {
            'id': 'p-gone',
            'user_details': {'name': 'Left Company', 'email': 'gone@x.io'},
          },
        ],
        'teams': [
          {'id': 't1', 'name': 'Support', 'member_count': 3},
        ],
        'created_by': {
          'id': 'u1',
          'name': 'Ada Lovelace',
          'email': 'ada@example.com',
        },
      }),
    ];
    return DocumentsData(
      documents: documents,
      totals: documentTotals(documents),
    );
  }

  @override
  Future<ApiResponse<Map<String, dynamic>>> updateDocument(
    String id, {
    required String title,
    required String status,
    List<String>? sharedTo,
    List<String>? teams,
    String? filePath,
    String? fileName,
  }) async {
    saves.add(_Save(title, sharedTo, teams));
    return const ApiResponse(success: true, data: {}, statusCode: 200);
  }
}
