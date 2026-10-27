import 'package:bottle_crm/config/api_config.dart';
import 'package:bottle_crm/data/models/comment.dart';
import 'package:bottle_crm/data/models/custom_object.dart';
import 'package:bottle_crm/data/models/object_board.dart';
import 'package:bottle_crm/providers/custom_objects_provider.dart';
import 'package:bottle_crm/services/api_service.dart';
import 'package:flutter_test/flutter_test.dart';

const _a = '11111111-1111-1111-1111-111111111111';
const _b = '22222222-2222-2222-2222-222222222222';
const _c = '33333333-3333-3333-3333-333333333333';

Map<String, dynamic> _row(String id) => {
  'id': id,
  'name': 'Row $id',
  'can_edit': id != _c,
};

Map<String, dynamic> _boardJson() => {
  'field': {'key': 'stage', 'label': 'Stage'},
  'columns': [
    {
      'value': 'new',
      'label': 'New',
      'kind': 'option',
      'count': 2,
      'records': [_row(_a), _row(_b)],
    },
    {
      'value': 'won',
      'label': 'Won',
      'kind': 'option',
      'count': 0,
      'records': [],
    },
    {
      'value': null,
      'label': 'No value',
      'kind': 'none',
      'count': 1,
      'records': [_row(_c)],
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

Comment _note(String email) => Comment(
  id: 'n1',
  comment: 'text',
  commentedOn: DateTime(2026),
  commentedByEmail: email,
);

void main() {
  group('board model', () {
    test('reads columns in the order the server sent them', () {
      final board = ObjectBoard.fromJson(_boardJson());
      expect(board.fieldKey, 'stage');
      expect(
        [for (final c in board.columns) c.kind],
        [
          BoardColumnKind.option,
          BoardColumnKind.option,
          BoardColumnKind.none,
          BoardColumnKind.other,
        ],
      );
      expect(board.columns.first.records.map((r) => r.id), [_a, _b]);
      expect(board.columns[2].value, isNull);
    });

    test('a missing body reads as an empty board', () {
      expect(ObjectBoard.fromJson(const {}).columns, isEmpty);
    });

    test('drop rules: other column, own column and required none refuse', () {
      final board = ObjectBoard.fromJson(_boardJson());
      final record = board.columns.first.records.first;
      final [newCol, won, none, other] = board.columns;
      expect(board.canDrop(record, won, fieldRequired: false), isTrue);
      expect(board.canDrop(record, none, fieldRequired: false), isTrue);
      expect(board.canDrop(record, none, fieldRequired: true), isFalse);
      expect(board.canDrop(record, other, fieldRequired: false), isFalse);
      expect(board.canDrop(record, newCol, fieldRequired: false), isFalse);
    });

    test('moved shifts the card and both counts, leaving the rest alone', () {
      final board = ObjectBoard.fromJson(_boardJson());
      final record = board.columns.first.records.first;
      final next = board.moved(record, board.columns[1]);
      expect(next.columns[0].records.map((r) => r.id), [_b]);
      expect(next.columns[0].count, 1);
      expect(next.columns[1].records.map((r) => r.id), [_a]);
      expect(next.columns[1].count, 1);
      expect(next.columns[2].records.map((r) => r.id), [_c]);
    });

    test('a move body names the record, value and the card it lands under', () {
      final board = ObjectBoard.fromJson(_boardJson());
      final record = board.columns.first.records.first;
      expect(boardMoveBody(record, board.columns[1]), {
        'record': _a,
        'value': 'won',
        'above_id': null,
        'below_id': null,
      });
      expect(boardMoveBody(record, board.columns[2]), {
        'record': _a,
        'value': null,
        'above_id': _c,
        'below_id': null,
      });
    });
  });

  group('object models', () {
    test('board_field is read, and absent means no board', () {
      expect(
        ObjectSummary.fromJson(const {
          'key': 'property',
          'label': 'Property',
          'board_field': 'stage',
        }).boardField,
        'stage',
      );
      expect(ObjectSummary.fromJson(const {'key': 'property'}).boardField, '');
    });

    test('a turned-off or non-dropdown board field is no board', () {
      ObjectDefinition def(String key, ObjectField f) => ObjectDefinition(
        summary: ObjectSummary(
          key: 'property',
          label: 'Property',
          labelPlural: 'Properties',
          boardField: key,
        ),
        fields: [f],
      );
      const on = ObjectField(
        key: 'stage',
        label: 'Stage',
        fieldType: 'dropdown',
      );
      expect(def('stage', on).boardField?.key, 'stage');
      expect(def('', on).boardField, isNull);
      expect(
        def(
          'stage',
          const ObjectField(
            key: 'stage',
            label: 'Stage',
            fieldType: 'dropdown',
            isActive: false,
          ),
        ).boardField,
        isNull,
      );
      expect(
        def(
          'stage',
          const ObjectField(key: 'stage', label: 'Stage', fieldType: 'text'),
        ).boardField,
        isNull,
      );
    });

    test('comments are read, and a missing list is empty', () {
      final withNotes = ObjectRecord.fromJson({
        'id': _a,
        'name': 'x',
        'comments': [
          {
            'id': 'n1',
            'comment': 'hello',
            'commented_on': '2026-10-27T10:00:00Z',
            'commented_by': {
              'id': 'p1',
              'user_details': {'email': 'a@example.com'},
            },
          },
        ],
      });
      expect(withNotes.comments.single.comment, 'hello');
      expect(withNotes.comments.single.commentedByEmail, 'a@example.com');
      expect(ObjectRecord.fromJson({'id': _a, 'name': 'x'}).comments, isEmpty);
    });
  });

  group('note rules', () {
    test('only the author edits, case-insensitively', () {
      expect(canEditNote(_note('Me@Example.com'), 'me@example.com'), isTrue);
      expect(
        canEditNote(_note('other@example.com'), 'me@example.com'),
        isFalse,
      );
      expect(canEditNote(_note('me@example.com'), null), isFalse);
    });

    test('the author or an admin deletes', () {
      final mine = _note('me@example.com');
      final theirs = _note('other@example.com');
      expect(canDeleteNote(mine, 'me@example.com', isAdmin: false), isTrue);
      expect(canDeleteNote(theirs, 'me@example.com', isAdmin: false), isFalse);
      expect(canDeleteNote(theirs, 'me@example.com', isAdmin: true), isTrue);
      expect(canEditNote(theirs, 'me@example.com'), isFalse);
    });
  });

  group('refusal messages', () {
    ApiResponse<Map<String, dynamic>> refused(
      int status,
      Map<String, dynamic> body,
    ) => ApiResponse(success: false, statusCode: status, data: body);

    test('403 details reach the user verbatim', () {
      expect(
        noteError(
          refused(403, {'detail': 'You can only edit your own notes.'}),
          'x',
        ),
        'You can only edit your own notes.',
      );
      expect(
        noteError(
          refused(403, {'detail': 'You cannot delete this note.'}),
          'x',
        ),
        'You cannot delete this note.',
      );
      expect(
        boardMoveError(
          refused(403, {'detail': 'You cannot change this record.'}),
        ),
        'You cannot change this record.',
      );
    });

    test('a 400 names the field', () {
      expect(
        noteError(
          refused(400, {
            'comment': ['This field may not be blank.'],
          }),
          'x',
        ),
        'Note: This field may not be blank.',
      );
      expect(
        boardMoveError(
          refused(400, {
            'value': ["Choose one of this field's options."],
          }),
        ),
        "Choose one of this field's options.",
      );
    });

    test('a card carries its own edit right', () {
      final board = ObjectBoard.fromJson(_boardJson());
      expect(board.columns[0].records.map((r) => r.canEdit), [true, true]);
      expect(board.columns[2].records.single.canEdit, isFalse);
    });

    test('an unreadable body falls back', () {
      expect(
        noteError(const ApiResponse(success: false, statusCode: 500), 'Oops'),
        'Oops',
      );
    });
  });

  group('settings and routes', () {
    test('the board field is its own write body, and empty clears it', () {
      expect(boardFieldBody('stage'), {'board_field': 'stage'});
      expect(boardFieldBody(''), {'board_field': ''});
      expect(
        objectWriteBody(
          creating: false,
          label: 'Property',
          labelPlural: 'Properties',
        ).containsKey('board_field'),
        isFalse,
      );
    });

    test('only active dropdown fields are choices', () {
      const fields = [
        ObjectField(key: 'stage', label: 'Stage', fieldType: 'dropdown'),
        ObjectField(
          key: 'old',
          label: 'Old',
          fieldType: 'dropdown',
          isActive: false,
        ),
        ObjectField(key: 'city', label: 'City', fieldType: 'text'),
      ];
      expect(boardChoices(fields).map((f) => f.key), ['stage']);
    });

    test('routes are checked', () {
      expect(
        ApiConfig.objectBoard('property'),
        endsWith('/objects/property/board/'),
      );
      expect(
        ApiConfig.objectBoardMove('property'),
        endsWith('/objects/property/board/move/'),
      );
      expect(
        ApiConfig.objectComment('property', _a),
        endsWith('/objects/property/comments/$_a/'),
      );
      expect(
        () => ApiConfig.objectComment('property', '../x'),
        throwsArgumentError,
      );
    });
  });
}
