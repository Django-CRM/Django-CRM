import 'dart:convert';
import 'dart:io';

import 'package:bottle_crm/services/api_service.dart';
import 'package:bottle_crm/services/attachment_upload.dart';
import 'package:file_picker/file_picker.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import '../helpers/fake_platform_file.dart';

/// The app could delete an attachment it had no way to create.
///
/// The risk in closing that is quiet failure. The Django views read one named
/// multipart field each (`request.FILES.get("task_attachment")`), and a request
/// that names the field wrongly, or that arrives as JSON because the default
/// `Content-Type` header was left on, is answered 200 with nothing stored. So
/// these tests assert what goes on the wire, not only what comes back.
class _RecordingClient extends http.BaseClient {
  _RecordingClient({this.status = 200, this.body = '{"attachments": []}'});

  final int status;
  final String body;
  http.BaseRequest? sent;
  List<int>? sentBody;

  @override
  Future<http.StreamedResponse> send(http.BaseRequest request) async {
    sent = request;
    sentBody = await request.finalize().toBytes();
    return http.StreamedResponse(
      Stream.value(utf8.encode(body)),
      status,
      request: request,
    );
  }
}

void main() {
  late _RecordingClient client;
  late Directory tempDir;

  setUpAll(() {
    tempDir = Directory.systemTemp.createTempSync('bottlecrm-upload-test-');
  });

  tearDownAll(() {
    if (tempDir.existsSync()) tempDir.deleteSync(recursive: true);
  });

  /// A real file on disk, because `MultipartFile.fromPath` reads it.
  ///
  /// `size` is metadata the picker reports and is what the limit is checked
  /// against, so it can say 25 MB while the file holds five bytes. That keeps
  /// the boundary test from writing 25 MB on every run.
  PlatformFile file({String name = 'quote.pdf', int size = 2048}) {
    final onDisk = File('${tempDir.path}/$name')..writeAsBytesSync([1, 2, 3]);
    return FakePlatformFile(name: name, size: size, path: onDisk.path);
  }

  setUp(() {
    client = _RecordingClient();
    ApiService().setClientForTesting(client);
  });

  group('the endpoint and field each record type uses', () {
    test('every target names a distinct field', () {
      // A wrong field name is a 200 with nothing stored, which is the worst
      // failure shape available: it looks like it worked.
      final fields = AttachmentTarget.values.map((t) => t.field).toSet();

      expect(fields, hasLength(AttachmentTarget.values.length));
      expect(AttachmentTarget.task.field, 'task_attachment');
      expect(AttachmentTarget.lead.field, 'lead_attachment');
      // The API calls a deal an opportunity and a ticket a case. The mobile
      // names are the product's; these two are the server's.
      expect(AttachmentTarget.deal.field, 'opportunity_attachment');
      expect(AttachmentTarget.ticket.field, 'case_attachment');
    });

    test('each URL ends at the record detail, with a trailing slash', () {
      for (final target in AttachmentTarget.values) {
        final url = target.urlFor('abc-123');

        expect(url, endsWith('/abc-123/'));
        expect(url, contains('/api/'));
      }
    });

    test('a deal posts to opportunities and a ticket to cases', () {
      expect(AttachmentTarget.deal.urlFor('x'), contains('/opportunities/'));
      expect(AttachmentTarget.ticket.urlFor('x'), contains('/cases/'));
    });
  });

  group('a file the user picked', () {
    test('can be selected for a support form without uploading yet', () async {
      final picked = file();
      final result = await selectAttachment(pickFile: () async => picked);

      expect(result.file, same(picked));
      expect(result.error, isNull);
      expect(client.sent, isNull);
    });

    test('is sent as multipart under the target field', () async {
      final result = await pickAndUploadAttachment(
        target: AttachmentTarget.task,
        recordId: 'task-1',
        pickFile: () async => file(),
      );

      expect(result.succeeded, isTrue);
      expect(client.sent, isA<http.MultipartRequest>());
      final request = client.sent as http.MultipartRequest;
      expect(request.files.single.field, 'task_attachment');
      expect(request.files.single.filename, 'quote.pdf');
    });

    test('goes out as multipart, not as the default JSON', () async {
      // Every other call this client makes sets `Content-Type:
      // application/json`, and Django parses no files out of a body labelled
      // that way: the view answers 200 and stores nothing. `MultipartRequest`
      // overwrites the header itself, which is a detail of package:http and so
      // is worth an assertion rather than trust.
      await pickAndUploadAttachment(
        target: AttachmentTarget.task,
        recordId: 'task-1',
        pickFile: () async => file(),
      );

      final contentType = client.sent!.headers['Content-Type'] ?? '';
      expect(contentType, startsWith('multipart/form-data'));
      expect(contentType, contains('boundary='));
    });

    test('goes to the record it was attached to', () async {
      await pickAndUploadAttachment(
        target: AttachmentTarget.ticket,
        recordId: 'ticket-9',
        pickFile: () async => file(),
      );

      expect(client.sent!.url.path, endsWith('/cases/ticket-9/'));
    });
  });

  group('when it should not be sent at all', () {
    test('closing the picker is a cancellation, not a failure', () async {
      final result = await pickAndUploadAttachment(
        target: AttachmentTarget.task,
        recordId: 'task-1',
        pickFile: () async => null,
      );

      expect(result.cancelled, isTrue);
      expect(result.error, isNull);
      // No message to show, and nothing sent.
      expect(client.sent, isNull);
    });

    test('a file over the limit never leaves the phone', () async {
      final result = await pickAndUploadAttachment(
        target: AttachmentTarget.task,
        recordId: 'task-1',
        pickFile: () async =>
            file(name: 'huge.mov', size: attachmentMaxBytes + 1),
      );

      expect(result.succeeded, isFalse);
      expect(client.sent, isNull, reason: 'it should not upload to be refused');
      expect(result.error, contains('25 MB or smaller'));
      expect(result.error, contains('huge.mov'));
    });

    test('a file exactly at the limit is sent', () async {
      // The boundary is inclusive, matching the server, which is where the
      // rule actually lives.
      final result = await pickAndUploadAttachment(
        target: AttachmentTarget.task,
        recordId: 'task-1',
        pickFile: () async => file(size: attachmentMaxBytes),
      );

      expect(result.succeeded, isTrue);
      expect(client.sent, isNotNull);
    });

    test('a pick with no readable path is reported, not sent', () async {
      final result = await pickAndUploadAttachment(
        target: AttachmentTarget.task,
        recordId: 'task-1',
        pickFile: () async => FakePlatformFile(name: 'ghost.txt', size: 10),
      );

      expect(result.succeeded, isFalse);
      expect(client.sent, isNull);
    });

    test(
      'a pick whose size cannot be measured is reported, not sent',
      () async {
        // file_picker 13 returns null from length() when the picker reported no
        // size and the copy cannot be read, so the limit could not be checked.
        final result = await pickAndUploadAttachment(
          target: AttachmentTarget.task,
          recordId: 'task-1',
          pickFile: () async => FakePlatformFile(
            name: 'gone.pdf',
            path: '${tempDir.path}/does-not-exist.pdf',
          ),
        );

        expect(
          result.error,
          'That file could not be read. Try picking it again.',
        );
        expect(client.sent, isNull);
      },
    );
  });

  group('when the server refuses', () {
    test("the server's own message is what the user sees", () async {
      // The server rejects an oversized file too, and it is the only check
      // that counts. Its wording has to reach the screen.
      ApiService().setClientForTesting(
        _RecordingClient(
          status: 400,
          body: '{"attachment": ["Files must be 25 MB or smaller."]}',
        ),
      );

      final result = await pickAndUploadAttachment(
        target: AttachmentTarget.task,
        recordId: 'task-1',
        pickFile: () async => file(),
      );

      expect(result.succeeded, isFalse);
      expect(result.error, contains('25 MB'));
    });

    test('a failure is never reported as a cancellation', () async {
      ApiService().setClientForTesting(_RecordingClient(status: 500, body: ''));

      final result = await pickAndUploadAttachment(
        target: AttachmentTarget.task,
        recordId: 'task-1',
        pickFile: () async => file(),
      );

      expect(result.cancelled, isFalse);
      expect(result.succeeded, isFalse);
      expect(result.error, isNotNull);
    });
  });

  test(
    'the refreshed attachment list comes back for the screen to use',
    () async {
      // The detail POST answers with the whole list, the same as it does for a
      // comment, so a screen can update in place instead of refetching.
      ApiService().setClientForTesting(
        _RecordingClient(
          body: '{"attachments": [{"id": "a1", "file_name": "quote.pdf"}]}',
        ),
      );

      final result = await pickAndUploadAttachment(
        target: AttachmentTarget.deal,
        recordId: 'deal-1',
        pickFile: () async => file(),
      );

      expect(result.data?['attachments'], hasLength(1));
    },
  );
}
