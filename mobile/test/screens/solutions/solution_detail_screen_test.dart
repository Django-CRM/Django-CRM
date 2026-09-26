import 'package:bottle_crm/core/theme/theme.dart';
import 'package:bottle_crm/data/models/auth_response.dart';
import 'package:bottle_crm/data/models/lookup_models.dart';
import 'package:bottle_crm/data/models/solution.dart';
import 'package:bottle_crm/providers/auth_provider.dart';
import 'package:bottle_crm/providers/lookup_provider.dart';
import 'package:bottle_crm/providers/solutions_provider.dart';
import 'package:bottle_crm/screens/solutions/solution_detail_screen.dart';
import 'package:bottle_crm/services/api_service.dart';
import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

/// Opening an article from the knowledge base list showed an app bar, a delete
/// button, and nothing else: no title, no body, no status. The delete icon is
/// the tell, because it only renders once the fetch has produced a record.
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  Solution article({
    String author = 'author-1',
    SolutionStatus status = SolutionStatus.draft,
    List<String> tagIds = const [],
  }) => Solution(
    id: 'sol-1',
    title: 'Parity probe solution',
    description: 'Seeded so the solution detail route can be driven.',
    status: status,
    isPublished: false,
    createdById: author,
    tagIds: tagIds,
  );

  const orgTags = [
    TagLookup(
      id: 'tag-billing',
      name: 'Billing',
      slug: 'billing',
      color: 'blue',
    ),
    TagLookup(id: 'tag-access', name: 'Access', slug: 'access', color: 'green'),
  ];

  Widget host(
    Solution solution, {
    required String role,
    required String userId,
    List<TagLookup> tags = const [],
  }) => ProviderScope(
    overrides: [
      authProvider.overrideWith(() => _FakeAuth(role: role, userId: userId)),
      solutionsProvider.overrideWith(() => _FakeSolutions(solution)),
      tagsProvider.overrideWithValue(tags),
    ],
    child: MaterialApp(
      theme: AppTheme.light,
      home: const SolutionDetailScreen(solutionId: 'sol-1'),
    ),
  );

  testWidgets('an existing solution renders its title and body', (
    tester,
  ) async {
    await tester.pumpWidget(host(article(), role: 'ADMIN', userId: 'admin-1'));
    await tester.pumpAndSettle();

    expect(tester.takeException(), isNull);
    expect(find.text('Parity probe solution'), findsOneWidget);
    expect(
      find.text('Seeded so the solution detail route can be driven.'),
      findsOneWidget,
    );
  });

  group('tags', () {
    testWidgets('a chip is lit for each tag the article already carries', (
      tester,
    ) async {
      await tester.pumpWidget(
        host(
          article(tagIds: const ['tag-billing']),
          role: 'ADMIN',
          userId: 'admin-1',
          tags: orgTags,
        ),
      );
      await tester.pumpAndSettle();

      FilterChip chipNamed(String label) => tester.widget<FilterChip>(
        find.ancestor(of: find.text(label), matching: find.byType(FilterChip)),
      );
      expect(chipNamed('Billing').selected, isTrue);
      expect(chipNamed('Access').selected, isFalse);
    });

    testWidgets('the section is absent when the org has no tags', (
      tester,
    ) async {
      await tester.pumpWidget(
        host(article(), role: 'ADMIN', userId: 'admin-1', tags: const []),
      );
      await tester.pumpAndSettle();
      expect(find.text('TAGS'), findsNothing);
    });

    testWidgets('a reader who may not edit cannot change the tags', (
      tester,
    ) async {
      await tester.pumpWidget(
        host(
          article(tagIds: const ['tag-billing']),
          role: 'USER',
          userId: 'somebody-else',
          tags: orgTags,
        ),
      );
      await tester.pumpAndSettle();

      final chip = tester.widget<FilterChip>(
        find.ancestor(
          of: find.text('Access'),
          matching: find.byType(FilterChip),
        ),
      );
      expect(chip.onSelected, isNull, reason: 'the chip must be inert');
    });
  });

  group('saving tags', () {
    // `_apply_tags` replaces the article's tags with the ACTIVE ids in a
    // present `tags` key and leaves an absent one alone. The lookup offers
    // active tags only, so an archived tag the article carries has no chip.
    // Sending `tags` on every save dropped it, even with its id in the list.
    const archived = 'tag-archived';

    Future<_FakeSolutions> saveAfter(
      WidgetTester tester, {
      String? toggle,
    }) async {
      final fake = _FakeSolutions(
        article(tagIds: const ['tag-billing', archived]),
      );
      await tester.pumpWidget(
        ProviderScope(
          overrides: [
            authProvider.overrideWith(
              () => _FakeAuth(role: 'ADMIN', userId: 'admin-1'),
            ),
            solutionsProvider.overrideWith(() => fake),
            tagsProvider.overrideWithValue(orgTags),
          ],
          child: MaterialApp.router(
            theme: AppTheme.light,
            routerConfig: GoRouter(
              initialLocation: '/solutions/sol-1',
              routes: [
                GoRoute(
                  path: '/solutions',
                  builder: (_, _) => const Text('list'),
                  routes: [
                    GoRoute(
                      path: 'sol-1',
                      builder: (_, _) =>
                          const SolutionDetailScreen(solutionId: 'sol-1'),
                    ),
                  ],
                ),
              ],
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();
      if (toggle != null) {
        await tester.tap(find.text(toggle));
        await tester.pumpAndSettle();
      }
      final save = find.text('Save changes');
      await tester.ensureVisible(save);
      await tester.tap(save);
      await tester.pumpAndSettle();
      return fake;
    }

    testWidgets('an edit that leaves the tags alone does not send them, so an '
        'archived tag survives', (tester) async {
      final fake = await saveAfter(tester);
      expect(fake.updates, hasLength(1));
      expect(fake.updates.single.containsKey('tags'), isFalse);
      expect(fake.updates.single['title'], 'Parity probe solution');
    });

    testWidgets('changing a chip sends the whole list', (tester) async {
      final fake = await saveAfter(tester, toggle: 'Access');
      expect(fake.updates, hasLength(1));
      expect((fake.updates.single['tags'] as List).toSet(), {
        'tag-billing',
        archived,
        'tag-access',
      });
    });

    testWidgets('unticking a chip is still a deliberate removal', (
      tester,
    ) async {
      final fake = await saveAfter(tester, toggle: 'Billing');
      expect(fake.updates.single['tags'], [archived]);
    });
  });

  group('the three access rules the server draws', () {
    testWidgets('a member who did not write it can read it and nothing else', (
      tester,
    ) async {
      await tester.pumpWidget(
        host(
          article(author: 'someone-else'),
          role: 'USER',
          userId: 'member-1',
        ),
      );
      await tester.pumpAndSettle();

      // Reading is open to the org.
      expect(find.text('Parity probe solution'), findsOneWidget);
      // Writing and deleting are not.
      expect(find.byIcon(LucideIcons.trash2), findsNothing);
      expect(find.text('Save changes'), findsNothing);
    });

    testWidgets('the author may edit and delete their own article', (
      tester,
    ) async {
      await tester.pumpWidget(
        host(
          article(author: 'member-1'),
          role: 'USER',
          userId: 'member-1',
        ),
      );
      await tester.pumpAndSettle();

      expect(find.byIcon(LucideIcons.trash2), findsOneWidget);
      expect(find.text('Save changes'), findsOneWidget);
    });

    testWidgets('the author still may not publish an approved article', (
      tester,
    ) async {
      await tester.pumpWidget(
        host(
          article(author: 'member-1', status: SolutionStatus.approved),
          role: 'USER',
          userId: 'member-1',
        ),
      );
      await tester.pumpAndSettle();

      // Being the author grants write, and deliberately does not grant
      // release: `assert_solution_release_access` takes no article for exactly
      // this reason.
      expect(find.text('Save changes'), findsOneWidget);
      final publish = tester.widget<TextButton>(
        find.widgetWithText(TextButton, 'Publish'),
      );
      expect(publish.onPressed, isNull);
    });

    testWidgets('an admin may publish an approved article', (tester) async {
      await tester.pumpWidget(
        host(
          article(author: 'someone-else', status: SolutionStatus.approved),
          role: 'ADMIN',
          userId: 'admin-1',
        ),
      );
      await tester.pumpAndSettle();

      final publish = tester.widget<TextButton>(
        find.widgetWithText(TextButton, 'Publish'),
      );
      expect(publish.onPressed, isNotNull);
    });

    testWidgets('an admin may not publish a draft', (tester) async {
      await tester.pumpWidget(
        host(
          article(author: 'someone-else'),
          role: 'ADMIN',
          userId: 'admin-1',
        ),
      );
      await tester.pumpAndSettle();

      final publish = tester.widget<TextButton>(
        find.widgetWithText(TextButton, 'Publish'),
      );
      expect(publish.onPressed, isNull);
    });
  });
}

class _FakeAuth extends AuthNotifier {
  _FakeAuth({required this.role, required this.userId});

  final String role;
  final String userId;

  @override
  AuthState build() {
    final org = Organization(id: 'org-1', name: 'Test Org', role: role);
    return AuthState(
      user: AuthUser(id: userId, email: 'user@example.com'),
      organizations: [org],
      selectedOrganization: org,
      isAuthenticated: true,
    );
  }
}

class _FakeSolutions extends SolutionsNotifier {
  _FakeSolutions(this.solution);

  final Solution solution;

  @override
  SolutionsListData build() => SolutionsListData(solutions: [solution]);

  @override
  Future<void> refresh({
    String? search,
    SolutionStatus? status,
    bool? publishedOnly,
  }) async {}

  @override
  Future<Solution?> getById(String id) async => solution;

  /// Every update body, in order. Answers success so the screen pops.
  final List<Map<String, dynamic>> updates = [];

  @override
  Future<ApiResponse<Map<String, dynamic>>> update(
    String id,
    Map<String, dynamic> payload,
  ) async {
    updates.add(payload);
    return const ApiResponse(success: true, data: {}, statusCode: 200);
  }
}
