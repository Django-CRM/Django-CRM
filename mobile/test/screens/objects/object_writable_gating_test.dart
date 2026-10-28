import 'package:bottle_crm/data/models/custom_object.dart';
import 'package:bottle_crm/data/models/lookup_models.dart';
import 'package:bottle_crm/providers/custom_objects_provider.dart';
import 'package:bottle_crm/providers/lookup_provider.dart';
import 'package:bottle_crm/screens/objects/object_record_detail_screen.dart';
import 'package:bottle_crm/screens/objects/object_record_form_screen.dart';
import 'package:bottle_crm/screens/objects/object_records_screen.dart';
import 'package:bottle_crm/services/api_service.dart';
import 'package:bottle_crm/widgets/objects/record_matches_card.dart';
import 'package:bottle_crm/widgets/objects/related_objects_section.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';

const _id = '11111111-1111-1111-1111-111111111111';

ObjectSummary _summary({bool? writable, bool matches = false}) => ObjectSummary(
  key: 'property',
  label: 'Property',
  labelPlural: 'Properties',
  writable: writable,
  matches: matches,
);

class _Objects extends CustomObjectsNotifier {
  _Objects(this.answer);
  final CustomObjectsAnswer answer;

  @override
  Future<CustomObjectsAnswer?> build() async => answer;
}

class _Records extends ObjectRecordsNotifier {
  _Records(super.key);

  @override
  Future<ObjectRecordsData> build() async => const ObjectRecordsData(
    records: [ObjectRecord(id: _id, name: 'Harbour View')],
    count: 1,
  );
}

class _Api extends CustomObjectsApi {
  @override
  Future<ApiResponse<Map<String, dynamic>>> fetchRecord(
    String key,
    String id,
  ) async => const ApiResponse(
    success: true,
    statusCode: 200,
    data: {
      'id': _id,
      'name': 'Harbour View',
      'can_edit': true,
      'can_delete': true,
    },
  );

  @override
  Future<ApiResponse<Map<String, dynamic>>> fetchMatches(
    String id, {
    bool dismissed = false,
  }) async => const ApiResponse(
    success: true,
    statusCode: 200,
    data: {
      'side': 'property',
      'object': {
        'key': 'requirement',
        'label': 'Requirement',
        'label_plural': 'Requirements',
      },
      'matches': [],
      'dismissed_count': 0,
    },
  );
}

/// [orgWrites] is the org-wide flag, [writable] the object's own.
Widget _app(
  Widget home, {
  required bool orgWrites,
  bool? writable,
  bool matches = false,
}) {
  final summary = _summary(writable: writable, matches: matches);
  return ProviderScope(
    key: UniqueKey(),
    overrides: [
      customObjectsProvider.overrideWith(
        () => _Objects(
          CustomObjectsAnswer(hasCustomObjects: orgWrites, objects: [summary]),
        ),
      ),
      objectDefinitionProvider.overrideWith(
        (ref, key) async => ObjectDefinition(summary: summary),
      ),
      objectRecordsProvider(
        'property',
      ).overrideWith(() => _Records('property')),
      customObjectsApiProvider.overrideWithValue(_Api()),
      usersProvider.overrideWithValue(const <UserLookup>[]),
    ],
    child: MaterialApp.router(
      routerConfig: GoRouter(
        routes: [GoRoute(path: '/', builder: (_, _) => home)],
      ),
    ),
  );
}

Future<void> _show(WidgetTester tester, Widget app) async {
  tester.view.devicePixelRatio = 3.0;
  tester.view.physicalSize = const Size(390 * 3, 844 * 3);
  addTearDown(tester.view.reset);
  await tester.pumpWidget(app);
  await tester.pumpAndSettle();
}

const _list = ObjectRecordsScreen(objectKey: 'property');
const _detail = ObjectRecordDetailScreen(objectKey: 'property', recordId: _id);

void main() {
  group('record list', () {
    testWidgets('writable opens New with the org flag off', (tester) async {
      await _show(tester, _app(_list, orgWrites: false, writable: true));
      expect(find.text('New Property'), findsOneWidget);
    });

    testWidgets('not writable hides New with the org flag on', (tester) async {
      await _show(tester, _app(_list, orgWrites: true, writable: false));
      expect(find.text('Harbour View'), findsOneWidget);
      expect(find.text('New Property'), findsNothing);
    });

    testWidgets('no flag from the server falls back to the org flag', (
      tester,
    ) async {
      await _show(tester, _app(_list, orgWrites: true));
      expect(find.text('New Property'), findsOneWidget);
    });
  });

  group('record screen', () {
    testWidgets('writable offers edit, delete, attach and notes', (
      tester,
    ) async {
      await _show(tester, _app(_detail, orgWrites: false, writable: true));
      expect(find.byTooltip('Edit'), findsOneWidget);
      expect(find.byTooltip('Delete'), findsOneWidget);
      expect(find.text('Attach a file'), findsOneWidget);
      expect(find.byTooltip('Add note'), findsOneWidget);
    });

    testWidgets('not writable offers none of them', (tester) async {
      await _show(tester, _app(_detail, orgWrites: true, writable: false));
      expect(find.text('Harbour View'), findsWidgets);
      expect(find.byTooltip('Edit'), findsNothing);
      expect(find.byTooltip('Delete'), findsNothing);
      expect(find.text('Attach a file'), findsNothing);
      expect(find.byTooltip('Add note'), findsNothing);
    });

    testWidgets('the matches card appears only when the object has matches', (
      tester,
    ) async {
      await _show(tester, _app(_detail, orgWrites: false, writable: true));
      expect(find.byType(RecordMatchesCard), findsNothing);

      await _show(
        tester,
        _app(_detail, orgWrites: false, writable: true, matches: true),
      );
      expect(find.byType(RecordMatchesCard), findsOneWidget);
      expect(find.text('Matching requirements'), findsOneWidget);
    });
  });

  group('record form', () {
    const form = ObjectRecordFormScreen(objectKey: 'property');

    testWidgets('writable opens the form with the org flag off', (
      tester,
    ) async {
      await _show(tester, _app(form, orgWrites: false, writable: true));
      expect(find.text('New Property'), findsOneWidget);
      expect(find.text('Save'), findsOneWidget);
    });

    testWidgets('not writable refuses with the org flag on', (tester) async {
      await _show(tester, _app(form, orgWrites: true, writable: false));
      expect(find.text(objectsTurnedOffNotice), findsOneWidget);
      expect(find.text('Save'), findsNothing);
    });
  });

  group('related add', () {
    test('an object the org may not write to is not offered', () {
      expect(canWriteObject(_summary(writable: false), null), isFalse);
      expect(
        canWriteObject(_summary(writable: true), const CustomObjectsAnswer()),
        isTrue,
      );
      expect(
        canWriteObject(
          _summary(),
          const CustomObjectsAnswer(hasCustomObjects: true),
        ),
        isTrue,
      );
      expect(canWriteObject(_summary(), null), isFalse);
    });

    test('the section is exported for the screens that embed it', () {
      expect(const RelatedObjectsSection(to: 'lead', recordId: _id), isNotNull);
    });
  });
}
