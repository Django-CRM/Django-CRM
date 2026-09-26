import 'dart:convert';
import 'dart:typed_data';

import 'package:bottle_crm/providers/csv_import_provider.dart';
import 'package:bottle_crm/services/api_service.dart';
import 'package:file_picker/file_picker.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;

/// Answers each request with the next queued reply and keeps what was sent,
/// so a test can check the wire as well as the state.
class _QueueClient extends http.BaseClient {
  final List<(int, String)> replies = [];
  final List<http.BaseRequest> sent = [];
  final List<List<int>> bodies = [];

  @override
  Future<http.StreamedResponse> send(http.BaseRequest request) async {
    sent.add(request);
    bodies.add(await request.finalize().toBytes());
    final (status, body) = replies.removeAt(0);
    return http.StreamedResponse(
      Stream.value(utf8.encode(body)),
      status,
      request: request,
    );
  }
}

const _csv = 'first_name,last_name\nAda,Lovelace\nAlan,Turing\n';

PlatformFile _file({String name = 'people.csv', String content = _csv}) {
  final bytes = Uint8List.fromList(utf8.encode(content));
  return PlatformFile(name: name, size: bytes.length, bytes: bytes);
}

String _previewBody({
  String? headerError,
  int valid = 2,
  List<Map<String, Object>> errors = const [],
}) => jsonEncode({
  'header_error': headerError,
  'valid': [
    for (var i = 1; i <= valid; i++) {'row': i},
  ],
  'errors': errors,
  'summary': {
    'total': valid + errors.length,
    'valid': valid,
    'invalid': errors.length,
  },
});

void main() {
  late _QueueClient client;
  late ProviderContainer container;

  setUp(() {
    client = _QueueClient();
    ApiService().setClientForTesting(client);
    container = ProviderContainer();
  });

  tearDown(() => container.dispose());

  /// The provider is autoDispose, so a listener stands in for the open sheet.
  CsvImportNotifier notifier([
    CsvImportTarget target = CsvImportTarget.contacts,
  ]) {
    container.listen(csvImportProvider(target), (_, _) {});
    return container.read(csvImportProvider(target).notifier);
  }

  CsvImportState stateOf([CsvImportTarget target = CsvImportTarget.contacts]) =>
      container.read(csvImportProvider(target));

  group('preview', () {
    test('sends the file as multipart `file` and reads the counts', () async {
      client.replies.add((200, _previewBody()));

      await notifier().choose(_file());

      final request = client.sent.single as http.MultipartRequest;
      expect(request.url.path, endsWith('/api/contacts/import/preview/'));
      expect(request.files.single.field, 'file');
      expect(request.files.single.filename, 'people.csv');
      expect(request.headers['Content-Type'], startsWith('multipart/'));
      final state = stateOf();
      expect(state.preview!.valid, 2);
      expect(state.preview!.total, 2);
      expect(state.error, isNull);
      expect(state.canCommit, isTrue);
    });

    test('a 403 says who may import, in the web wording', () async {
      client.replies.add((
        403,
        '{"error": true, "message": "Permission denied"}',
      ));

      await notifier().choose(_file());

      final state = stateOf();
      expect(
        state.error,
        'Importing needs an admin, or a member with sales access.',
      );
      expect(state.error, csvImportForbiddenMessage);
      expect(state.preview, isNull);
      expect(state.canCommit, isFalse);
    });

    test('a header_error blocks the commit and sends nothing', () async {
      client.replies.add((
        200,
        _previewBody(
          headerError: 'Missing required header(s): last_name',
          valid: 0,
        ),
      ));
      final n = notifier();

      await n.choose(_file());

      expect(
        stateOf().preview!.headerError,
        'Missing required header(s): last_name',
      );
      expect(stateOf().canCommit, isFalse);
      expect(await n.commit(), isFalse);
      expect(client.sent, hasLength(1));
    });

    test('row errors are listed and block the commit, which would refuse '
        'the whole file', () async {
      client.replies.add((
        200,
        _previewBody(
          valid: 1,
          errors: [
            {'row': 2, 'field': 'email', 'message': 'Invalid email'},
          ],
        ),
      ));
      final n = notifier();

      await n.choose(_file());

      final errors = stateOf().preview!.errors;
      expect(errors.single.row, 2);
      expect(errors.single.field, 'email');
      expect(errors.single.message, 'Invalid email');
      expect(stateOf().canCommit, isFalse);
      expect(await n.commit(), isFalse);
      expect(client.sent, hasLength(1));
    });

    test('a file with no valid rows cannot be committed', () async {
      client.replies.add((200, _previewBody(valid: 0)));

      await notifier().choose(_file());

      expect(stateOf().canCommit, isFalse);
    });
  });

  group('commit', () {
    test('re-sends the same bytes and reports what was created', () async {
      client.replies
        ..add((200, _previewBody()))
        ..add((200, '{"error": false, "created": 2, "ids": ["a", "b"]}'));
      final n = notifier();
      await n.choose(_file());

      expect(await n.commit(), isTrue);

      expect(client.sent, hasLength(2));
      expect(client.sent[1].url.path, endsWith('/api/contacts/import/commit/'));
      // Both multipart bodies carry the CSV unchanged.
      for (final body in client.bodies) {
        expect(utf8.decode(body), contains(_csv));
      }
      expect(stateOf().created, 2);
      expect(stateOf().error, isNull);
    });

    test(
      'a 400 keeps the preview and shows the message and the rows',
      () async {
        client.replies
          ..add((200, _previewBody()))
          ..add((
            400,
            jsonEncode({
              'error': true,
              'message': 'Fix the invalid rows before importing',
              'errors': [
                {'row': 1, 'field': 'email', 'message': 'Duplicate email'},
              ],
              'created': 0,
            }),
          ));
        final n = notifier();
        await n.choose(_file());

        expect(await n.commit(), isFalse);

        final state = stateOf();
        expect(state.error, 'Fix the invalid rows before importing');
        expect(state.commitErrors.single.message, 'Duplicate email');
        expect(state.preview, isNotNull);
        expect(state.created, isNull);
      },
    );

    test('a 400 carrying only header_error shows it', () async {
      client.replies
        ..add((200, _previewBody()))
        ..add((
          400,
          '{"error": true, "header_error": "CSV is empty", "created": 0}',
        ));
      final n = notifier();
      await n.choose(_file());

      expect(await n.commit(), isFalse);
      expect(stateOf().error, 'CSV is empty');
    });

    test('a 403 at commit uses the same wording as at preview', () async {
      client.replies
        ..add((200, _previewBody()))
        ..add((403, '{"error": true, "message": "Permission denied"}'));
      final n = notifier();
      await n.choose(_file());

      expect(await n.commit(), isFalse);
      expect(stateOf().error, csvImportForbiddenMessage);
    });
  });

  group('refused before upload', () {
    test('a file not named .csv', () async {
      await notifier().choose(_file(name: 'people.xlsx'));

      expect(stateOf().error, contains('is not a CSV file'));
      expect(stateOf().file, isNull);
      expect(client.sent, isEmpty);
    });

    test('an upper-case .CSV extension is accepted', () async {
      client.replies.add((200, _previewBody()));

      await notifier().choose(_file(name: 'PEOPLE.CSV'));

      expect(stateOf().error, isNull);
      expect(client.sent, hasLength(1));
    });

    test('a file the picker reports as over 5 MB, before reading it', () async {
      final bytes = Uint8List.fromList(utf8.encode(_csv));
      await notifier().choose(
        PlatformFile(name: 'big.csv', size: 6 * 1024 * 1024, bytes: bytes),
      );

      expect(
        stateOf().error,
        'big.csv is 6.0 MB. CSV files must be 5 MB or '
        'smaller.',
      );
      expect(client.sent, isEmpty);
    });

    test('bytes over 5 MB when the picker did not know the size', () async {
      final bytes = Uint8List(csvImportMaxBytes + 1);
      await notifier().choose(
        PlatformFile(name: 'big.csv', size: 0, bytes: bytes),
      );

      expect(stateOf().error, contains('CSV files must be 5 MB or smaller'));
      expect(client.sent, isEmpty);
    });

    test('exactly 5 MB is allowed through', () async {
      client.replies.add((200, _previewBody()));
      final bytes = Uint8List(csvImportMaxBytes);
      await notifier().choose(
        PlatformFile(name: 'edge.csv', size: bytes.length, bytes: bytes),
      );

      expect(stateOf().error, isNull);
      expect(client.sent, hasLength(1));
    });

    test('an empty file', () async {
      await notifier().choose(_file(content: ''));

      expect(stateOf().error, 'Choose a CSV file.');
      expect(client.sent, isEmpty);
    });

    test('closing the picker changes nothing', () async {
      await notifier().choose(null);

      expect(stateOf().error, isNull);
      expect(stateOf().file, isNull);
      expect(client.sent, isEmpty);
    });
  });

  group('targets', () {
    test('each module posts to its own two-step import, never upload/', () {
      expect(
        CsvImportTarget.tickets.previewUrl,
        endsWith('/api/cases/import/preview/'),
      );
      expect(
        CsvImportTarget.tickets.commitUrl,
        endsWith('/api/cases/import/commit/'),
      );
      expect(
        CsvImportTarget.leads.previewUrl,
        endsWith('/api/leads/import/preview/'),
      );
      expect(
        CsvImportTarget.leads.commitUrl,
        endsWith('/api/leads/import/commit/'),
      );
      for (final t in CsvImportTarget.values) {
        expect(t.commitUrl, isNot(contains('upload')));
      }
    });

    test('tickets preview goes to cases', () async {
      client.replies.add((200, _previewBody()));

      await notifier(CsvImportTarget.tickets).choose(_file());

      expect(
        client.sent.single.url.path,
        endsWith('/api/cases/import/preview/'),
      );
    });

    test('every template has one example cell per header and includes the '
        'required headers', () {
      for (final t in CsvImportTarget.values) {
        expect(t.templateExample, hasLength(t.templateHeaders.length));
        expect(t.templateHeaders, containsAll(t.requiredHeaders));
        final lines = t.templateCsv.split('\n');
        expect(lines, hasLength(2));
        expect(lines.first, t.templateHeaders.join(','));
      }
    });
  });
}
