import 'package:bottle_crm/core/theme/theme.dart';
import 'package:bottle_crm/data/models/org_app.dart';
import 'package:bottle_crm/screens/settings/app_preview_sheet.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import '../../support/apps_fixtures.dart';

void main() {
  testWidgets('objects are listed with their effects', (tester) async {
    tester.view.devicePixelRatio = 3.0;
    tester.view.physicalSize = const Size(390 * 3, 844 * 3);
    addTearDown(tester.view.reset);
    final app = appsAnswer().apps.first;
    final preview = AppPreview.fromJson({
      'action': 'install',
      'items': [
        {'type': 'object', 'label': 'Property', 'effect': 'add'},
        {'type': 'object', 'label': 'Requirement', 'effect': 'conflict'},
        {'type': 'object', 'label': 'Site visit', 'effect': 'restore'},
      ],
    }, AppAction.install);
    await tester.pumpWidget(
      MaterialApp(
        theme: AppTheme.light,
        home: Builder(
          builder: (context) => TextButton(
            onPressed: () =>
                showAppPreviewSheet(context, app: app, preview: preview),
            child: const Text('open'),
          ),
        ),
      ),
    );
    await tester.tap(find.text('open'));
    await tester.pumpAndSettle();

    expect(find.text('OBJECTS'), findsOneWidget);
    expect(find.text('Property'), findsOneWidget);
    expect(find.text('New'), findsOneWidget);
    expect(find.text('Comes back'), findsOneWidget);
    expect(find.text('Not added'), findsOneWidget);
    expect(find.text("None of the app's objects is added"), findsOneWidget);
  });

  testWidgets('an item with an empty target draws no caption', (tester) async {
    tester.view.devicePixelRatio = 3.0;
    tester.view.physicalSize = const Size(390 * 3, 844 * 3);
    addTearDown(tester.view.reset);
    final app = appsAnswer().apps.first;
    final preview = AppPreview.fromJson({
      'action': 'install',
      'items': [
        {'type': 'object', 'target': '', 'label': 'Property', 'effect': 'add'},
        {'type': 'field', 'target': '', 'label': 'Budget', 'effect': 'add'},
        {
          'type': 'pipeline',
          'target': 'lead',
          'label': 'Sales',
          'effect': 'add',
        },
      ],
    }, AppAction.install);
    await tester.pumpWidget(
      MaterialApp(
        theme: AppTheme.light,
        home: Builder(
          builder: (context) => TextButton(
            onPressed: () =>
                showAppPreviewSheet(context, app: app, preview: preview),
            child: const Text('open'),
          ),
        ),
      ),
    );
    await tester.tap(find.text('open'));
    await tester.pumpAndSettle();

    expect(find.text('Budget'), findsOneWidget);
    expect(find.text(appTargetLabel('lead')), findsOneWidget);
    expect(find.text(appTargetLabel('')), findsNothing);
  });
}
