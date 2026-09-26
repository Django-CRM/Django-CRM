import 'package:bottle_crm/data/models/custom_field_definition.dart';
import 'package:bottle_crm/data/models/lookup_models.dart';
import 'package:bottle_crm/data/models/web_form.dart';
import 'package:bottle_crm/providers/auth_provider.dart';
import 'package:bottle_crm/providers/lookup_provider.dart';
import 'package:bottle_crm/providers/settings_provider.dart';
import 'package:bottle_crm/providers/web_forms_provider.dart';
import 'package:bottle_crm/routes/app_router.dart';
import 'package:bottle_crm/screens/settings/web_form_detail_screen.dart';
import 'package:bottle_crm/screens/settings/web_forms_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';

/// Web forms on the phone (issue #634).
///
/// Rendered rather than read. The default test surface is 800x600, which is
/// wider than any phone and therefore proves nothing about one: a Row that
/// overflows at 390 fits comfortably at 800 and the test stays green. Flutter
/// reports an overflow as a thrown FlutterError, so `tester.takeException()`
/// returning null is the assertion, and it is checked at two text scales
/// because a phone with large text is where a Row that only just fits stops
/// fitting. A tablet width is checked too, since mobile-first here means the
/// pressure runs the other way.
///
/// Beyond layout, the things worth pinning are the ones that are invisible
/// until a stranger is refused on somebody's website: who may write, whether
/// the reorder controls agree with each other, and that the captcha secret box
/// says which of "none stored" and "stored, hidden" it means.
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  /// An iPhone 15-ish logical viewport, the narrow end of what ships today.
  void useViewport(
    WidgetTester tester, {
    required Size size,
    double textScale = 1.0,
  }) {
    tester.view.devicePixelRatio = 3.0;
    tester.view.physicalSize = Size(size.width * 3, size.height * 3);
    tester.platformDispatcher.textScaleFactorTestValue = textScale;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    addTearDown(tester.platformDispatcher.clearTextScaleFactorTestValue);
  }

  const phone = Size(390, 844);
  const tablet = Size(834, 1112);

  Future<void> pump(
    WidgetTester tester,
    Widget app, {
    Size size = phone,
    double textScale = 1.0,
  }) async {
    useViewport(tester, size: size, textScale: textScale);
    await tester.pumpWidget(app);
    await tester.pumpAndSettle();
  }

  Widget listApp({required bool isAdmin, bool empty = false}) => ProviderScope(
    overrides: [
      webFormsProvider.overrideWith(
        empty ? _FakeNoWebForms.new : _FakeWebForms.new,
      ),
      isOrgAdminProvider.overrideWithValue(isAdmin),
    ],
    child: MaterialApp.router(
      routerConfig: GoRouter(
        initialLocation: '/here',
        routes: [
          GoRoute(path: '/here', builder: (_, _) => const WebFormsScreen()),
          GoRoute(
            path: '/more/settings/web-forms/:formId',
            builder: (_, s) => Text('editor ${s.pathParameters['formId']}'),
          ),
        ],
      ),
    ),
  );

  WebForm detailForm({
    bool published = false,
    bool hasSecret = false,
    String captcha = '',
    List<String> origins = const [],
    Map<String, dynamic>? assignee,
    // Labels deliberately unlike the lead-field names they write into. A row
    // prints both, so a fixture where they match makes every label assertion
    // ambiguous with the target line beneath it.
    List<Map<String, dynamic>> fields = const [
      {
        'source': 'lead',
        'lead_field': 'email',
        'label': 'Email address',
        'order': 0,
      },
      {
        'source': 'lead',
        'lead_field': 'first_name',
        'label': 'Given name',
        'order': 1,
      },
      {
        'source': 'lead',
        'lead_field': 'phone',
        'label': 'Telephone',
        'order': 2,
      },
    ],
  }) {
    return WebForm.fromJson({
      'id': 'f1',
      'name': 'Contact us',
      'is_published': published,
      'allowed_origins': origins,
      'submit_button_label': 'Send',
      'success_mode': 'message',
      'success_message': 'Thanks.',
      'lead_source': 'other',
      'assign_to': assignee?['id'],
      'assign_to_details': assignee,
      'captcha_provider': captcha,
      'captcha_site_key': captcha.isEmpty ? '' : 'site-key',
      'has_captcha_secret': hasSecret,
      'reject_disposable_email': true,
      'embed_html':
          '<iframe src="https://api.example.com/api/public/forms/'
          'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee/f1/embed/" '
          'style="width:100%;border:0" height="500" title="Contact us">'
          '</iframe>',
      'embed_js':
          '<div id="bottlecrm-webform-f1"></div>\n'
          '<script src="https://api.example.com/api/public/forms/'
          'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee/f1/embed.js" async></script>',
      'fields': fields,
    });
  }

  Widget detailApp({
    required bool isAdmin,
    WebForm? form,
    WebFormsNotifier Function() notifier = _FakeWebForms.new,
  }) => ProviderScope(
    overrides: [
      webFormsProvider.overrideWith(notifier),
      webFormDetailProvider('f1').overrideWith(
        (ref) async => WebFormDetail(
          form: form ?? detailForm(),
          submissions: [
            WebFormSubmission.fromJson(const {
              'id': 's1',
              'status': 'accepted',
              'lead': 'l1',
              'lead_name': 'Dana Fields',
              'referer': 'https://example.com/contact',
              'created_at': '2026-08-17T09:00:00Z',
            }),
            WebFormSubmission.fromJson(const {
              'id': 's2',
              'status': 'rejected_spam',
              'created_at': '2026-08-17T08:00:00Z',
            }),
          ],
          submissionCount: 2,
          analytics: WebFormAnalytics.fromJson(const {
            'window_days': 30,
            'totals': {
              'views': 40,
              'submissions': 8,
              'spam': 3,
              'conversion_rate': 0.2,
            },
          }),
        ),
      ),
      isOrgAdminProvider.overrideWithValue(isAdmin),
      usersProvider.overrideWithValue(const [
        UserLookup(
          id: 'p1',
          email: 'ada@example.com',
          name: 'Ada',
          role: 'ADMIN',
          isActive: true,
        ),
      ]),
      tagsProvider.overrideWithValue(const [
        TagLookup(id: 't1', name: 'Inbound', slug: 'inbound', color: 'gray'),
      ]),
      customFieldsProvider.overrideWith(_FakeLeadCustomFields.new),
    ],
    child: MaterialApp(home: WebFormDetailScreen(formId: 'f1')),
  );

  group('the list at 390px', () {
    testWidgets('renders without overflowing', (tester) async {
      await pump(tester, listApp(isAdmin: true));
      expect(tester.takeException(), isNull);
      expect(find.text('Contact us'), findsOneWidget);
    });

    testWidgets('fits with the system font scaled up', (tester) async {
      await pump(tester, listApp(isAdmin: true), textScale: 1.5);
      expect(tester.takeException(), isNull);
    });

    testWidgets('fits at a tablet width', (tester) async {
      await pump(tester, listApp(isAdmin: true), size: tablet);
      expect(tester.takeException(), isNull);
    });

    testWidgets('shows the org totals, not the page count', (tester) async {
      // Two rows on screen, thirteen forms in the org. Counting the rows would
      // print "2 published" on a settings screen and be quietly wrong.
      await pump(tester, listApp(isAdmin: true));
      expect(find.text('5'), findsOneWidget);
      expect(find.text('published'), findsOneWidget);
    });

    testWidgets('says when a published form has heard nothing', (tester) async {
      // The usual cause is the snippet having been taken off the site it was
      // pasted onto, which nothing else here would ever tell you.
      await pump(tester, listApp(isAdmin: true));
      expect(
        find.text('Live but silent. Nothing has been submitted.'),
        findsOneWidget,
      );
    });

    testWidgets('an admin gets the create button', (tester) async {
      await pump(tester, listApp(isAdmin: true));
      expect(find.byTooltip('New web form'), findsOneWidget);
    });

    testWidgets('a member reads the list and cannot create', (tester) async {
      // Read is open to any member; creating a form mints an anonymous
      // endpoint that writes leads into the org, so it is admin-only. Hiding
      // the button is a courtesy, not the boundary.
      await pump(tester, listApp(isAdmin: false));
      expect(find.text('Contact us'), findsOneWidget);
      expect(find.byTooltip('New web form'), findsNothing);
    });

    testWidgets('the empty state tells a member who can fix it', (
      tester,
    ) async {
      await pump(tester, listApp(isAdmin: false, empty: true));
      expect(
        find.textContaining('An administrator can create one'),
        findsOneWidget,
      );
    });
  });

  group('the editor at 390px', () {
    testWidgets('renders without overflowing', (tester) async {
      await pump(tester, detailApp(isAdmin: true));
      expect(tester.takeException(), isNull);
    });

    testWidgets('fits with the system font scaled up', (tester) async {
      await pump(tester, detailApp(isAdmin: true), textScale: 1.5);
      expect(tester.takeException(), isNull);
    });

    testWidgets('fits at a tablet width', (tester) async {
      await pump(tester, detailApp(isAdmin: true), size: tablet);
      expect(tester.takeException(), isNull);
    });

    testWidgets('shows all five sections', (tester) async {
      // A ListView builds only what is near the viewport, so on a 390x844
      // phone a section below the fold is not merely off-screen, it is absent
      // from the tree. Each one is scrolled to rather than asserted blind,
      // which is also the check that they are all reachable by thumb.
      await pump(tester, detailApp(isAdmin: true));

      final seen = <String>[];
      for (final section in [
        'FIELDS',
        'BEHAVIOUR',
        'SPAM',
        'EMBED',
        'ACTIVITY',
      ]) {
        for (var attempt = 0; attempt < 12; attempt++) {
          if (find.text(section).evaluate().isNotEmpty) {
            seen.add(section);
            break;
          }
          await tester.drag(find.byType(ListView).first, const Offset(0, -400));
          await tester.pumpAndSettle();
        }
      }

      expect(seen, ['FIELDS', 'BEHAVIOUR', 'SPAM', 'EMBED', 'ACTIVITY']);
    });

    testWidgets('the reorder buttons are at least 44px', (tester) async {
      // The pointer here is a fingertip and an icon-only button has nothing to
      // pad around, so the minimum is explicit rather than incidental.
      await pump(tester, detailApp(isAdmin: true));
      final up = find.byTooltip('Move Email address up');
      expect(up, findsOneWidget);
      final size = tester.getSize(up);
      expect(size.width, greaterThanOrEqualTo(44));
      expect(size.height, greaterThanOrEqualTo(44));
    });

    testWidgets('the first row cannot move up and the last cannot move down', (
      tester,
    ) async {
      await pump(tester, detailApp(isAdmin: true));
      // `find.ancestor`, not `find.descendant`: an IconButton renders its
      // Tooltip inside itself, so the button is above the tooltip in the tree.
      IconButton buttonFor(String tooltip) => tester.widget<IconButton>(
        find
            .ancestor(
              of: find.byTooltip(tooltip),
              matching: find.byType(IconButton),
            )
            .first,
      );

      final firstUp = buttonFor('Move Email address up');
      final lastDown = buttonFor('Move Telephone down');
      expect(firstUp.onPressed, isNull);
      expect(lastDown.onPressed, isNull);
    });

    testWidgets('the down button moves a row exactly one place', (
      tester,
    ) async {
      // The off-by-one this pins is real: the deprecated `onReorder` took an
      // insertion point rather than a destination, so "down one" through that
      // API is index + 2. Wired to `onReorderItem`, the buttons and the drag
      // pass the same numbers.
      await pump(tester, detailApp(isAdmin: true));

      List<String> labels() => tester
          .widgetList<Text>(
            find.descendant(
              of: find.byType(ReorderableListView),
              matching: find.byType(Text),
            ),
          )
          .map((t) => t.data ?? '')
          .where(
            (s) => ['Email address', 'Given name', 'Telephone'].contains(s),
          )
          .toList();

      expect(labels(), ['Email address', 'Given name', 'Telephone']);
      await tester.tap(find.byTooltip('Move Email address down'));
      await tester.pumpAndSettle();
      expect(labels(), ['Given name', 'Email address', 'Telephone']);
    });

    testWidgets('a member sees the form and none of the controls', (
      tester,
    ) async {
      await pump(tester, detailApp(isAdmin: false));
      expect(find.text('Email address'), findsWidgets);
      expect(find.text('Save changes'), findsNothing);
      expect(find.text('Add a field'), findsNothing);
      expect(find.text('Publish'), findsNothing);
      expect(find.byTooltip('Move Email address down'), findsNothing);
      expect(find.byTooltip('Delete form'), findsNothing);
    });
  });

  group('publishing', () {
    testWidgets('is refused, with the reason, when there is no email field', (
      tester,
    ) async {
      // The same check `WebFormPublishView` runs. Said before the round trip
      // so the button is not a coin flip, and worded like the server's own 400
      // so the two never read as different rules.
      await pump(
        tester,
        detailApp(
          isAdmin: true,
          form: detailForm(
            fields: const [
              {
                'source': 'lead',
                'lead_field': 'phone',
                'label': 'Telephone',
                'order': 0,
              },
            ],
          ),
        ),
      );

      expect(
        find.textContaining('Add an email field before publishing'),
        findsOneWidget,
      );
      final publish = tester.widget<OutlinedButton>(
        find.widgetWithText(OutlinedButton, 'Publish'),
      );
      expect(publish.onPressed, isNull);
    });

    testWidgets('is offered when the form has an email field', (tester) async {
      await pump(tester, detailApp(isAdmin: true));
      final publish = tester.widget<OutlinedButton>(
        find.widgetWithText(OutlinedButton, 'Publish'),
      );
      expect(publish.onPressed, isNotNull);
    });

    testWidgets('a published form offers unpublish instead', (tester) async {
      await pump(
        tester,
        detailApp(isAdmin: true, form: detailForm(published: true)),
      );
      expect(find.widgetWithText(OutlinedButton, 'Unpublish'), findsOneWidget);
      expect(find.widgetWithText(OutlinedButton, 'Publish'), findsNothing);
    });
  });

  group('the captcha secret', () {
    Future<void> openSpam(WidgetTester tester, WebForm form) async {
      await pump(tester, detailApp(isAdmin: true, form: form));
      await tester.drag(find.byType(ListView).first, const Offset(0, -1400));
      await tester.pumpAndSettle();
    }

    testWidgets('says so when none is stored', (tester) async {
      // Turnstile fails closed, so a provider set with no secret refuses every
      // submission. Nothing else on the screen would show that.
      await openSpam(
        tester,
        detailForm(captcha: 'turnstile', hasSecret: false),
      );
      expect(find.textContaining('No secret stored yet'), findsOneWidget);
    });

    testWidgets('says the empty box means unchanged when one is stored', (
      tester,
    ) async {
      // The value is never returned, so the box is always empty. Without this
      // an admin cannot tell "none stored" from "stored, hidden", and saving
      // an unrelated setting looks like it might wipe it.
      await openSpam(tester, detailForm(captcha: 'turnstile', hasSecret: true));
      expect(find.text('Stored. Leave blank to keep it'), findsOneWidget);
      expect(find.textContaining('No secret stored yet'), findsNothing);
    });

    testWidgets('is not asked for at all without a challenge', (tester) async {
      await openSpam(tester, detailForm());
      expect(find.text('Turnstile secret'), findsNothing);
    });
  });

  group('a deactivated assignee', () {
    // The people picker lists active members only, so the stored assignee has
    // no item of its own once deactivated. A dropdown whose value matches no
    // item asserts, and the web equivalent silently clears the field on save.
    const gone = {
      'id': 'gone',
      'email': 'left@example.com',
      'name': 'Left',
      'is_active': false,
    };

    Future<void> openBehaviour(WidgetTester tester, Widget app) async {
      await pump(tester, app);
      for (var attempt = 0; attempt < 12; attempt++) {
        if (find.text('Assign new leads to').evaluate().isNotEmpty) break;
        await tester.drag(find.byType(ListView).first, const Offset(0, -300));
        await tester.pumpAndSettle();
      }
    }

    testWidgets('is offered, labelled, with a note', (tester) async {
      await openBehaviour(
        tester,
        detailApp(isAdmin: true, form: detailForm(assignee: gone)),
      );

      expect(tester.takeException(), isNull);
      expect(find.text('Left (deactivated)'), findsOneWidget);
      expect(
        find.textContaining('Deactivated users are not assigned'),
        findsOneWidget,
      );
    });

    testWidgets('survives a save unchanged', (tester) async {
      _RecordingWebForms.payloads.clear();
      await pump(
        tester,
        detailApp(
          isAdmin: true,
          form: detailForm(assignee: gone),
          notifier: _RecordingWebForms.new,
        ),
      );

      await tester.tap(find.text('Save changes'));
      await tester.pumpAndSettle();

      expect(_RecordingWebForms.payloads, hasLength(1));
      expect(_RecordingWebForms.payloads.single['assign_to'], 'gone');
    });

    testWidgets('an active assignee gets no note', (tester) async {
      await openBehaviour(
        tester,
        detailApp(
          isAdmin: true,
          form: detailForm(
            assignee: const {
              'id': 'p1',
              'email': 'ada@example.com',
              'name': 'Ada',
              'is_active': true,
            },
          ),
        ),
      );

      expect(tester.takeException(), isNull);
      expect(find.text('Ada'), findsWidgets);
      expect(
        find.textContaining('Deactivated users are not assigned'),
        findsNothing,
      );
    });
  });

  group('the embed snippets', () {
    Future<void> openEmbed(WidgetTester tester, WebForm form) async {
      await pump(tester, detailApp(isAdmin: true, form: form));
      await tester.drag(find.byType(ListView).first, const Offset(0, -2600));
      await tester.pumpAndSettle();
    }

    testWidgets('warn that the script one needs an origin listed', (
      tester,
    ) async {
      await openEmbed(tester, detailForm());
      expect(find.textContaining('This one will not work yet'), findsOneWidget);
    });

    testWidgets('drop the warning once an origin is listed', (tester) async {
      await openEmbed(
        tester,
        detailForm(origins: const ['https://example.com']),
      );
      expect(find.textContaining('This one will not work yet'), findsNothing);
    });

    testWidgets('a long snippet URL does not widen the screen', (tester) async {
      // It scrolls inside its own box. Without that a long absolute URL widens
      // the page and every section beside it inherits a sideways swipe.
      await openEmbed(tester, detailForm());
      expect(tester.takeException(), isNull);
    });
  });

  group('choosing the target when creating', () {
    Widget createApp() => ProviderScope(
      overrides: [
        webFormsProvider.overrideWith(_CreatingWebForms.new),
        isOrgAdminProvider.overrideWithValue(true),
      ],
      child: MaterialApp.router(
        routerConfig: GoRouter(
          initialLocation: '/here',
          routes: [
            GoRoute(path: '/here', builder: (_, _) => const WebFormsScreen()),
            GoRoute(
              path: AppRoutes.settingsWebFormDetail,
              builder: (_, s) => Text('editor ${s.pathParameters['formId']}'),
            ),
          ],
        ),
      ),
    );

    for (final scale in [1.0, 1.3]) {
      testWidgets('the dialog fits at 390px and ${scale}x text', (
        tester,
      ) async {
        await pump(tester, createApp(), textScale: scale);
        await tester.tap(find.byTooltip('New web form'));
        await tester.pumpAndSettle();
        expect(tester.takeException(), isNull);
        expect(find.text('Each submission creates'), findsOneWidget);
        expect(find.text('A lead'), findsOneWidget);
        expect(find.text('A ticket'), findsOneWidget);
      });
    }

    testWidgets('a ticket form is created as a ticket form and opened', (
      tester,
    ) async {
      _CreatingWebForms.created.clear();
      await pump(tester, createApp(), textScale: 1.3);
      await tester.tap(find.byTooltip('New web form'));
      await tester.pumpAndSettle();
      await tester.enterText(find.byType(TextField), 'Support');
      await tester.tap(find.text('A ticket'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Create and add fields'));
      await tester.pumpAndSettle();

      expect(_CreatingWebForms.created, [
        (name: 'Support', target: WebForm.targetTicket),
      ]);
      // The real id, not a literal "$id": the route helper used to escape
      // the interpolation, so every new form opened an editor for "$id".
      expect(find.text('editor new-form-id'), findsOneWidget);
    });

    testWidgets('a lead form stays the default', (tester) async {
      _CreatingWebForms.created.clear();
      await pump(tester, createApp());
      await tester.tap(find.byTooltip('New web form'));
      await tester.pumpAndSettle();
      await tester.enterText(find.byType(TextField), 'Contact');
      await tester.tap(find.text('Create and add fields'));
      await tester.pumpAndSettle();
      expect(_CreatingWebForms.created, [
        (name: 'Contact', target: WebForm.targetLead),
      ]);
    });
  });

  group('the route helper', () {
    test('interpolates the form id', () {
      expect(
        AppRoutes.settingsWebForm('abc-123'),
        '/more/settings/web-forms/abc-123',
      );
    });
  });

  group('a ticket form in the editor', () {
    WebForm ticketForm({List<Map<String, dynamic>>? fields}) =>
        WebForm.fromJson({
          'id': 'f1',
          'name': 'Support',
          'target': 'ticket',
          'is_published': false,
          'ticket_priority': 'High',
          'ticket_type': 'Incident',
          'success_mode': 'message',
          'fields':
              fields ??
              const [
                {
                  'source': 'ticket',
                  'ticket_field': 'email',
                  'label': 'Your email',
                  'order': 0,
                },
                {
                  'source': 'ticket',
                  'ticket_field': 'name',
                  'label': 'What went wrong',
                  'order': 1,
                },
              ],
        });

    Widget ticketApp({WebForm? form}) => ProviderScope(
      overrides: [
        webFormsProvider.overrideWith(_FakeWebForms.new),
        webFormDetailProvider('f1').overrideWith(
          (ref) async => WebFormDetail(
            form: form ?? ticketForm(),
            submissions: [
              WebFormSubmission.fromJson(const {
                'id': 's1',
                'status': 'accepted',
                'lead': null,
                'case': 'c1',
                'case_name': 'Printer on fire',
                'created_at': '2026-09-26T09:00:00Z',
              }),
            ],
            submissionCount: 1,
          ),
        ),
        isOrgAdminProvider.overrideWithValue(true),
        usersProvider.overrideWithValue(const []),
        tagsProvider.overrideWithValue(const []),
        customFieldsProvider.overrideWith(_FakeLeadCustomFields.new),
      ],
      child: MaterialApp.router(
        routerConfig: GoRouter(
          initialLocation: '/f',
          routes: [
            GoRoute(
              path: '/f',
              builder: (_, _) => const WebFormDetailScreen(formId: 'f1'),
            ),
            GoRoute(
              path: '/tickets/:id',
              builder: (_, s) => Text('ticket ${s.pathParameters['id']}'),
            ),
          ],
        ),
      ),
    );

    for (final scale in [1.0, 1.3]) {
      testWidgets('renders at 390px and ${scale}x text', (tester) async {
        await pump(tester, ticketApp(), textScale: scale);
        expect(tester.takeException(), isNull);
        await tester.scrollUntilVisible(
          find.text('Ticket type'),
          200,
          scrollable: find.byType(Scrollable).first,
        );
        expect(tester.takeException(), isNull);
      });
    }

    testWidgets('speaks of tickets and offers the ticket defaults', (
      tester,
    ) async {
      await pump(tester, ticketApp());
      expect(find.textContaining('Creates tickets'), findsOneWidget);
      expect(find.text('Subject'), findsOneWidget);
      await tester.scrollUntilVisible(
        find.text('Ticket type'),
        200,
        scrollable: find.byType(Scrollable).first,
      );
      expect(find.text('Ticket priority'), findsOneWidget);
      expect(find.text('Assign new tickets to'), findsOneWidget);
      expect(find.text('Record the source as'), findsNothing);
    });

    testWidgets('the field sheet offers ticket fields and Case custom fields', (
      tester,
    ) async {
      await pump(tester, ticketApp(), textScale: 1.3);
      await tester.tap(find.text('Add a field'));
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
      expect(find.text('Ticket field'), findsOneWidget);
      expect(find.text('Lead field'), findsNothing);

      await tester.tap(find.text('Writes into'));
      await tester.pumpAndSettle();
      expect(find.text('Subject (already on this form)'), findsWidgets);
      expect(find.text('Message'), findsWidgets);
      expect(find.text('Salutation'), findsNothing);
      await tester.tap(find.text('Message').last);
      await tester.pumpAndSettle();

      await tester.tap(find.text('Custom field'));
      await tester.pumpAndSettle();
      await tester.tap(find.byType(DropdownButtonFormField<String>).first);
      await tester.pumpAndSettle();
      // The Case definition only: Lead's belong to lead forms.
      expect(find.text('Severity'), findsWidgets);
      expect(find.text('Budget'), findsNothing);
    });

    testWidgets('blocks publishing without a ticket email field', (
      tester,
    ) async {
      await pump(
        tester,
        ticketApp(
          form: ticketForm(
            fields: const [
              {
                'source': 'ticket',
                'ticket_field': 'name',
                'label': 'What went wrong',
                'order': 0,
              },
            ],
          ),
        ),
      );
      expect(
        find.textContaining('how each ticket finds its contact'),
        findsOneWidget,
      );
    });

    testWidgets('a submission opens the ticket it created', (tester) async {
      await pump(tester, ticketApp());
      await tester.scrollUntilVisible(
        find.text('Printer on fire'),
        300,
        scrollable: find.byType(Scrollable).first,
      );
      expect(find.text('Ticket'), findsOneWidget);
      await tester.tap(find.text('Printer on fire'));
      await tester.pumpAndSettle();
      expect(find.text('ticket c1'), findsOneWidget);
    });
  });
}

/// Records each create instead of sending it.
class _CreatingWebForms extends _FakeWebForms {
  static final created = <({String name, String target})>[];

  @override
  Future<String> createWebForm(
    String name, {
    String target = WebForm.targetLead,
  }) async {
    created.add((name: name, target: target));
    return 'new-form-id';
  }
}

class _FakeWebForms extends WebFormsNotifier {
  @override
  Future<WebFormsState> build() async => WebFormsState(
    forms: [
      WebForm.fromJson(const {
        'id': 'f1',
        'name': 'Contact us',
        'is_published': true,
        'submission_count': 0,
        'field_count': 3,
      }),
      WebForm.fromJson(const {
        'id': 'f2',
        'name': 'Newsletter signup',
        'is_published': false,
        'submission_count': 12,
        'field_count': 1,
      }),
    ],
    // Deliberately larger than the two rows above: the list is paginated and
    // the totals are computed server-side over every form.
    totals: const WebFormTotals(
      count: 13,
      published: 5,
      submissions30d: 40,
      spam30d: 7,
    ),
    truncated: true,
  );
}

/// Records each update payload instead of sending it.
class _RecordingWebForms extends _FakeWebForms {
  static final payloads = <Map<String, dynamic>>[];

  @override
  Future<String?> updateWebForm(String id, Map<String, dynamic> payload) async {
    payloads.add(payload);
    return null;
  }
}

class _FakeNoWebForms extends WebFormsNotifier {
  @override
  Future<WebFormsState> build() async => const WebFormsState();
}

class _FakeLeadCustomFields extends CustomFieldsNotifier {
  @override
  Future<CustomFieldsState> build() async => CustomFieldsState(
    fields: [
      CustomFieldDefinition.fromJson(const {
        'id': 'cf1',
        'target_model': 'Lead',
        'key': 'budget',
        'label': 'Budget',
        'field_type': 'number',
        'is_active': true,
      }),
      // Another model's definition, and a turned-off one. Neither belongs in
      // the picker: a form pointing at either writes into a column nothing
      // reads.
      CustomFieldDefinition.fromJson(const {
        'id': 'cf2',
        'target_model': 'Case',
        'key': 'severity',
        'label': 'Severity',
        'field_type': 'text',
        'is_active': true,
      }),
      CustomFieldDefinition.fromJson(const {
        'id': 'cf3',
        'target_model': 'Lead',
        'key': 'retired',
        'label': 'Retired field',
        'field_type': 'text',
        'is_active': false,
      }),
    ],
    count: 3,
    active: 2,
  );
}
