import 'dart:convert';

import 'package:bottle_crm/core/theme/theme.dart';
import 'package:bottle_crm/data/models/lookup_models.dart';
import 'package:bottle_crm/data/models/macro.dart';
import 'package:bottle_crm/data/models/ticket.dart';
import 'package:bottle_crm/data/models/time_entry.dart';
import 'package:bottle_crm/providers/lookup_provider.dart';
import 'package:bottle_crm/providers/settings_provider.dart';
import 'package:bottle_crm/providers/tickets_provider.dart';
import 'package:bottle_crm/screens/settings/macro_form_sheet.dart';
import 'package:bottle_crm/screens/tickets/macro_picker_sheet.dart';
import 'package:bottle_crm/screens/tickets/ticket_detail_screen.dart';
import 'package:bottle_crm/services/api_service.dart';
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:http/http.dart' as http;
import 'package:lucide_icons_flutter/lucide_icons.dart';

/// Macro actions (G18) on the phone, at 390px and with large text.
///
/// * The picker applies a macro with no text at once, behind an Apply button.
/// * A macro with text and actions fills the composer and puts its actions on
///   it as removable chips; the kept ones apply right after the reply posts,
///   and only those.
/// * The macro form edits the four actions and keeps a deactivated assignee
///   the macro already carries.
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  void usePhone(WidgetTester tester, {double textScale = 1.0}) {
    tester.view.devicePixelRatio = 3.0;
    tester.view.physicalSize = const Size(390 * 3, 844 * 3);
    tester.platformDispatcher.textScaleFactorTestValue = textScale;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    addTearDown(tester.platformDispatcher.clearTextScaleFactorTestValue);
  }

  late _RoutingClient client;
  setUp(() {
    client = _RoutingClient();
    ApiService().setClientForTesting(client);
  });
  tearDown(() => ApiService().setClientForTesting(http.Client()));

  final withText = Macro.fromJson(const {
    'id': 'm1',
    'title': 'Waiting on the customer',
    'body': 'We have asked for the logs.',
    'scope': 'org',
    'set_status': 'Pending',
    'set_priority': 'High',
    'add_tags_details': [
      {'id': 't1', 'name': 'waiting', 'is_active': true},
    ],
  });
  final actionsOnly = Macro.fromJson(const {
    'id': 'm2',
    'title': 'Escalate to billing',
    'body': '',
    'scope': 'org',
    'set_priority': 'Urgent',
    'set_assignees_details': [
      {
        'id': 'p1',
        'name': 'Bea Billing',
        'email': 'bea@x.test',
        'is_active': true,
      },
    ],
  });

  group('the picker', () {
    MacroPick? picked;

    Widget pickerApp() => ProviderScope(
      overrides: [
        activeMacrosProvider.overrideWith(
          (ref) async => [withText, actionsOnly],
        ),
      ],
      child: MaterialApp(
        theme: AppTheme.light,
        home: Scaffold(
          body: Builder(
            builder: (context) => Center(
              child: ElevatedButton(
                onPressed: () async =>
                    picked = await showMacroPickerSheet(context, 'c1'),
                child: const Text('open'),
              ),
            ),
          ),
        ),
      ),
    );

    for (final scale in [1.0, 1.3]) {
      testWidgets('shows Apply only for a macro with no text, at $scale', (
        tester,
      ) async {
        usePhone(tester, textScale: scale);
        await tester.pumpWidget(pickerApp());
        await tester.tap(find.text('open'));
        await tester.pumpAndSettle();

        expect(find.text('Apply'), findsOneWidget);
        expect(find.textContaining('Status: Pending'), findsOneWidget);
        expect(find.textContaining('Assign: Bea Billing'), findsOneWidget);
        expect(tester.takeException(), isNull);
      });
    }

    testWidgets('Apply applies every action at once and says what it did', (
      tester,
    ) async {
      usePhone(tester);
      client.routes['/macros/m2/apply/'] = (
        200,
        {
          'applied': ['priority', 'assignees'],
          'skipped': [],
        },
      );
      await tester.pumpWidget(pickerApp());
      await tester.tap(find.text('open'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Apply'));
      await tester.pumpAndSettle();

      expect(client.bodies['/macros/m2/apply/'], {'case_id': 'c1'});
      expect(picked?.appliedSummary, 'Macro applied: priority, assignees.');
      expect(picked?.text, isNull);
    });

    testWidgets('a refused apply stays in the sheet with the reason', (
      tester,
    ) async {
      usePhone(tester);
      client.routes['/macros/m2/apply/'] = (
        403,
        {'detail': 'You do not have Permission to perform this action'},
      );
      await tester.pumpWidget(pickerApp());
      await tester.tap(find.text('open'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Apply'));
      await tester.pumpAndSettle();

      expect(find.textContaining('do not have Permission'), findsOneWidget);
      expect(tester.takeException(), isNull);
    });
  });

  group('the composer', () {
    Widget ticketApp(_FakeTicketsNotifier notifier, {Macro? macro}) =>
        ProviderScope(
          overrides: [
            ticketsProvider.overrideWith(() => notifier),
            usersProvider.overrideWithValue(const []),
            tagsProvider.overrideWithValue(const []),
            accountOptionsProvider.overrideWithValue(const []),
            activeMacrosProvider.overrideWith(
              (ref) async => [macro ?? withText],
            ),
          ],
          child: MaterialApp.router(
            theme: AppTheme.light,
            routerConfig: GoRouter(
              initialLocation: '/here',
              routes: [
                GoRoute(
                  path: '/here',
                  builder: (_, _) => const TicketDetailScreen(ticketId: 'c1'),
                ),
              ],
            ),
          ),
        );

    Future<void> pickIntoComposer(WidgetTester tester) async {
      client.routes['/macros/m1/render/'] = (
        200,
        {'rendered_body': 'We have asked for the logs.'},
      );
      await tester.tap(find.text('Comments'));
      await tester.pumpAndSettle();
      await tester.tap(find.byTooltip('Saved reply'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Waiting on the customer'));
      await tester.pumpAndSettle();
    }

    for (final scale in [1.0, 1.3]) {
      testWidgets('shows the macro actions as chips, at $scale', (
        tester,
      ) async {
        usePhone(tester, textScale: scale);
        await tester.pumpWidget(ticketApp(_FakeTicketsNotifier()));
        await tester.pumpAndSettle();
        await pickIntoComposer(tester);

        expect(find.text('Also on send'), findsOneWidget);
        expect(find.text('Status: Pending'), findsOneWidget);
        expect(find.text('Priority: High'), findsOneWidget);
        expect(find.text('Tag: waiting'), findsOneWidget);
        expect(tester.takeException(), isNull);
      });
    }

    for (final scale in [1.0, 1.3]) {
      testWidgets('a long assignee chip is readable in full, at $scale', (
        tester,
      ) async {
        usePhone(tester, textScale: scale);
        final manyAssignees = Macro.fromJson(const {
          'id': 'm1',
          'title': 'Waiting on the customer',
          'body': 'We have asked for the logs.',
          'scope': 'org',
          'set_assignees_details': [
            {'id': 'p1', 'name': 'Bea Billing', 'is_active': true},
            {
              'id': 'p2',
              'name': 'Carlos Castellanos-Rivera',
              'is_active': true,
            },
            {'id': 'p3', 'name': 'Dominique Whitfield', 'is_active': true},
          ],
        });
        const label =
            'Assign: Bea Billing, Carlos Castellanos-Rivera, '
            'Dominique Whitfield';
        await tester.pumpWidget(
          ticketApp(_FakeTicketsNotifier(), macro: manyAssignees),
        );
        await tester.pumpAndSettle();
        await pickIntoComposer(tester);

        expect(find.text(label), findsOneWidget);
        expect(tester.takeException(), isNull);
        // The label wraps onto more lines and its box grows to hold them, so
        // nothing is faded or clipped. (A chip kept one line and faded it.)
        final paragraph = tester.renderObject<RenderParagraph>(
          find.text(label),
        );
        expect(paragraph.didExceedMaxLines, isFalse);
        final boxes = paragraph.getBoxesForSelection(
          const TextSelection(baseOffset: 0, extentOffset: label.length),
        );
        expect(boxes.map((b) => b.top).toSet().length, greaterThan(1));
        expect(
          boxes.map((b) => b.bottom).reduce((a, b) => a > b ? a : b),
          lessThanOrEqualTo(paragraph.size.height + 0.5),
        );
        final chip = tester.getRect(
          find.byKey(const ValueKey('macro-action-assignees')),
        );
        expect(chip.right, lessThanOrEqualTo(390));
        expect(chip.height, greaterThanOrEqualTo(44));
        // Taking it off still works from its delete button.
        expect(find.byTooltip('Do not apply this'), findsOneWidget);
      });
    }

    testWidgets('sends the reply, then applies only the chips left on', (
      tester,
    ) async {
      usePhone(tester);
      client.routes['/macros/m1/apply/'] = (
        200,
        {
          'applied': ['status', 'tags'],
          'skipped': [],
        },
      );
      final notifier = _FakeTicketsNotifier();
      await tester.pumpWidget(ticketApp(notifier));
      await tester.pumpAndSettle();
      await pickIntoComposer(tester);

      // Take the priority chip off.
      final priorityChip = find.byKey(const ValueKey('macro-action-priority'));
      await tester.tap(
        find.descendant(
          of: priorityChip,
          matching: find.byTooltip('Do not apply this'),
        ),
      );
      await tester.pumpAndSettle();
      expect(find.text('Priority: High'), findsNothing);

      await tester.tap(find.byIcon(LucideIcons.send));
      await tester.pumpAndSettle();

      expect(notifier.sentComments, ['We have asked for the logs.']);
      expect(client.bodies['/macros/m1/apply/'], {
        'case_id': 'c1',
        'only': ['status', 'tags'],
      });
      expect(find.text('Macro applied: status, tags.'), findsOneWidget);
      expect(find.text('Also on send'), findsNothing);
    });

    testWidgets(
      'a refused apply says the reply went and why the rest did not',
      (tester) async {
        usePhone(tester);
        client.routes['/macros/m1/apply/'] = (
          400,
          {
            'error': true,
            'errors': {
              'status': [
                'This ticket is merged into another. Unmerge it first to '
                    'change its status.',
              ],
            },
          },
        );
        final notifier = _FakeTicketsNotifier();
        await tester.pumpWidget(ticketApp(notifier));
        await tester.pumpAndSettle();
        await pickIntoComposer(tester);
        await tester.tap(find.byIcon(LucideIcons.send));
        await tester.pumpAndSettle();

        expect(notifier.sentComments, hasLength(1));
        expect(
          find.textContaining("Reply posted, but the macro's actions were not"),
          findsOneWidget,
        );
        expect(find.textContaining('Unmerge it first'), findsOneWidget);
      },
    );

    testWidgets('a reader gets no composer, so no chips', (tester) async {
      usePhone(tester);
      await tester.pumpWidget(
        ticketApp(_FakeTicketsNotifier(commentPermission: false)),
      );
      await tester.pumpAndSettle();
      await tester.tap(find.text('Comments'));
      await tester.pumpAndSettle();

      expect(find.byTooltip('Saved reply'), findsNothing);
    });
  });

  group('the macro form', () {
    Map<String, dynamic>? saved;

    Widget formApp(Macro? existing) => ProviderScope(
      overrides: [
        usersProvider.overrideWithValue(const [
          UserLookup(
            id: 'p1',
            email: 'bea@x.test',
            name: 'Bea Billing',
            role: 'USER',
            isActive: true,
          ),
        ]),
        tagsProvider.overrideWithValue(const [
          TagLookup(id: 't1', name: 'waiting', slug: 'waiting', color: 'blue'),
        ]),
      ],
      child: MaterialApp(
        theme: AppTheme.light,
        home: Scaffold(
          body: Builder(
            builder: (context) => Center(
              child: ElevatedButton(
                onPressed: () async => saved = await showMacroFormSheet(
                  context,
                  existing: existing,
                  canCreateOrg: true,
                  placeholders: const [],
                ),
                child: const Text('open'),
              ),
            ),
          ),
        ),
      ),
    );

    final carriesGone = Macro.fromJson(const {
      'id': 'm9',
      'title': 'Hand over',
      'body': 'Passing this on.',
      'scope': 'org',
      'set_status': 'Assigned',
      'set_assignees_details': [
        {'id': 'p9', 'name': 'Gone Person', 'is_active': false},
      ],
    });

    for (final scale in [1.0, 1.3]) {
      testWidgets('renders the four actions without overflowing, at $scale', (
        tester,
      ) async {
        usePhone(tester, textScale: scale);
        await tester.pumpWidget(formApp(carriesGone));
        await tester.tap(find.text('open'));
        await tester.pumpAndSettle();

        expect(find.text('Set status'), findsOneWidget);
        expect(find.text('Set priority'), findsOneWidget);
        expect(find.text('Gone Person (deactivated)'), findsOneWidget);
        expect(tester.takeException(), isNull);
      });
    }

    testWidgets('an edit resends the stored deactivated assignee', (
      tester,
    ) async {
      usePhone(tester);
      await tester.pumpWidget(formApp(carriesGone));
      await tester.tap(find.text('open'));
      await tester.pumpAndSettle();
      await tester.ensureVisible(find.text('Save changes'));
      await tester.tap(find.text('Save changes'));
      await tester.pumpAndSettle();

      expect(saved?['set_status'], 'Assigned');
      expect(saved?['set_priority'], '');
      expect(saved?['set_assignees'], ['p9']);
      expect(saved?['add_tags'], isEmpty);
    });

    testWidgets(
      'the assignee picker offers active members and the stored one',
      (tester) async {
        usePhone(tester, textScale: 1.3);
        await tester.pumpWidget(formApp(carriesGone));
        await tester.tap(find.text('open'));
        await tester.pumpAndSettle();
        await tester.ensureVisible(find.text('Assign to'));
        await tester.pumpAndSettle();
        await tester.tap(find.text('Gone Person (deactivated)'));
        await tester.pumpAndSettle();

        expect(find.text('Bea Billing'), findsOneWidget);
        expect(find.text('Gone Person (deactivated)'), findsWidgets);
        expect(tester.takeException(), isNull);
      },
    );

    testWidgets('a macro with only an action and no text can be saved', (
      tester,
    ) async {
      usePhone(tester);
      await tester.pumpWidget(formApp(null));
      await tester.tap(find.text('open'));
      await tester.pumpAndSettle();
      await tester.enterText(find.widgetWithText(TextField, 'Title'), 'Close');
      final status = find.ancestor(
        of: find.text('Set status'),
        matching: find.byType(DropdownButtonFormField<String>),
      );
      await tester.ensureVisible(status);
      await tester.pumpAndSettle();
      await tester.tap(status);
      await tester.pumpAndSettle();
      await tester.tap(find.text('Closed').last);
      await tester.pumpAndSettle();
      await tester.ensureVisible(find.text('Save reply'));
      await tester.tap(find.text('Save reply'));
      await tester.pumpAndSettle();

      expect(saved?['body'], '');
      expect(saved?['set_status'], 'Closed');
    });

    testWidgets('no text and no action is refused before any request', (
      tester,
    ) async {
      usePhone(tester);
      saved = null;
      await tester.pumpWidget(formApp(null));
      await tester.tap(find.text('open'));
      await tester.pumpAndSettle();
      await tester.enterText(find.widgetWithText(TextField, 'Title'), 'Empty');
      await tester.ensureVisible(find.text('Save reply'));
      await tester.tap(find.text('Save reply'));
      await tester.pumpAndSettle();

      expect(saved, isNull);
      expect(find.textContaining('something to do'), findsOneWidget);
    });
  });
}

/// Answers each path with a canned status and JSON body, and keeps the JSON
/// body each path was sent.
class _RoutingClient extends http.BaseClient {
  final Map<String, (int, Map<String, dynamic>)> routes = {};
  final Map<String, dynamic> bodies = {};

  @override
  Future<http.StreamedResponse> send(http.BaseRequest request) async {
    final path = request.url.path.replaceFirst(RegExp(r'^/api'), '');
    final raw = utf8.decode(await request.finalize().toBytes());
    if (raw.isNotEmpty) bodies[path] = jsonDecode(raw);
    final (status, body) = routes[path] ?? (200, <String, dynamic>{});
    return http.StreamedResponse(
      Stream.value(utf8.encode(jsonEncode(body))),
      status,
      request: request,
    );
  }
}

class _FakeTicketsNotifier extends TicketsNotifier {
  _FakeTicketsNotifier({this.commentPermission = true});

  final bool commentPermission;
  final List<String> sentComments = [];

  @override
  Future<TicketsListData> build() async =>
      TicketsListData(tickets: const [], totalCount: 0, hasMore: false);

  @override
  Future<TicketDetailResult?> getTicketDetail(String id) async =>
      TicketDetailResult(
        ticketObj: Ticket.fromJson({
          'id': id,
          'name': 'Printer on fire',
          'status': 'New',
          'priority': 'Normal',
          'created_at': '2026-09-01T09:00:00Z',
        }),
        activities: const [],
        commentPermission: commentPermission,
        internalCommentIds: const {},
      );

  @override
  Future<ApiResponse<Map<String, dynamic>>> addComment(
    String ticketId,
    String comment, {
    bool isInternal = false,
  }) async {
    sentComments.add(comment);
    return const ApiResponse(success: true, data: null, statusCode: 200);
  }

  @override
  Future<TicketWatchers?> getWatchers(String id) async => const TicketWatchers(
    watchers: [],
    count: 0,
    isCurrentUserWatching: false,
  );

  @override
  Future<TicketTreeNode?> fetchTree(String id) async => null;

  @override
  Future<List<TimeEntry>> fetchTimeEntries(String id) async => const [];

  @override
  Future<TimeSummary?> fetchTimeSummary(String id) async => null;
}
