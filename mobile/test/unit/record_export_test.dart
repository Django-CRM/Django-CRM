import 'dart:convert';
import 'dart:typed_data';

import 'package:bottle_crm/config/api_config.dart';
import 'package:bottle_crm/providers/tickets_provider.dart';
import 'package:bottle_crm/services/api_service.dart';
import 'package:bottle_crm/services/record_export.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;

/// The CSV export sends the list's own filters and saves what comes back.
/// What goes on the wire is asserted, because a filter dropped here is an
/// export that quietly holds more than the screen showed.
class _RecordingClient extends http.BaseClient {
  _RecordingClient({this.status = 200, this.body = 'ID\r\n'});

  final int status;
  final String body;
  http.BaseRequest? sent;

  @override
  Future<http.StreamedResponse> send(http.BaseRequest request) async {
    sent = request;
    return http.StreamedResponse(
      Stream.value(utf8.encode(body)),
      status,
      request: request,
    );
  }
}

void main() {
  late _RecordingClient client;

  setUp(() {
    client = _RecordingClient();
    ApiService().setClientForTesting(client);
  });

  test('a list value is a repeated parameter, blanks are left out', () {
    final url = Uri.parse(
      exportUrl('https://api.test/api/cases/export/', {
        'status': ['New', 'Pending'],
        'search': '',
        'priority': 'High',
        'tags': <String>[],
      }),
    );
    expect(url.queryParametersAll['status'], ['New', 'Pending']);
    expect(url.queryParameters['priority'], 'High');
    expect(url.queryParameters.containsKey('search'), isFalse);
    expect(url.queryParameters.containsKey('tags'), isFalse);
  });

  test('the file is named like the web download', () {
    expect(
      exportFileName('deals', DateTime(2026, 3, 7)),
      'deals-2026-03-07.csv',
    );
  });

  test('saves the bytes under the day-stamped name', () async {
    Uint8List? savedBytes;
    String? savedName;
    final result = await exportRecordsCsv(
      endpoint: ApiConfig.leadsExport,
      query: {
        'search': 'ada',
        'status': ['assigned', 'recycled'],
      },
      filePrefix: 'leads',
      today: DateTime(2026, 9, 26),
      saveFile: (bytes, name) async {
        savedBytes = bytes;
        savedName = name;
        return '/tmp/$name';
      },
    );

    expect(result.outcome, RecordExportOutcome.saved);
    expect(result.fileName, 'leads-2026-09-26.csv');
    expect(savedName, 'leads-2026-09-26.csv');
    expect(utf8.decode(savedBytes!), 'ID\r\n');
    final url = client.sent!.url;
    expect(url.path, endsWith('/leads/export/'));
    expect(url.queryParameters['search'], 'ada');
    expect(url.queryParametersAll['status'], ['assigned', 'recycled']);
  });

  test('closing the save dialog is not a failure', () async {
    final result = await exportRecordsCsv(
      endpoint: ApiConfig.contactsExport,
      query: const {},
      filePrefix: 'contacts',
      saveFile: (_, _) async => null,
    );
    expect(result.outcome, RecordExportOutcome.cancelled);
  });

  test('a refused request is a failure and nothing is saved', () async {
    ApiService().setClientForTesting(
      _RecordingClient(status: 400, body: '{"detail": "bad filter"}'),
    );
    var saved = false;
    final result = await exportRecordsCsv(
      endpoint: ApiConfig.invoicesExport,
      query: const {'issue_date_gte': 'nope'},
      filePrefix: 'invoices',
      saveFile: (_, _) async {
        saved = true;
        return 'x';
      },
    );
    expect(result.outcome, RecordExportOutcome.failed);
    expect(result.error, isNotEmpty);
    expect(saved, isFalse);
  });

  test('a save that throws is a failure', () async {
    final result = await exportRecordsCsv(
      endpoint: ApiConfig.accountsExport,
      query: const {},
      filePrefix: 'accounts',
      saveFile: (_, _) async => throw Exception('disk full'),
    );
    expect(result.outcome, RecordExportOutcome.failed);
  });

  test('the ticket queue query carries every filter the list sends', () {
    final query = ticketListQuery(
      const TicketListFilters(
        search: 'printer',
        statusList: ['New', 'Assigned', 'Pending'],
        priority: 'High',
        assigneeIds: ['a1', 'a2'],
        slaBreached: true,
      ),
    );
    expect(query, {
      'search': 'printer',
      'status': ['New', 'Assigned', 'Pending'],
      'priority': 'High',
      'sla_breached': 'true',
      'assigned_to': ['a1', 'a2'],
    });
    expect(ticketListQuery(const TicketListFilters(status: 'Closed')), {
      'status': 'Closed',
    });
  });
}
