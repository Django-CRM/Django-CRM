import 'package:bottle_crm/core/theme/theme.dart';
import 'package:bottle_crm/data/models/auth_response.dart';
import 'package:bottle_crm/data/models/lookup_models.dart';
import 'package:bottle_crm/data/models/solution.dart';
import 'package:bottle_crm/providers/auth_provider.dart';
import 'package:bottle_crm/providers/lookup_provider.dart';
import 'package:bottle_crm/providers/solutions_provider.dart';
import 'package:bottle_crm/screens/solutions/solution_detail_screen.dart';
import 'package:flutter/material.dart';
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
}
