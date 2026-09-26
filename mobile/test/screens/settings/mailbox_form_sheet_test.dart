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

  Widget app(Mailbox existing) => ProviderScope(
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

  Future<void> open(WidgetTester tester, Mailbox existing) async {
    result = null;
    tester.view.devicePixelRatio = 3.0;
    tester.view.physicalSize = const Size(390 * 3, 844 * 3);
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
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
}
