import 'package:bottle_crm/core/theme/theme.dart';
import 'package:bottle_crm/data/models/ticket.dart';
import 'package:bottle_crm/widgets/cards/ticket_card.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

/// D53: TicketCard must lay out without overflow on a phone and a tablet, at
/// default and enlarged system text, whatever optional chips a ticket carries.
///
/// Flutter reports a RenderFlex overflow as a thrown FlutterError, so
/// `tester.takeException()` returning null is the assertion. The card is
/// pumped inside the same 12px-padded ListView the tickets list uses, so it
/// gets the width it gets in the app.
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  void useView(WidgetTester tester, double width, double textScale) {
    tester.view.devicePixelRatio = 3.0;
    tester.view.physicalSize = Size(width * 3, 1200 * 3);
    tester.platformDispatcher.textScaleFactorTestValue = textScale;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    addTearDown(tester.platformDispatcher.clearTextScaleFactorTestValue);
  }

  Widget host(Ticket ticket) => MaterialApp(
    theme: AppTheme.light,
    home: Scaffold(
      body: ListView(
        padding: const EdgeInsets.fromLTRB(12, 0, 12, 80),
        children: [TicketCard(ticketItem: ticket)],
      ),
    ),
  );

  final twoMonthsAgo = DateTime.now()
      .subtract(const Duration(days: 65))
      .toUtc()
      .toIso8601String();

  Map<String, dynamic> base() => {
    'id': 't1',
    'name': 'Printer jam',
    'status': 'New',
    'priority': 'Normal',
    'case_type': 'Question',
    'created_at': twoMonthsAgo,
  };

  const readableParent = {
    'id': 'p-open',
    'name': 'Outage umbrella',
    'status': 'Pending',
    'restricted': false,
  };
  const hiddenParent = {
    'id': 'p-hidden',
    'name': null,
    'status': null,
    'restricted': true,
  };

  /// Every optional piece of the card present, each at its widest.
  Map<String, dynamic> loaded({
    Map<String, dynamic>? parent,
    bool breached = true,
  }) => {
    ...base(),
    'name':
        'Every printer on the third floor stopped printing at once and the '
        'queue shows jobs from last Tuesday that nobody can cancel',
    'status': 'Duplicate',
    'priority': 'Urgent',
    'case_type': 'Incident',
    'account': {
      'id': 'a1',
      'name':
          'Consolidated International Holdings and Logistics Partners '
          'Incorporated (Northern Region)',
    },
    'is_problem': true,
    'child_count': 12,
    if (parent != null) 'parent': parent['id'],
    'parent_summary': ?parent,
    'tags': [
      'AnExtraordinarilyLongTagNameWithNoSpacesThatCannotWrapAnywhere',
      'hardware-procurement-escalation',
      'third',
      'fourth',
    ],
    'assigned_to': [
      {'id': 'u1', 'email': 'maximilian.featherstonehaugh@example.com'},
      {'id': 'u2', 'email': 'b@example.com'},
      {'id': 'u3', 'email': 'c@example.com'},
      {'id': 'u4', 'email': 'd@example.com'},
    ],
    if (breached) 'is_sla_resolution_breached': true,
    if (!breached) 'is_sla_resolution_at_risk': true,
  };

  final variants = <String, Map<String, dynamic>>{
    'minimal': base(),
    'readable parent': {
      ...base(),
      'parent': readableParent['id'],
      'parent_summary': readableParent,
    },
    'restricted parent': {
      ...base(),
      'parent': hiddenParent['id'],
      'parent_summary': hiddenParent,
    },
    'all chips, readable parent, SLA breached': loaded(parent: readableParent),
    'all chips, restricted parent, SLA due soon': loaded(
      parent: hiddenParent,
      breached: false,
    ),
    'all chips, no parent': loaded(),
  };

  // Reproduces D53 as reported: 390px, 1.3x, with and without a parent.
  group('D53 reproduction at 390px, 1.3x', () {
    for (final entry in [
      MapEntry('no parent', base()),
      MapEntry('restricted parent', variants['restricted parent']!),
    ]) {
      testWidgets(entry.key, (tester) async {
        useView(tester, 390, 1.3);
        await tester.pumpWidget(host(Ticket.fromJson(entry.value)));
        expect(tester.takeException(), isNull);
      });
    }
  });

  for (final width in [390.0, 800.0]) {
    for (final scale in [1.0, 1.3, 2.0]) {
      group('TicketCard at ${width.toInt()}px, ${scale}x', () {
        variants.forEach((name, json) {
          testWidgets(name, (tester) async {
            useView(tester, width, scale);
            await tester.pumpWidget(host(Ticket.fromJson(json)));
            expect(tester.takeException(), isNull);
          });
        });
      });
    }
  }

  testWidgets('with room, the footer chips sit on the meta line, right', (
    tester,
  ) async {
    useView(tester, 800, 1.0);
    await tester.pumpWidget(host(Ticket.fromJson(loaded())));
    expect(tester.takeException(), isNull);
    final card = tester.getRect(find.byType(TicketCard));
    final priority = tester.getRect(find.text('Urgent'));
    final chip = tester.getRect(find.text('SLA · Resolve'));
    // Same line as the priority label, and pushed to the card's right edge
    // (12px padding plus the chip's own 6px), not packed after the meta.
    expect(chip.center.dy, closeTo(priority.center.dy, 2));
    expect(chip.right, closeTo(card.right - 12 - 6 - 1, 2));
  });

  testWidgets('every chip and label is still rendered when loaded', (
    tester,
  ) async {
    useView(tester, 390, 2.0);
    await tester.pumpWidget(
      host(Ticket.fromJson(loaded(parent: readableParent))),
    );
    expect(tester.takeException(), isNull);
    for (final label in [
      'Duplicate',
      'Problem',
      'Sub-ticket',
      '12 children',
      '+2',
      '+3',
      'Incident',
      'Urgent',
      '2mo ago',
      'SLA · Resolve',
    ]) {
      expect(find.text(label), findsOneWidget, reason: label);
    }
    expect(find.textContaining('Consolidated International'), findsOneWidget);
    expect(find.textContaining('AnExtraordinarilyLong'), findsOneWidget);
  });
}
