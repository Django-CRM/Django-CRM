import 'package:bottle_crm/data/models/custom_field_definition.dart';
import 'package:bottle_crm/data/models/custom_object.dart';
import 'package:bottle_crm/providers/auth_provider.dart';
import 'package:bottle_crm/providers/custom_objects_provider.dart';
import 'package:bottle_crm/screens/objects/object_record_detail_screen.dart';
import 'package:bottle_crm/screens/objects/object_records_screen.dart';
import 'package:bottle_crm/services/api_service.dart';
import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';

const _key = 'property';
const _id = '11111111-1111-1111-1111-111111111111';
const _id2 = '22222222-2222-2222-2222-222222222222';

Map<String, dynamic> _note(String id, String text, String email) => {
  'id': id,
  'comment': text,
  'commented_on': '2026-10-27T10:00:00Z',
  'commented_by': {
    'id': 'p-$id',
    'user_details': {'email': email},
  },
};

Map<String, dynamic> _record(List<Map<String, dynamic>> notes) => {
  'id': _id,
  'name': 'Harbour View',
  'created_at': '2026-10-01T10:00:00Z',
  'created_by': {'id': 'p9', 'name': 'Meera', 'email': 'meera@example.com'},
  'can_edit': true,
  'can_delete': true,
  'comments': notes,
};

ApiResponse<Map<String, dynamic>> _ok(Map<String, dynamic> body) =>
    ApiResponse(success: true, data: body, statusCode: 200);

ApiResponse<Map<String, dynamic>> _refused(int status, String detail) =>
    ApiResponse(
      success: false,
      statusCode: status,
      data: {'detail': detail},
      message: detail,
    );

class _FakeObjects extends CustomObjectsNotifier {
  _FakeObjects(this.writes);

  final bool writes;

  @override
  Future<CustomObjectsAnswer?> build() async => CustomObjectsAnswer(
    hasCustomObjects: writes,
    objects: const [
      ObjectSummary(key: _key, label: 'Property', labelPlural: 'Properties'),
    ],
  );
}

class _FakeRecords extends ObjectRecordsNotifier {
  _FakeRecords() : super(_key);

  @override
  Future<ObjectRecordsData> build() async => const ObjectRecordsData();
}

class _Api extends CustomObjectsApi {
  _Api({required this.record, this.board, this.moveAnswer});

  Map<String, dynamic> record;
  final Map<String, dynamic>? board;
  ApiResponse<Map<String, dynamic>>? moveAnswer;
  ApiResponse<Map<String, dynamic>>? noteAnswer;
  ApiResponse<Map<String, dynamic>>? boardAnswer;
  final calls = <String>[];
  final moves = <Map<String, dynamic>>[];

  @override
  Future<ApiResponse<Map<String, dynamic>>> fetchRecord(
    String key,
    String id,
  ) async => _ok(record);

  @override
  Future<ApiResponse<Map<String, dynamic>>> addNote(
    String key,
    String id,
    String text,
  ) async {
    calls.add('add:$text');
    final answer = noteAnswer;
    if (answer != null) return answer;
    record = {
      ...record,
      'comments': [_note('n9', text, 'me@example.com'), ...record['comments']],
    };
    return _ok(record);
  }

  @override
  Future<ApiResponse<Map<String, dynamic>>> editNote(
    String key,
    String commentId,
    String text,
  ) async {
    calls.add('edit:$commentId');
    return noteAnswer ?? _ok({'id': commentId});
  }

  @override
  Future<ApiResponse<Map<String, dynamic>>> deleteNote(
    String key,
    String commentId,
  ) async {
    calls.add('delete:$commentId');
    return noteAnswer ?? const ApiResponse(success: true, statusCode: 204);
  }

  @override
  Future<ApiResponse<Map<String, dynamic>>> fetchBoard(
    String key,
    Map<String, dynamic> filters,
  ) async => boardAnswer ?? _ok(board ?? const {});

  @override
  Future<ApiResponse<Map<String, dynamic>>> moveCard(
    String key,
    Map<String, dynamic> body,
  ) async {
    moves.add(body);
    return moveAnswer ??
        _ok({
          'record': {
            'id': body['record'],
            'name': 'Harbour View',
            'can_edit': true,
          },
        });
  }
}

ObjectField _stage({bool required = false}) => ObjectField(
  key: 'stage',
  label: 'Stage',
  fieldType: 'dropdown',
  isRequired: required,
  options: const [
    CustomFieldOption(value: 'new', label: 'New'),
    CustomFieldOption(value: 'won', label: 'Won'),
  ],
);

ObjectDefinition _definition({String board = 'stage'}) => ObjectDefinition(
  summary: ObjectSummary(
    key: _key,
    label: 'Property',
    labelPlural: 'Properties',
    boardField: board,
  ),
  fields: [_stage()],
);

Widget _app(
  Widget home,
  _Api api, {
  bool writes = true,
  bool admin = false,
  String email = 'me@example.com',
  ObjectDefinition? definition,
}) => ProviderScope(
  key: UniqueKey(),
  overrides: [
    customObjectsApiProvider.overrideWithValue(api),
    customObjectsProvider.overrideWith(() => _FakeObjects(writes)),
    objectDefinitionProvider.overrideWith(
      (ref, key) async => definition ?? _definition(),
    ),
    objectRecordsProvider(_key).overrideWith(_FakeRecords.new),
    myEmailProvider.overrideWithValue(email),
    isOrgAdminProvider.overrideWithValue(admin),
  ],
  child: MaterialApp.router(
    routerConfig: GoRouter(
      routes: [
        GoRoute(path: '/', builder: (_, _) => home),
        GoRoute(
          path: '/more/objects/:key/:id',
          builder: (_, _) => const Scaffold(body: Text('detail screen')),
        ),
      ],
    ),
    builder: (context, child) => MediaQuery(
      data: MediaQuery.of(context).copyWith(size: const Size(300, 800)),
      child: child!,
    ),
  ),
);

Future<void> _pump(WidgetTester tester, Widget app) async {
  tester.view.devicePixelRatio = 1;
  tester.view.physicalSize = const Size(1000, 1400);
  addTearDown(tester.view.reset);
  await tester.pumpWidget(app);
  await tester.pumpAndSettle();
}

const _detail = ObjectRecordDetailScreen(objectKey: _key, recordId: _id);

Map<String, dynamic> _boardBody({bool firstEditable = true}) => {
  'field': {'key': 'stage', 'label': 'Stage'},
  'columns': [
    {
      'value': 'new',
      'label': 'New',
      'kind': 'option',
      'count': 1,
      'records': [
        {'id': _id, 'name': 'Harbour View', 'can_edit': firstEditable},
      ],
    },
    {
      'value': 'won',
      'label': 'Won',
      'kind': 'option',
      'count': 1,
      'records': [
        {'id': _id2, 'name': 'Quay Side', 'can_edit': true},
      ],
    },
    {
      'value': null,
      'label': 'No value',
      'kind': 'none',
      'count': 0,
      'records': [],
    },
    {
      'value': null,
      'label': 'Other values',
      'kind': 'other',
      'count': 0,
      'records': [],
    },
  ],
};

Future<void> _drag(WidgetTester tester, String from, String to) async {
  final gesture = await tester.startGesture(tester.getCenter(find.text(from)));
  await tester.pump(kLongPressTimeout + const Duration(milliseconds: 50));
  await gesture.moveTo(tester.getCenter(find.text(to)).translate(0, 120));
  await tester.pump();
  await gesture.up();
  await tester.pumpAndSettle();
}

void main() {
  group('notes on the record timeline', () {
    final notes = [
      _note('n1', 'Mine', 'me@example.com'),
      _note('n2', 'Theirs', 'other@example.com'),
    ];

    testWidgets('shows notes and the creation, newest first', (tester) async {
      await _pump(tester, _app(_detail, _Api(record: _record(notes))));
      expect(find.text('Mine'), findsOneWidget);
      expect(find.text('Theirs'), findsOneWidget);
      expect(find.textContaining('created this property'), findsOneWidget);
      expect(find.text('Add a note'), findsOneWidget);
    });

    testWidgets('the composer stops at the server cap', (tester) async {
      await _pump(tester, _app(_detail, _Api(record: _record(notes))));
      await tester.enterText(find.byType(TextField), 'x' * 10050);
      final field = tester.widget<TextField>(find.byType(TextField));
      expect(field.controller!.text.length, noteMaxLength);
    });

    testWidgets('a member edits only their own note and deletes it', (
      tester,
    ) async {
      await _pump(tester, _app(_detail, _Api(record: _record(notes))));
      // Only the author's note carries a menu.
      expect(find.byTooltip('Note actions'), findsOneWidget);
      await tester.tap(find.byTooltip('Note actions'));
      await tester.pumpAndSettle();
      expect(find.text('Edit'), findsOneWidget);
      expect(find.text('Delete'), findsOneWidget);
    });

    testWidgets('an admin deletes any note but edits none but their own', (
      tester,
    ) async {
      final api = _Api(
        record: _record([_note('n2', 'Theirs', 'other@example.com')]),
      );
      await _pump(tester, _app(_detail, api, admin: true));
      await tester.tap(find.byTooltip('Note actions'));
      await tester.pumpAndSettle();
      expect(find.text('Edit'), findsNothing);
      await tester.tap(find.text('Delete'));
      await tester.pumpAndSettle();
      await tester.tap(find.widgetWithText(TextButton, 'Delete'));
      await tester.pumpAndSettle();
      expect(api.calls, ['delete:n2']);
    });

    testWidgets('posting a note shows it from the answer', (tester) async {
      final api = _Api(record: _record(const []));
      await _pump(tester, _app(_detail, api));
      await tester.enterText(find.byType(TextField), 'A new note');
      await tester.tap(find.byTooltip('Add note'));
      await tester.pumpAndSettle();
      expect(api.calls, ['add:A new note']);
      expect(find.text('A new note'), findsOneWidget);
    });

    testWidgets('a refused post says why and keeps the text', (tester) async {
      final api = _Api(record: _record(const []))
        ..noteAnswer = _refused(403, 'Your plan does not include this.');
      await _pump(tester, _app(_detail, api));
      await tester.enterText(find.byType(TextField), 'Keep me');
      await tester.tap(find.byTooltip('Add note'));
      await tester.pumpAndSettle();
      expect(find.text('Your plan does not include this.'), findsOneWidget);
      expect(find.text('Keep me'), findsOneWidget);
    });

    testWidgets('after a downgrade the composer and edit go, delete stays', (
      tester,
    ) async {
      await _pump(
        tester,
        _app(_detail, _Api(record: _record(notes)), writes: false),
      );
      expect(find.text('Add a note'), findsNothing);
      await tester.tap(find.byTooltip('Note actions'));
      await tester.pumpAndSettle();
      expect(find.text('Edit'), findsNothing);
      expect(find.text('Delete'), findsOneWidget);
    });

    testWidgets('a refused delete shows the server words', (tester) async {
      final api = _Api(record: _record(notes))
        ..noteAnswer = _refused(403, 'You cannot delete this note.');
      await _pump(tester, _app(_detail, api));
      await tester.tap(find.byTooltip('Note actions'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Delete'));
      await tester.pumpAndSettle();
      await tester.tap(find.widgetWithText(TextButton, 'Delete'));
      await tester.pumpAndSettle();
      expect(find.text('You cannot delete this note.'), findsOneWidget);
    });
  });

  group('board mode', () {
    const list = ObjectRecordsScreen(objectKey: _key);

    testWidgets('no toggle without a board field', (tester) async {
      await _pump(
        tester,
        _app(
          list,
          _Api(record: const {}),
          definition: _definition(board: ''),
        ),
      );
      expect(find.text('Board'), findsNothing);
    });

    testWidgets('the toggle shows columns in order with counts', (
      tester,
    ) async {
      await _pump(
        tester,
        _app(list, _Api(record: const {}, board: _boardBody())),
      );
      await tester.tap(find.text('Board'));
      await tester.pumpAndSettle();
      for (final t in ['New', 'Won', 'No value', 'Other values']) {
        expect(find.text(t), findsOneWidget);
      }
      expect(find.text('Harbour View'), findsOneWidget);
      expect(find.text('Quay Side'), findsOneWidget);
    });

    testWidgets('dropping a card on a column sends one move', (tester) async {
      final api = _Api(record: const {}, board: _boardBody());
      await _pump(tester, _app(list, api));
      await tester.tap(find.text('Board'));
      await tester.pumpAndSettle();
      await _drag(tester, 'Harbour View', 'Won');
      expect(api.moves, [
        {'record': _id, 'value': 'won', 'above_id': _id2, 'below_id': null},
      ]);
    });

    testWidgets('a refused move shows the message and the card returns', (
      tester,
    ) async {
      final api = _Api(
        record: const {},
        board: _boardBody(),
        moveAnswer: _refused(403, 'You cannot change this record.'),
      );
      await _pump(tester, _app(list, api));
      await tester.tap(find.text('Board'));
      await tester.pumpAndSettle();
      await _drag(tester, 'Harbour View', 'Won');
      expect(find.text('You cannot change this record.'), findsOneWidget);
      final card = tester.getCenter(find.text('Harbour View'));
      final newCol = tester.getCenter(find.text('New'));
      expect((card.dx - newCol.dx).abs() < 200, isTrue);
    });

    testWidgets('nothing drops on Other values, or on No value if required', (
      tester,
    ) async {
      final api = _Api(record: const {}, board: _boardBody());
      final required = ObjectDefinition(
        summary: _definition().summary,
        fields: [_stage(required: true)],
      );
      await _pump(tester, _app(list, api, definition: required));
      await tester.tap(find.text('Board'));
      await tester.pumpAndSettle();
      await _drag(tester, 'Harbour View', 'Other values');
      await _drag(tester, 'Harbour View', 'No value');
      expect(api.moves, isEmpty);
    });

    testWidgets('a card the viewer cannot edit does not pick up', (
      tester,
    ) async {
      final api = _Api(
        record: const {},
        board: _boardBody(firstEditable: false),
      );
      await _pump(tester, _app(list, api));
      await tester.tap(find.text('Board'));
      await tester.pumpAndSettle();
      await _drag(tester, 'Harbour View', 'Won');
      expect(api.moves, isEmpty);
      await _drag(tester, 'Quay Side', 'New');
      expect(api.moves, hasLength(1));
    });

    testWidgets('a failed board load shows the reason', (tester) async {
      final api = _Api(record: const {}, board: const {})
        ..boardAnswer = _refused(403, 'Custom objects are not on this plan.');
      await _pump(tester, _app(list, api));
      await tester.tap(find.text('Board'));
      await tester.pumpAndSettle();
      expect(find.text('Failed to load the board'), findsOneWidget);
      expect(find.text('Custom objects are not on this plan.'), findsOneWidget);
    });

    testWidgets('with writes off a card does not pick up', (tester) async {
      final api = _Api(record: const {}, board: _boardBody());
      await _pump(tester, _app(list, api, writes: false));
      await tester.tap(find.text('Board'));
      await tester.pumpAndSettle();
      await _drag(tester, 'Harbour View', 'Won');
      expect(api.moves, isEmpty);
    });
  });
}
