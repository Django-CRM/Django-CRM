import 'dart:typed_data';

import 'package:file_picker/file_picker.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../config/api_config.dart';
import '../services/api_service.dart';

/// CSV import for contacts, tickets and leads: preview, then commit.
///
/// The same two-step contract the web drawers speak
/// (`ContactImportDrawer`, `TicketImportDrawer`, `LeadImportDrawer`): one
/// multipart `file` posted to `import/preview/`, which validates every row and
/// writes nothing, then the same bytes posted to `import/commit/`, which
/// validates again and creates the rows in one transaction.
///
/// Every rule checked here is a courtesy. The import views decide who may
/// import (`can_mass_import`: admins, superusers and sales access), cap the
/// size at 5 MB and the rows at 5000, and scope every write to the caller's
/// org. The checks below only save somebody an upload the server would refuse.
enum CsvImportTarget {
  contacts(
    singular: 'contact',
    plural: 'contacts',
    description:
        "Upload a CSV of contacts. We'll validate every row, including "
        'duplicates by email, phone, and name, before writing anything.',
    requiredHeaders: ['first_name', 'last_name'],
    optionalHelp:
        'email, phone, organization, title, department, do_not_call (yes/no), '
        'linkedin_url, address_line, city, state, postcode, country (2-letter '
        'code), description, account_name, assigned_emails, team_names, tags '
        '(semicolon-separated for the last three).',
    extraHelp:
        'Duplicates: rows with a previously-used email or phone (in your org '
        'or earlier in the file) are flagged. Two people with the same name '
        'are allowed only if at least one has an email or phone to tell them '
        'apart.',
    templateHeaders: [
      'first_name',
      'last_name',
      'email',
      'phone',
      'organization',
      'title',
      'department',
      'do_not_call',
      'linkedin_url',
      'address_line',
      'city',
      'state',
      'postcode',
      'country',
      'description',
      'account_name',
      'assigned_emails',
      'team_names',
      'tags',
    ],
    templateExample: [
      'Alice',
      'Smith',
      'alice@acme.test',
      '+1 (555) 123-4567',
      'Acme Corp',
      'VP of Sales',
      'Sales',
      'no',
      'https://linkedin.com/in/alice',
      '500 Main St',
      'Austin',
      'TX',
      '78701',
      'US',
      'Met at SaaStr 2024',
      'Acme Corp',
      'agent@yourco.com',
      'Sales',
      'vip;newsletter',
    ],
  ),
  tickets(
    singular: 'ticket',
    plural: 'tickets',
    description:
        "Upload a CSV of tickets. We'll validate every row before writing "
        'anything. References (account, contacts, assignees) must already '
        'exist in your org.',
    requiredHeaders: ['name', 'status', 'priority'],
    optionalHelp:
        'description, case_type, account_name, contact_emails, '
        'assigned_emails, team_names, tags (semicolon-separated), closed_on '
        '(YYYY-MM-DD).',
    templateHeaders: [
      'name',
      'status',
      'priority',
      'description',
      'case_type',
      'account_name',
      'contact_emails',
      'assigned_emails',
      'team_names',
      'tags',
      'closed_on',
    ],
    templateExample: [
      'Login button broken',
      'New',
      'High',
      'Returns 500 on submit',
      'Incident',
      'Acme Corp',
      'pat@acme.test',
      'agent@yourco.com',
      'Support',
      'auth;urgent',
      '',
    ],
  ),
  leads(
    singular: 'lead',
    plural: 'leads',
    description:
        "Upload a CSV of leads. We'll validate every row, with the same rules "
        'as adding one by hand, before writing anything.',
    requiredHeaders: ['first_name', 'last_name'],
    optionalHelp:
        'title, salutation, email, phone, job_title, company_name, website, '
        'linkedin_url, status, source, industry, rating, opportunity_amount, '
        'currency, probability, close_date, last_contacted, next_follow_up '
        '(dates as YYYY-MM-DD), address_line, city, state, postcode, country, '
        'description, assigned_emails, team_names, tags (semicolon-separated '
        'for the last three).',
    requiredNote: 'each row needs at least one of the two.',
    extraHelp:
        'Choice columns such as status and source accept the value or its '
        'label. An email already used by a lead in your org, or earlier in '
        'the file, is flagged.',
    templateHeaders: [
      'first_name',
      'last_name',
      'email',
      'phone',
      'title',
      'company_name',
      'job_title',
      'status',
      'source',
      'rating',
      'opportunity_amount',
      'close_date',
      'description',
      'assigned_emails',
      'team_names',
      'tags',
    ],
    templateExample: [
      'Alice',
      'Smith',
      'alice@acme.test',
      '+1 (555) 123-4567',
      'Enterprise rollout',
      'Acme Corp',
      'VP of Sales',
      'assigned',
      'email',
      'HOT',
      '12000',
      '2026-12-31',
      'Met at SaaStr',
      'agent@yourco.com',
      'Sales',
      'vip;trade-show',
    ],
  );

  const CsvImportTarget({
    required this.singular,
    required this.plural,
    required this.description,
    required this.requiredHeaders,
    required this.optionalHelp,
    required this.templateHeaders,
    required this.templateExample,
    this.requiredNote,
    this.extraHelp,
  });

  final String singular;
  final String plural;

  /// The web drawer's description line, word for word.
  final String description;
  final List<String> requiredHeaders;

  /// Appended to the required headers sentence (leads only).
  final String? requiredNote;
  final String optionalHelp;
  final String? extraHelp;

  /// One header row and one example row, the same file the web drawer's
  /// "Download CSV template" produces.
  final List<String> templateHeaders;
  final List<String> templateExample;

  String get _moduleUrl {
    switch (this) {
      case CsvImportTarget.contacts:
        return ApiConfig.contacts;
      case CsvImportTarget.tickets:
        return ApiConfig.tickets;
      case CsvImportTarget.leads:
        return ApiConfig.leads;
    }
  }

  String get previewUrl => ApiConfig.csvImportPreview(_moduleUrl);
  String get commitUrl => ApiConfig.csvImportCommit(_moduleUrl);

  String get templateFileName => '$plural-import-template.csv';

  /// The template as CSV text, quoting a cell only when it holds a comma, as
  /// the web does.
  String get templateCsv => [
    templateHeaders.join(','),
    templateExample.map((v) => v.contains(',') ? '"$v"' : v).join(','),
  ].join('\n');

  String noun(int count) => count == 1 ? singular : plural;
}

/// Mirrors `MAX_UPLOAD_BYTES` in the three import views.
const int csvImportMaxBytes = 5 * 1024 * 1024;

/// The web's wording for a 403 (`frontend/src/lib/server/v2/csv-import.js`),
/// kept identical so both clients say the same thing.
const String csvImportForbiddenMessage =
    'Importing needs an admin, or a member with sales access.';

/// A picked file, held as bytes so the commit sends exactly what was
/// previewed.
class CsvImportFile {
  const CsvImportFile({required this.name, required this.bytes});

  final String name;
  final Uint8List bytes;
}

/// One problem with one row. `row` is 1-based and matches the data rows of
/// the file (the header is row 0).
class CsvRowError {
  const CsvRowError({
    required this.row,
    required this.field,
    required this.message,
  });

  factory CsvRowError.fromJson(Map<String, dynamic> json) => CsvRowError(
    row: (json['row'] as num?)?.toInt() ?? 0,
    field: json['field']?.toString() ?? '',
    message: json['message']?.toString() ?? '',
  );

  final int row;
  final String field;
  final String message;
}

List<CsvRowError> _rowErrors(Object? raw) => raw is List
    ? raw
          .whereType<Map<String, dynamic>>()
          .map(CsvRowError.fromJson)
          .toList(growable: false)
    : const [];

/// What `import/preview/` answered.
class CsvImportPreview {
  const CsvImportPreview({
    required this.total,
    required this.valid,
    required this.invalid,
    required this.errors,
    this.headerError,
  });

  factory CsvImportPreview.fromJson(Map<String, dynamic> json) {
    final summary = json['summary'] is Map<String, dynamic>
        ? json['summary'] as Map<String, dynamic>
        : const <String, dynamic>{};
    int count(String key) => (summary[key] as num?)?.toInt() ?? 0;
    final headerError = json['header_error']?.toString();
    return CsvImportPreview(
      total: count('total'),
      valid: count('valid'),
      invalid: count('invalid'),
      errors: _rowErrors(json['errors']),
      headerError: (headerError == null || headerError.isEmpty)
          ? null
          : headerError,
    );
  }

  final int total;
  final int valid;
  final int invalid;
  final List<CsvRowError> errors;

  /// Set when the file as a whole could not be read: missing or unknown
  /// headers, too many rows, not UTF-8. No row is checked in that case.
  final String? headerError;
}

class CsvImportState {
  const CsvImportState({
    this.file,
    this.preview,
    this.busy = false,
    this.error,
    this.commitErrors = const [],
    this.created,
  });

  final CsvImportFile? file;
  final CsvImportPreview? preview;
  final bool busy;
  final String? error;

  /// Rows the commit refused, which happens only if the file or the org's
  /// data changed after the preview passed.
  final List<CsvRowError> commitErrors;

  /// Set once a commit succeeds.
  final int? created;

  /// The commit refuses the whole file when any row fails
  /// (`commit_rows`: "Fix the invalid rows before importing"), so a preview
  /// with row errors cannot be committed either. Same gate as the web
  /// drawer's Import button.
  bool get canCommit {
    final p = preview;
    return !busy &&
        file != null &&
        p != null &&
        p.headerError == null &&
        p.errors.isEmpty &&
        p.valid > 0;
  }
}

class CsvImportNotifier extends Notifier<CsvImportState> {
  CsvImportNotifier(this.target);

  final CsvImportTarget target;
  final ApiService _api = ApiService();

  @override
  CsvImportState build() => const CsvImportState();

  /// Take a file from the picker and preview it straight away. `null` is the
  /// picker being closed and changes nothing.
  ///
  /// The client-side half of `_read_upload`: the extension and the size are
  /// checked on what the picker reports before anything is read, so a large
  /// file is refused without loading it, and the size again on the bytes.
  Future<void> choose(PlatformFile? picked) async {
    if (picked == null) return;
    final early = _refuse(picked.name, picked.size);
    if (early != null) {
      state = CsvImportState(error: early);
      return;
    }
    final Uint8List bytes;
    try {
      bytes = picked.bytes ?? await picked.xFile.readAsBytes();
    } catch (_) {
      if (ref.mounted) {
        state = const CsvImportState(
          error: 'That file could not be read. Try picking it again.',
        );
      }
      return;
    }
    if (!ref.mounted) return;
    final refusal = bytes.isEmpty
        ? 'Choose a CSV file.'
        : _refuse(picked.name, bytes.length);
    if (refusal != null) {
      state = CsvImportState(error: refusal);
      return;
    }
    state = CsvImportState(
      file: CsvImportFile(name: picked.name, bytes: bytes),
    );
    await _preview();
  }

  String? _refuse(String name, int size) {
    if (!name.toLowerCase().endsWith('.csv')) {
      return '$name is not a CSV file. Choose a file ending in .csv.';
    }
    if (size > csvImportMaxBytes) {
      final mb = (size / (1024 * 1024)).toStringAsFixed(1);
      return '$name is $mb MB. CSV files must be 5 MB or smaller.';
    }
    return null;
  }

  Future<void> _preview() async {
    final file = state.file;
    if (file == null) return;
    state = CsvImportState(file: file, busy: true);
    final response = await _upload(target.previewUrl, file);
    if (!ref.mounted) return;
    if (!response.success || response.data == null) {
      state = CsvImportState(
        file: file,
        error: _failureMessage(response, 'Preview failed'),
      );
      return;
    }
    state = CsvImportState(
      file: file,
      preview: CsvImportPreview.fromJson(response.data!),
    );
  }

  /// Send the previewed bytes again. Returns whether rows were created.
  Future<bool> commit() async {
    if (!state.canCommit) return false;
    final file = state.file!;
    final preview = state.preview;
    state = CsvImportState(file: file, preview: preview, busy: true);
    final response = await _upload(target.commitUrl, file);
    if (!ref.mounted) return false;
    final body = response.data;
    if (!response.success || body == null) {
      state = CsvImportState(
        file: file,
        preview: preview,
        error: _failureMessage(response, 'Import failed'),
        commitErrors: _rowErrors(body?['errors']),
      );
      return false;
    }
    state = CsvImportState(
      file: file,
      preview: preview,
      created: (body['created'] as num?)?.toInt() ?? 0,
    );
    return true;
  }

  /// Back to an empty sheet, to pick a corrected file.
  void reset() => state = const CsvImportState();

  Future<ApiResponse<Map<String, dynamic>>> _upload(
    String url,
    CsvImportFile file,
  ) => _api.postMultipart(
    url,
    fileField: 'file',
    fileBytes: file.bytes,
    fileName: file.name,
  );

  /// The web's mapping (`forwardCsvImport`): a 403 gets its own sentence,
  /// anything else the server's `message`, then its `header_error`. A
  /// network failure has neither and keeps ApiService's own message.
  String _failureMessage(
    ApiResponse<Map<String, dynamic>> response,
    String fallback,
  ) {
    if (response.statusCode == 403) return csvImportForbiddenMessage;
    final body = response.data;
    for (final key in const ['message', 'header_error']) {
      final value = body?[key];
      if (value is String && value.isNotEmpty) return value;
    }
    if (response.statusCode == 0 && response.message != null) {
      return response.message!;
    }
    return fallback;
  }
}

/// One import per module, discarded when its sheet closes, so reopening the
/// sheet starts empty as the web drawer does.
final csvImportProvider = NotifierProvider.autoDispose
    .family<CsvImportNotifier, CsvImportState, CsvImportTarget>(
      CsvImportNotifier.new,
    );
