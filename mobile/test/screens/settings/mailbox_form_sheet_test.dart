import 'package:bottle_crm/data/models/lookup_models.dart';
import 'package:bottle_crm/data/models/mailbox.dart';
import 'package:bottle_crm/providers/lookup_provider.dart';
import 'package:bottle_crm/screens/settings/mailbox_form_sheet.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

/// A mailbox whose default assignee was deactivated after being chosen.
///
/// The people picker lists active members only, so the stored assignee needs
/// an item of their own, and the server accepts that unchanged value back
/// (`InboundMailboxSerializer.validate_default_assignee_id`). Dropping it
/// would move the mailbox to unassigned as a side effect of editing anything
/// else on the sheet.
void main() {
  Mailbox mailbox({required bool active}) => Mailbox.fromJson({
    'id': 'm1',
    'address': 'help@acme.com',
    'provider': 'ses',
    'default_priority': 'Normal',
    'default_assignee': {
      'id': 'gone',
      'user_details': {'email': 'left@example.com', 'name': 'Left'},
      'role': 'USER',
      'is_active': active,
    },
  });

  Map<String, dynamic>? result;

  Widget app(Mailbox? existing) => ProviderScope(
    overrides: [
      usersProvider.overrideWithValue(const [
        UserLookup(
          id: 'p1',
          email: 'ada@example.com',
          name: 'Ada',
          role: 'ADMIN',
          isActive: true,
        ),
      ]),
    ],
    child: MaterialApp(
      home: Scaffold(
        body: Builder(
          builder: (context) => ElevatedButton(
            onPressed: () async {
              result = await showMailboxFormSheet(context, existing: existing);
            },
            child: const Text('open'),
          ),
        ),
      ),
    ),
  );

  Future<void> open(
    WidgetTester tester,
    Mailbox? existing, {
    double textScale = 1.0,
  }) async {
    result = null;
    tester.view.devicePixelRatio = 3.0;
    tester.view.physicalSize = const Size(390 * 3, 844 * 3);
    tester.platformDispatcher.textScaleFactorTestValue = textScale;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    addTearDown(tester.platformDispatcher.clearTextScaleFactorTestValue);
    await tester.pumpWidget(app(existing));
    await tester.tap(find.text('open'));
    await tester.pumpAndSettle();
  }

  testWidgets('offers the deactivated assignee, labelled, with a note', (
    tester,
  ) async {
    await open(tester, mailbox(active: false));

    expect(tester.takeException(), isNull);
    expect(find.text('Left (deactivated)'), findsOneWidget);
    expect(
      find.textContaining('Deactivated users are not assigned'),
      findsOneWidget,
    );
  });

  testWidgets('the deactivated assignee survives a save', (tester) async {
    await open(tester, mailbox(active: false));

    await tester.ensureVisible(find.text('Save changes'));
    await tester.tap(find.text('Save changes'));
    await tester.pumpAndSettle();

    expect(result, isNotNull);
    expect(result!['default_assignee_id'], 'gone');
  });

  testWidgets('an active assignee missing from the list is not called '
      'deactivated', (tester) async {
    // What a picker that has not loaded yet looks like: the stored person is
    // absent from the list without having been deactivated.
    await open(tester, mailbox(active: true));

    expect(find.text('Left'), findsOneWidget);
    expect(find.text('Left (deactivated)'), findsNothing);
    expect(
      find.textContaining('Deactivated users are not assigned'),
      findsNothing,
    );
  });

  group('SNS Topic ARN', () {
    const arn = 'arn:aws:sns:us-east-1:123456789012:inbound';
    const other = 'arn:aws:sns:eu-west-1:123456789012:other';
    final topicField = find.byKey(const ValueKey('mailbox-topic-arn'));

    Mailbox pinned(String? topic) => Mailbox.fromJson({
      'id': 'm1',
      'address': 'help@acme.com',
      'provider': 'ses',
      'default_priority': 'Normal',
      'has_topic_arn': topic != null,
      'topic_arn': topic ?? '',
    });

    Future<void> save(WidgetTester tester, String label) async {
      await tester.ensureVisible(find.text(label));
      await tester.tap(find.text(label));
      await tester.pumpAndSettle();
    }

    testWidgets('is prefilled and left out of a save that did not touch it', (
      tester,
    ) async {
      await open(tester, pinned(arn));
      expect(find.widgetWithText(TextField, arn), findsOneWidget);

      await save(tester, 'Save changes');

      expect(result, isNotNull);
      expect(result!.containsKey('topic_arn'), isFalse);
    });

    testWidgets('sends a changed ARN', (tester) async {
      await open(tester, pinned(arn));
      await tester.enterText(topicField, ' $other ');
      await save(tester, 'Save changes');

      expect(result!['topic_arn'], other);
    });

    testWidgets('sends the empty string when cleared', (tester) async {
      await open(tester, pinned(arn));
      await tester.enterText(topicField, '');
      await save(tester, 'Save changes');

      expect(result!['topic_arn'], '');
    });

    testWidgets('a new address sends one only when entered', (tester) async {
      await open(tester, null);
      await tester.enterText(
        find.widgetWithText(TextField, 'Address'),
        'a@b.io',
      );
      await save(tester, 'Add address');
      expect(result!.containsKey('topic_arn'), isFalse);

      await tester.tap(find.text('open'));
      await tester.pumpAndSettle();
      await tester.enterText(
        find.widgetWithText(TextField, 'Address'),
        'a@b.io',
      );
      await tester.enterText(topicField, arn);
      await save(tester, 'Add address');
      expect(result!['topic_arn'], arn);
    });

    testWidgets('holds up at 390px and 1.3x text', (tester) async {
      await open(tester, pinned(arn), textScale: 1.3);

      expect(tester.takeException(), isNull);
      await tester.ensureVisible(topicField);
      await tester.pumpAndSettle();
      expect(tester.getSize(topicField).height, greaterThanOrEqualTo(44));
      expect(tester.getRect(topicField).right, lessThanOrEqualTo(390));
      expect(
        find.textContaining('Mail is accepted only from this exact topic'),
        findsOneWidget,
      );
      await save(tester, 'Save changes');
      expect(tester.takeException(), isNull);
      expect(result, isNotNull);
    });
  });
}
