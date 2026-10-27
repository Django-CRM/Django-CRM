import 'package:bottle_crm/data/models/custom_field_definition.dart';
import 'package:bottle_crm/data/models/custom_object.dart';
import 'package:bottle_crm/providers/auth_provider.dart';
import 'package:bottle_crm/providers/custom_objects_provider.dart';
import 'package:bottle_crm/screens/settings/object_settings_screen.dart';
import 'package:bottle_crm/screens/settings/objects_settings_screen.dart';
import 'package:bottle_crm/services/api_service.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

class _FakeObjects extends CustomObjectsNotifier {
  _FakeObjects(this.answer, [this.onBuild]);

  final CustomObjectsAnswer? answer;
  final void Function(String name)? onBuild;

  @override
  Future<CustomObjectsAnswer?> build() async {
    onBuild?.call('objects');
    return answer;
  }
}

class _FailingObjects extends CustomObjectsNotifier {
  _FailingObjects([this.onBuild]);

  final void Function()? onBuild;

  @override
  Future<CustomObjectsAnswer?> build() async {
    onBuild?.call();
    throw Exception('502');
  }
}

const _active = ObjectSummary(
  key: 'property',
  label: 'Property',
  labelPlural: 'Properties',
);
const _archived = ObjectSummary(
  key: 'vehicle',
  label: 'Vehicle',
  labelPlural: 'Vehicles',
  isActive: false,
);

const _fields = [
  ObjectField(key: 'city', label: 'City', fieldType: 'text'),
  ObjectField(
    key: 'owner',
    label: 'Owner',
    fieldType: 'link',
    target: 'contact',
    many: false,
  ),
  ObjectField(
    key: 'old',
    label: 'Old note',
    fieldType: 'text',
    isActive: false,
  ),
];

CustomObjectsAnswer _answer({required bool writes}) => CustomObjectsAnswer(
  hasCustomObjects: writes,
  objects: const [_active, _archived],
  linkTargets: const [
    LinkTarget(key: 'contact', label: 'Contact', labelPlural: 'Contacts'),
  ],
);

class _Call {
  _Call(this.name, this.args);

  final String name;
  final List<Object?> args;

  @override
  String toString() => '$name($args)';
}

class _FakeApi extends CustomObjectsApi {
  _FakeApi({this.deleteResponse});

  final calls = <_Call>[];
  ApiResponse<Map<String, dynamic>>? deleteResponse;

  ApiResponse<Map<String, dynamic>> get _ok => const ApiResponse(
    success: true,
    data: <String, dynamic>{},
    statusCode: 200,
  );

  @override
  Future<ApiResponse<Map<String, dynamic>>> deleteObject(String key) async {
    calls.add(_Call('deleteObject', [key]));
    return deleteResponse ?? _ok;
  }

  @override
  Future<ApiResponse<Map<String, dynamic>>> updateObject(
    String key,
    Map<String, dynamic> body,
  ) async {
    calls.add(_Call('updateObject', [key, body]));
    return _ok;
  }

  @override
  Future<ApiResponse<Map<String, dynamic>>> createField(
    String key,
    Map<String, dynamic> body,
  ) async {
    calls.add(_Call('createField', [key, body]));
    return _ok;
  }

  @override
  Future<ApiResponse<Map<String, dynamic>>> updateField(
    String key,
    String fieldKey,
    Map<String, dynamic> body,
  ) async {
    calls.add(_Call('updateField', [key, fieldKey, body]));
    return _ok;
  }
}

Widget _app({
  required bool admin,
  required bool writes,
  required Widget home,
  ObjectSummary summary = _active,
  List<ObjectField> fields = _fields,
  _FakeApi? api,
  void Function(String name)? onBuild,
}) {
  final answer = _answer(writes: writes);
  return ProviderScope(
    key: UniqueKey(),
    overrides: [
      isOrgAdminProvider.overrideWithValue(admin),
      if (api != null) customObjectsApiProvider.overrideWithValue(api),
      customObjectsProvider.overrideWith(() => _FakeObjects(answer, onBuild)),
      objectsSettingsProvider.overrideWith((ref) async {
        onBuild?.call('settingsList');
        return answer;
      }),
      objectSettingsProvider(summary.key).overrideWith((ref) async {
        onBuild?.call('settings');
        return ObjectSettingsData(summary: summary, fields: fields);
      }),
    ],
    child: MaterialApp(home: home),
  );
}

void main() {
  void tall(WidgetTester tester) {
    tester.view.physicalSize = const Size(800, 1600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
  }

  testWidgets('a member sees no New object, edit or archive controls', (
    tester,
  ) async {
    tall(tester);
    await tester.pumpWidget(
      _app(admin: false, writes: true, home: const ObjectsSettingsScreen()),
    );
    await tester.pumpAndSettle();
    expect(find.text('Properties'), findsOneWidget);
    expect(find.text('Vehicles'), findsOneWidget);
    expect(find.text('Archived'), findsOneWidget);
    expect(find.byType(FloatingActionButton), findsNothing);

    await tester.pumpWidget(
      _app(
        admin: false,
        writes: true,
        home: const ObjectSettingsScreen(objectKey: 'property'),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('City'), findsOneWidget);
    expect(find.text('Old note'), findsOneWidget);
    for (final label in [
      'Edit object',
      'Archive',
      'Restore',
      'Delete for good',
      'Add field',
      'Edit',
      'Turn off',
      'Turn on',
    ]) {
      expect(find.text(label), findsNothing, reason: label);
    }
    expect(
      find.text('Creating and editing is turned off for this organization.'),
      findsNothing,
    );
  });

  testWidgets('an admin with writes off can only archive, restore and delete', (
    tester,
  ) async {
    tall(tester);
    await tester.pumpWidget(
      _app(admin: true, writes: false, home: const ObjectsSettingsScreen()),
    );
    await tester.pumpAndSettle();
    expect(find.byType(FloatingActionButton), findsNothing);
    expect(
      find.text('Creating and editing is turned off for this organization.'),
      findsOneWidget,
    );

    await tester.pumpWidget(
      _app(
        admin: true,
        writes: false,
        home: const ObjectSettingsScreen(objectKey: 'property'),
      ),
    );
    await tester.pumpAndSettle();
    expect(
      find.text('Creating and editing is turned off for this organization.'),
      findsOneWidget,
    );
    expect(find.text('Archive'), findsOneWidget);
    for (final label in [
      'Edit object',
      'Add field',
      'Edit',
      'Turn off',
      'Turn on',
      'Restore',
      'Delete for good',
    ]) {
      expect(find.text(label), findsNothing, reason: label);
    }

    await tester.pumpWidget(
      _app(
        admin: true,
        writes: false,
        summary: _archived,
        home: const ObjectSettingsScreen(objectKey: 'vehicle'),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('Restore'), findsOneWidget);
    expect(find.text('Delete for good'), findsOneWidget);
    expect(find.text('Archive'), findsNothing);
    expect(find.text('Edit object'), findsNothing);
    expect(find.text('Add field'), findsNothing);
  });

  testWidgets('an admin with writes on sees every control', (tester) async {
    tall(tester);
    await tester.pumpWidget(
      _app(
        admin: true,
        writes: true,
        home: const ObjectSettingsScreen(objectKey: 'property'),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('Edit object'), findsOneWidget);
    expect(find.text('Archive'), findsOneWidget);
    expect(find.text('Add field'), findsOneWidget);
    expect(find.text('Turn on'), findsOneWidget);
    expect(find.text('Turn off'), findsNWidgets(2));
  });

  testWidgets('an admin with writes on can start a new object', (tester) async {
    await tester.pumpWidget(
      _app(admin: true, writes: true, home: const ObjectsSettingsScreen()),
    );
    await tester.pumpAndSettle();
    expect(find.text('New object'), findsOneWidget);
    await tester.tap(find.text('New object'));
    await tester.pumpAndSettle();
    expect(find.text('Name (singular)'), findsOneWidget);
    await tester.enterText(find.byType(TextField).first, 'Real Estate');
    await tester.pump();
    expect(find.text('real_estate'), findsOneWidget);
    expect(find.text('Only the people involved'), findsOneWidget);
  });

  Future<void> pumpObject(
    WidgetTester tester, {
    ObjectSummary summary = _active,
    List<ObjectField> fields = _fields,
    _FakeApi? api,
    void Function(String name)? onBuild,
    bool watchAll = false,
  }) async {
    tall(tester);
    await tester.pumpWidget(
      _app(
        admin: true,
        writes: true,
        summary: summary,
        fields: fields,
        api: api,
        onBuild: onBuild,
        home: Stack(
          children: [
            ObjectSettingsScreen(objectKey: summary.key),
            if (watchAll)
              Consumer(
                builder: (context, ref, _) {
                  ref.watch(objectsSettingsProvider);
                  return const SizedBox.shrink();
                },
              ),
          ],
        ),
      ),
    );
    await tester.pumpAndSettle();
  }

  testWidgets('archive asks first; cancel calls nothing, confirm archives', (
    tester,
  ) async {
    final api = _FakeApi();
    await pumpObject(tester, api: api);
    await tester.tap(find.text('Archive'));
    await tester.pumpAndSettle();
    expect(find.text('Archive Properties?'), findsOneWidget);
    await tester.tap(find.text('Cancel'));
    await tester.pumpAndSettle();
    expect(api.calls, isEmpty);

    await tester.tap(find.text('Archive'));
    await tester.pumpAndSettle();
    await tester.tap(
      find.descendant(
        of: find.byType(AlertDialog),
        matching: find.text('Archive'),
      ),
    );
    await tester.pumpAndSettle();
    expect(api.calls.map((c) => c.toString()), ['deleteObject([property])']);
  });

  testWidgets('a refused delete for good shows the reason and stays', (
    tester,
  ) async {
    final api = _FakeApi(
      deleteResponse: const ApiResponse(
        success: false,
        data: {'detail': 'This object still has records.'},
        statusCode: 400,
      ),
    );
    await pumpObject(tester, summary: _archived, api: api);
    await tester.tap(find.text('Delete for good'));
    await tester.pumpAndSettle();
    await tester.tap(
      find.descendant(
        of: find.byType(AlertDialog),
        matching: find.text('Delete for good'),
      ),
    );
    await tester.pumpAndSettle();
    expect(api.calls.single.name, 'deleteObject');
    expect(find.text('This object still has records.'), findsOneWidget);
    expect(find.byType(ObjectSettingsScreen), findsOneWidget);
  });

  testWidgets('restore sends exactly is_active true and refreshes all three', (
    tester,
  ) async {
    final api = _FakeApi();
    final builds = <String>[];
    await pumpObject(
      tester,
      summary: _archived,
      api: api,
      onBuild: builds.add,
      watchAll: true,
    );
    builds.clear();
    await tester.tap(find.text('Restore'));
    await tester.pumpAndSettle();
    expect(api.calls.single.name, 'updateObject');
    expect(api.calls.single.args, [
      'vehicle',
      {'is_active': true},
    ]);
    expect(builds.toSet(), {'objects', 'settings', 'settingsList'});
  });

  testWidgets('a field edit sends no key, type, target or many', (
    tester,
  ) async {
    final api = _FakeApi();
    await pumpObject(tester, api: api);
    await tester.tap(find.text('Edit').first);
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextField).first, 'Town');
    await tester.tap(find.text('Save field'));
    await tester.pumpAndSettle();
    final call = api.calls.single;
    expect(call.name, 'updateField');
    expect(call.args.take(2), ['property', 'city']);
    final body = call.args[2] as Map;
    expect(body['label'], 'Town');
    for (final k in ['key', 'field_type', 'target', 'many']) {
      expect(body.containsKey(k), isFalse, reason: k);
    }
  });

  testWidgets('a new link field sends target and many and no options', (
    tester,
  ) async {
    final api = _FakeApi();
    await pumpObject(tester, api: api);
    await tester.tap(find.text('Add field'));
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextField).first, 'Landlord');
    await tester.tap(find.text('Text').last);
    await tester.pumpAndSettle();
    await tester.tap(find.text('Link').last);
    await tester.pumpAndSettle();
    await tester.tap(find.text('Add field').last);
    await tester.pumpAndSettle();
    final call = api.calls.single;
    expect(call.name, 'createField');
    expect(call.args[0], 'property');
    final body = call.args[1] as Map;
    expect(body['target'], 'contact');
    expect(body['many'], false);
    expect(body['field_type'], 'link');
    expect(body['key'], 'landlord');
    expect(body.containsKey('options'), isFalse);
    expect(body.containsKey('is_required'), isFalse);
  });

  testWidgets('dropdown options keep their stored values through an edit', (
    tester,
  ) async {
    final api = _FakeApi();
    await pumpObject(
      tester,
      api: api,
      fields: const [
        ObjectField(
          key: 'stage',
          label: 'Stage',
          fieldType: 'dropdown',
          options: [
            CustomFieldOption(value: 'hot', label: 'Hot'),
            CustomFieldOption(value: 'c1', label: 'Cold'),
          ],
        ),
      ],
    );
    await tester.tap(find.text('Edit'));
    await tester.pumpAndSettle();
    await tester.enterText(find.widgetWithText(TextField, 'Cold'), 'Chilly');
    await tester.tap(find.text('Save field'));
    await tester.pumpAndSettle();
    expect((api.calls.single.args[2] as Map)['options'], [
      {'value': 'hot', 'label': 'Hot'},
      {'value': 'c1', 'label': 'Chilly'},
    ]);
  });

  testWidgets('an answer that could not be loaded is not "writes off"', (
    tester,
  ) async {
    tall(tester);
    await tester.pumpWidget(
      ProviderScope(
        key: UniqueKey(),
        overrides: [
          isOrgAdminProvider.overrideWithValue(true),
          customObjectsProvider.overrideWith(_FailingObjects.new),
          objectSettingsProvider('property').overrideWith(
            (ref) async =>
                const ObjectSettingsData(summary: _active, fields: _fields),
          ),
        ],
        child: const MaterialApp(
          home: ObjectSettingsScreen(objectKey: 'property'),
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(
      find.text('Custom objects could not be loaded. Try again.'),
      findsOneWidget,
    );
    expect(
      find.text('Creating and editing is turned off for this organization.'),
      findsNothing,
    );
  });

  testWidgets('the could-not-load sentence has a Retry that asks again', (
    tester,
  ) async {
    tall(tester);
    var builds = 0;
    await tester.pumpWidget(
      ProviderScope(
        key: UniqueKey(),
        overrides: [
          isOrgAdminProvider.overrideWithValue(true),
          customObjectsProvider.overrideWith(
            () => _FailingObjects(() => builds++),
          ),
          objectSettingsProvider('property').overrideWith(
            (ref) async =>
                const ObjectSettingsData(summary: _active, fields: _fields),
          ),
        ],
        child: const MaterialApp(
          home: ObjectSettingsScreen(objectKey: 'property'),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(builds, 1);

    final retry = find.widgetWithText(TextButton, 'Retry');
    expect(tester.getSize(retry).height, greaterThanOrEqualTo(44));
    await tester.tap(retry);
    await tester.pumpAndSettle();

    expect(builds, 2);
    expect(
      find.text('Custom objects could not be loaded. Try again.'),
      findsOneWidget,
    );
  });

  group('the board field', () {
    const stage = ObjectField(
      key: 'stage',
      label: 'Stage',
      fieldType: 'dropdown',
    );
    const withBoard = ObjectSummary(
      key: 'property',
      label: 'Property',
      labelPlural: 'Properties',
      boardField: 'stage',
    );

    testWidgets('an admin picks a dropdown field and it is saved alone', (
      tester,
    ) async {
      tall(tester);
      final api = _FakeApi();
      await tester.pumpWidget(
        _app(
          admin: true,
          writes: true,
          api: api,
          fields: const [..._fields, stage],
          home: const ObjectSettingsScreen(objectKey: 'property'),
        ),
      );
      await tester.pumpAndSettle();
      expect(find.text('No board'), findsOneWidget);
      await tester.tap(find.widgetWithText(OutlinedButton, 'Board field'));
      await tester.pumpAndSettle();
      expect(find.widgetWithText(ListTile, 'City'), findsNothing);
      await tester.tap(find.widgetWithText(ListTile, 'Stage'));
      await tester.pumpAndSettle();
      expect(api.calls.single.name, 'updateObject');
      expect(api.calls.single.args, [
        'property',
        {'board_field': 'stage'},
      ]);
    });

    testWidgets('a member sees the setting but not the picker', (tester) async {
      tall(tester);
      await tester.pumpWidget(
        _app(
          admin: false,
          writes: true,
          summary: withBoard,
          fields: const [..._fields, stage],
          home: const ObjectSettingsScreen(objectKey: 'property'),
        ),
      );
      await tester.pumpAndSettle();
      expect(find.text('Board field'), findsOneWidget);
      expect(find.widgetWithText(OutlinedButton, 'Board field'), findsNothing);
    });

    testWidgets('choosing No board clears it', (tester) async {
      tall(tester);
      final api = _FakeApi();
      await tester.pumpWidget(
        _app(
          admin: true,
          writes: true,
          api: api,
          summary: withBoard,
          fields: const [..._fields, stage],
          home: const ObjectSettingsScreen(objectKey: 'property'),
        ),
      );
      await tester.pumpAndSettle();
      await tester.tap(find.widgetWithText(OutlinedButton, 'Board field'));
      await tester.pumpAndSettle();
      await tester.tap(find.widgetWithText(ListTile, 'No board'));
      await tester.pumpAndSettle();
      expect(api.calls.single.args, [
        'property',
        {'board_field': ''},
      ]);
    });
  });
}
