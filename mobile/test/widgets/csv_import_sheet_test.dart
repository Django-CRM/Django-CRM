import 'dart:convert';
import 'dart:typed_data';

import 'package:bottle_crm/providers/csv_import_provider.dart';
import 'package:bottle_crm/services/api_service.dart';
import 'package:bottle_crm/widgets/forms/csv_import_sheet.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import '../helpers/fake_platform_file.dart';

class _QueueClient extends http.BaseClient {
  final List<(int, String)> replies = [];
  int sent = 0;

  @override
  Future<http.StreamedResponse> send(http.BaseRequest request) async {
    sent++;
    await request.finalize().toBytes();
    final (status, body) = replies.removeAt(0);
    return http.StreamedResponse(
      Stream.value(utf8.encode(body)),
      status,
      request: request,
    );
  }
}

String _previewBody({int valid = 2, int errorCount = 0}) => jsonEncode({
  'header_error': null,
  'valid': [
    for (var i = 1; i <= valid; i++)
      {
        'row': i,
        'first_name': 'Person',
        'last_name': '$i',
        'email': 'person$i@acme.test',
        'phone': '',
        'organization': 'Acme Corp',
      },
  ],
  'errors': [
    for (var i = 0; i < errorCount; i++)
      {
        'row': i + 3,
        'field': 'email',
        'message':
            'Duplicate email also used by row 1 in this file, so this row '
            'cannot be imported until one of the two is changed',
      },
  ],
  'summary': {
    'total': valid + errorCount,
    'valid': valid,
    'invalid': errorCount,
  },
});

void main() {
  late _QueueClient client;
  late int refreshed;

  setUp(() {
    client = _QueueClient();
    ApiService().setClientForTesting(client);
    refreshed = 0;
  });

  /// A 390px phone, as the other layout tests use, at [textScale].
  void usePhone(WidgetTester tester, {double textScale = 1.0}) {
    tester.view.devicePixelRatio = 3.0;
    tester.view.physicalSize = const Size(390 * 3, 844 * 3);
    tester.platformDispatcher.textScaleFactorTestValue = textScale;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    addTearDown(tester.platformDispatcher.clearTextScaleFactorTestValue);
  }

  Future<void> open(
    WidgetTester tester, {
    CsvImportTarget target = CsvImportTarget.contacts,
    double textScale = 1.0,
    Future<String?> Function(String fileName, List<int> bytes)? saveFile,
  }) async {
    usePhone(tester, textScale: textScale);
    final bytes = Uint8List.fromList(utf8.encode('first_name,last_name\n'));
    await tester.pumpWidget(
      ProviderScope(
        child: MaterialApp(
          home: Scaffold(
            body: Builder(
              builder: (context) => Center(
                child: ElevatedButton(
                  onPressed: () => showCsvImportSheet(
                    context,
                    target,
                    onImported: () => refreshed++,
                    pickFile: () async => FakePlatformFile(
                      name: 'people.csv',
                      size: bytes.length,
                      bytes: bytes,
                    ),
                    saveFile: saveFile,
                  ),
                  child: const Text('open'),
                ),
              ),
            ),
          ),
        ),
      ),
    );
    await tester.tap(find.text('open'));
    await tester.pumpAndSettle();
  }

  FilledButton importButton(WidgetTester tester) => tester.widget<FilledButton>(
    find.ancestor(
      of: find.textContaining(RegExp(r'^Import \d')),
      matching: find.byWidgetPredicate((w) => w is FilledButton),
    ),
  );

  for (final scale in [1.0, 1.3]) {
    testWidgets('the empty sheet fits a phone at ${scale}x text', (
      tester,
    ) async {
      for (final target in CsvImportTarget.values) {
        await open(tester, target: target, textScale: scale);

        expect(tester.takeException(), isNull);
        expect(find.text('Import ${target.plural} from CSV'), findsOneWidget);
        expect(find.text('CSV format'), findsOneWidget);
        expect(find.text('Save CSV template'), findsOneWidget);
        expect(find.text('Choose CSV file'), findsOneWidget);
        // The primary action is a full 44px target.
        expect(
          tester.getSize(find.byType(FilledButton)).height,
          greaterThanOrEqualTo(44),
        );

        await tester.tap(find.byTooltip('Close'));
        await tester.pumpAndSettle();
      }
    });

    testWidgets('row errors list and block the import at ${scale}x text', (
      tester,
    ) async {
      client.replies.add((200, _previewBody(valid: 2, errorCount: 30)));
      await open(tester, textScale: scale);

      await tester.tap(find.text('Choose CSV file'));
      await tester.pumpAndSettle();

      expect(tester.takeException(), isNull);
      expect(find.text('Valid: 2'), findsOneWidget);
      expect(find.text('Invalid: 30'), findsOneWidget);
      expect(
        find.text('30 errors, fix the CSV before importing'),
        findsOneWidget,
      );
      expect(find.textContaining('Row 3'), findsOneWidget);
      expect(importButton(tester).onPressed, isNull);
      // The long list scrolls inside its own box rather than pushing the
      // buttons off the sheet.
      expect(find.text('Back'), findsOneWidget);
      expect(tester.getBottomLeft(find.text('Back')).dy, lessThan(844));
    });

    testWidgets('the valid rows are sampled, as on the web, at ${scale}x', (
      tester,
    ) async {
      client.replies.add((200, _previewBody(valid: 25, errorCount: 1)));
      await open(tester, textScale: scale);

      await tester.tap(find.text('Choose CSV file'));
      await tester.pumpAndSettle();

      expect(tester.takeException(), isNull);
      expect(find.textContaining('Person 1'), findsWidgets);
      expect(
        find.textContaining('person1@acme.test \u00b7 Acme Corp'),
        findsOneWidget,
      );
      expect(find.text('Showing first 20 of 25 valid rows'), findsOneWidget);
      // The save button is a full-height target.
      expect(
        tester
            .getSize(
              find.ancestor(
                of: find.text('Save errors as CSV'),
                matching: find.byWidgetPredicate((w) => w is TextButton),
              ),
            )
            .height,
        greaterThanOrEqualTo(44),
      );
    });
  }

  testWidgets('the row errors save as the web drawer\'s errors file', (
    tester,
  ) async {
    final saved = <(String, String)>[];
    client.replies.add((200, _previewBody(valid: 1, errorCount: 2)));
    await open(
      tester,
      target: CsvImportTarget.leads,
      saveFile: (name, bytes) async {
        saved.add((name, utf8.decode(bytes)));
        return '/tmp/$name';
      },
    );

    await tester.tap(find.text('Choose CSV file'));
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('Save errors as CSV'));
    await tester.tap(find.text('Save errors as CSV'));
    await tester.pumpAndSettle();

    expect(saved, hasLength(1));
    expect(saved.single.$1, 'leads-import-errors.csv');
    final lines = saved.single.$2.split('\n');
    expect(lines.first, 'row,field,message');
    expect(lines, hasLength(3));
    // The message holds a comma, so it is one quoted cell.
    expect(lines[1], startsWith('3,email,"Duplicate email also used by row 1'));
    expect(lines[1], endsWith('changed"'));
    expect(find.text('Saved leads-import-errors.csv.'), findsOneWidget);
  });

  testWidgets('a closed save dialog says nothing, a failed one says so', (
    tester,
  ) async {
    var fail = false;
    client.replies.add((200, _previewBody(valid: 1, errorCount: 1)));
    await open(
      tester,
      saveFile: (name, bytes) async {
        if (fail) throw Exception('disk full');
        return null;
      },
    );
    await tester.tap(find.text('Choose CSV file'));
    await tester.pumpAndSettle();

    await tester.ensureVisible(find.text('Save errors as CSV'));
    await tester.tap(find.text('Save errors as CSV'));
    await tester.pumpAndSettle();
    expect(find.textContaining('Saved '), findsNothing);
    expect(find.text('Could not save the errors.'), findsNothing);

    fail = true;
    await tester.tap(find.text('Save errors as CSV'));
    await tester.pumpAndSettle();
    expect(find.text('Could not save the errors.'), findsOneWidget);
  });

  testWidgets('a clean file imports, closes, refreshes and reports the count', (
    tester,
  ) async {
    client.replies
      ..add((200, _previewBody(valid: 2)))
      ..add((200, '{"error": false, "created": 2, "ids": ["a", "b"]}'));
    await open(tester);

    await tester.tap(find.text('Choose CSV file'));
    await tester.pumpAndSettle();
    expect(importButton(tester).onPressed, isNotNull);

    await tester.tap(find.text('Import 2 contacts'));
    await tester.pumpAndSettle();

    expect(client.sent, 2);
    expect(find.text('Import contacts from CSV'), findsNothing);
    expect(find.text('Imported 2 contacts'), findsOneWidget);
    expect(refreshed, 1);
  });

  testWidgets('a 403 shows the access message and leaves the sheet open', (
    tester,
  ) async {
    client.replies.add((
      403,
      '{"error": true, "message": "Permission denied"}',
    ));
    await open(tester, target: CsvImportTarget.leads, textScale: 1.3);

    await tester.tap(find.text('Choose CSV file'));
    await tester.pumpAndSettle();

    expect(tester.takeException(), isNull);
    expect(find.text(csvImportForbiddenMessage), findsOneWidget);
    expect(find.text('Import leads from CSV'), findsOneWidget);
    expect(refreshed, 0);
  });

  testWidgets('closing without importing refreshes nothing', (tester) async {
    await open(tester);

    await tester.tap(find.text('Cancel'));
    await tester.pumpAndSettle();

    expect(find.text('Import contacts from CSV'), findsNothing);
    expect(refreshed, 0);
    expect(client.sent, 0);
  });
}
