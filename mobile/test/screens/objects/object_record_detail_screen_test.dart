import 'dart:typed_data';

import 'package:bottle_crm/data/models/custom_field_definition.dart';
import 'package:bottle_crm/data/models/custom_object.dart';
import 'package:bottle_crm/providers/custom_objects_provider.dart';
import 'package:bottle_crm/routes/app_router.dart';
import 'package:bottle_crm/screens/objects/object_record_detail_screen.dart';
import 'package:bottle_crm/services/api_service.dart';
import 'package:bottle_crm/widgets/common/attachment_tile.dart';
import 'package:bottle_crm/widgets/objects/photo_gallery.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';

const _key = 'property';
const _id = '11111111-1111-1111-1111-111111111111';
const _photoId = '22222222-2222-2222-2222-222222222222';
const _svgId = '33333333-3333-3333-3333-333333333333';
const _pdfId = '44444444-4444-4444-4444-444444444444';
const _leadId = '55555555-5555-5555-5555-555555555555';

const _definition = ObjectDefinition(
  summary: ObjectSummary(
    key: _key,
    label: 'Property',
    labelPlural: 'Properties',
    nameLabel: 'Address',
    writable: true,
  ),
  fields: [
    ObjectField(
      key: 'beds',
      label: 'Beds',
      fieldType: 'number',
      displayOrder: 1,
    ),
    ObjectField(
      key: 'listed',
      label: 'Listed',
      fieldType: 'checkbox',
      displayOrder: 2,
    ),
    ObjectField(
      key: 'kind',
      label: 'Kind',
      fieldType: 'dropdown',
      displayOrder: 3,
      options: [CustomFieldOption(value: 'flat', label: 'Flat')],
    ),
    ObjectField(
      key: 'old',
      label: 'Old kind',
      fieldType: 'dropdown',
      displayOrder: 4,
      options: [CustomFieldOption(value: 'flat', label: 'Flat')],
    ),
    ObjectField(
      key: 'notes',
      label: 'Notes',
      fieldType: 'text',
      displayOrder: 5,
    ),
    ObjectField(
      key: 'agent',
      label: 'Agent',
      fieldType: 'link',
      displayOrder: 6,
      target: 'lead',
      many: true,
    ),
  ],
);

Map<String, dynamic> _json({
  bool canEdit = false,
  bool canDelete = false,
  int hidden = 2,
  List<Map<String, dynamic>>? attachments,
}) => {
  'id': _id,
  'name': '12 Harbour Road',
  'custom_fields': {'beds': 3, 'listed': false, 'kind': 'flat', 'old': 'villa'},
  'assigned_to': [
    {'id': 'p1', 'name': 'Ravi', 'email': 'ravi@example.com'},
  ],
  'created_by': {'id': 'p2', 'name': 'Meera', 'email': 'meera@example.com'},
  'links': {
    'agent': {
      'many': true,
      'records': [
        {'id': _leadId, 'object': 'lead', 'name': 'Asha'},
      ],
      'hidden': hidden,
    },
  },
  'can_edit': canEdit,
  'can_delete': canDelete,
  'attachments': attachments ?? const [],
};

Map<String, dynamic> _att(String id, String name, {bool image = true}) => {
  'id': id,
  'file_name': name,
  'is_image': image,
  'can_delete': false,
  'download_url': '/api/attachments/$id/download/',
};

class _FakeApi extends CustomObjectsApi {
  _FakeApi(this.answer, {this.deleted});

  final ApiResponse<Map<String, dynamic>> answer;
  final List<String>? deleted;
  int fetches = 0;

  @override
  Future<ApiResponse<Map<String, dynamic>>> fetchRecord(
    String key,
    String id,
  ) async {
    fetches++;
    return answer;
  }

  @override
  Future<ApiResponse<Map<String, dynamic>>> deleteRecord(
    String key,
    String id,
  ) async {
    deleted?.add(id);
    return const ApiResponse(success: true, statusCode: 204);
  }
}

ApiResponse<Map<String, dynamic>> _ok(Map<String, dynamic> body) =>
    ApiResponse(success: true, data: body, statusCode: 200);

Widget _app(
  ApiResponse<Map<String, dynamic>> answer, {
  List<String>? deleted,
  List<String>? pushed,
  ObjectDefinition? definition = _definition,
  _FakeApi? api,
  List<String>? requestedBytes,
}) {
  final router = GoRouter(
    routes: [
      GoRoute(
        path: '/',
        builder: (context, _) => Scaffold(
          body: TextButton(
            onPressed: () => context.push('/detail'),
            child: const Text('open'),
          ),
        ),
      ),
      GoRoute(
        path: '/detail',
        builder: (_, _) =>
            const ObjectRecordDetailScreen(objectKey: _key, recordId: _id),
      ),
      GoRoute(
        path: AppRoutes.objectRecordEdit(_key, _id),
        builder: (context, _) => Scaffold(
          body: TextButton(
            onPressed: () => context.pop(),
            child: const Text('save'),
          ),
        ),
      ),
      GoRoute(
        path: linkedRecordRoute('lead', _leadId)!,
        builder: (_, _) {
          pushed?.add('lead');
          return const Scaffold(body: Text('lead screen'));
        },
      ),
    ],
  );
  return ProviderScope(
    key: UniqueKey(),
    overrides: [
      objectDefinitionProvider.overrideWith((ref, key) async => definition),
      customObjectsApiProvider.overrideWithValue(
        api ?? _FakeApi(answer, deleted: deleted),
      ),
      attachmentBytesProvider.overrideWith((ref, id) async {
        requestedBytes?.add(id);
        return null;
      }),
    ],
    child: MaterialApp.router(routerConfig: router),
  );
}

Future<void> _open(WidgetTester tester, Widget app) async {
  tester.view.devicePixelRatio = 3.0;
  tester.view.physicalSize = const Size(390 * 3, 844 * 3);
  addTearDown(tester.view.reset);
  await tester.pumpWidget(app);
  await tester.pumpAndSettle();
  await tester.tap(find.text('open'));
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('counts hidden links without naming them', (tester) async {
    final body = _json();
    (body['links'] as Map)['agent']['hidden_records'] = [
      {'id': 'secret-id', 'name': 'Secret Person'},
    ];
    await _open(tester, _app(_ok(body)));

    expect(find.text('Asha'), findsOneWidget);
    expect(find.text("and 2 you can't open"), findsOneWidget);
    expect(find.textContaining('Secret'), findsNothing);
    expect(find.textContaining('secret-id'), findsNothing);
  });

  testWidgets('shows custom fields by the web rules', (tester) async {
    await _open(tester, _app(_ok(_json())));

    expect(find.text('Address'), findsOneWidget);
    expect(find.text('12 Harbour Road'), findsOneWidget);
    expect(find.text('3'), findsOneWidget);
    expect(find.text('No'), findsOneWidget);
    expect(find.text('Flat'), findsOneWidget);
    expect(find.text('villa'), findsOneWidget);
    expect(find.text('None'), findsOneWidget);
    expect(find.text('Ravi'), findsOneWidget);
    expect(find.text('Meera'), findsOneWidget);
  });

  testWidgets('offers no edit, delete or upload without the flags', (
    tester,
  ) async {
    await _open(
      tester,
      _app(
        _ok(
          _json(
            attachments: [
              _att(_photoId, 'front.jpg'),
              _att(_pdfId, 'deed.pdf', image: false),
            ],
          ),
        ),
      ),
    );

    expect(find.byTooltip('Edit'), findsNothing);
    expect(find.byTooltip('Delete'), findsNothing);
    expect(find.text('Attach a file'), findsNothing);
    expect(find.byTooltip('Delete file'), findsNothing);
  });

  testWidgets('offers edit, delete and upload with the flags', (tester) async {
    await _open(tester, _app(_ok(_json(canEdit: true, canDelete: true))));

    expect(find.byTooltip('Edit'), findsOneWidget);
    expect(find.byTooltip('Delete'), findsOneWidget);
    expect(find.text('Attach a file'), findsOneWidget);
  });

  testWidgets('delete confirms, deletes and goes back', (tester) async {
    final deleted = <String>[];
    await _open(tester, _app(_ok(_json(canDelete: true)), deleted: deleted));

    await tester.tap(find.byTooltip('Delete'));
    await tester.pumpAndSettle();
    expect(deleted, isEmpty);
    await tester.tap(find.text('Cancel'));
    await tester.pumpAndSettle();
    expect(deleted, isEmpty);

    await tester.tap(find.byTooltip('Delete'));
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(TextButton, 'Delete'));
    await tester.pumpAndSettle();
    expect(deleted, [_id]);
    expect(find.text('open'), findsOneWidget);
  });

  testWidgets('lists an SVG as a file, not a photo', (tester) async {
    await _open(
      tester,
      _app(_ok(_json(attachments: [_att(_svgId, 'logo.svg')]))),
    );

    expect(find.byType(PhotoGallery), findsNothing);
    expect(find.byType(AttachmentTile), findsOneWidget);
    expect(find.text('logo.svg'), findsOneWidget);
  });

  testWidgets('puts a jpg in the gallery and a pdf in the files', (
    tester,
  ) async {
    await _open(
      tester,
      _app(
        _ok(
          _json(
            attachments: [
              _att(_photoId, 'front.jpg'),
              _att(_pdfId, 'deed.pdf', image: false),
            ],
          ),
        ),
      ),
    );

    expect(find.byType(PhotoGallery), findsOneWidget);
    expect(find.byType(AttachmentTile), findsOneWidget);
    expect(find.text('deed.pdf'), findsOneWidget);
  });

  testWidgets('says so for a record it cannot open', (tester) async {
    await _open(
      tester,
      _app(const ApiResponse(success: false, message: 'x', statusCode: 404)),
    );

    expect(
      find.text('This record does not exist, or you do not have access to it.'),
      findsOneWidget,
    );
    expect(find.text('Retry'), findsNothing);
  });

  testWidgets('says so when the object is missing', (tester) async {
    await _open(tester, _app(_ok(_json()), definition: null));

    expect(
      find.text('This record does not exist, or you do not have access to it.'),
      findsOneWidget,
    );
  });

  testWidgets('a server error offers retry, not not-found', (tester) async {
    await _open(
      tester,
      _app(const ApiResponse(success: false, message: 'boom', statusCode: 500)),
    );

    expect(find.text('Retry'), findsOneWidget);
    expect(find.textContaining('have access to it'), findsNothing);
  });

  testWidgets('opens a linked lead', (tester) async {
    final pushed = <String>[];
    await _open(tester, _app(_ok(_json()), pushed: pushed));

    await tester.tap(find.text('Asha'));
    await tester.pumpAndSettle();
    expect(pushed, ['lead']);
    expect(find.text('lead screen'), findsOneWidget);
  });

  for (final scale in [1.0, 1.3, 1.5]) {
    testWidgets('fits at 390px with text scaled $scale', (tester) async {
      tester.platformDispatcher.textScaleFactorTestValue = scale;
      addTearDown(tester.platformDispatcher.clearTextScaleFactorTestValue);
      await _open(
        tester,
        _app(
          _ok(
            _json(
              canEdit: true,
              canDelete: true,
              attachments: [
                _att(_photoId, 'front.jpg'),
                _att(_svgId, 'a-rather-long-file-name-for-a-vector-logo.svg'),
              ],
            ),
          ),
        ),
      );
      expect(tester.takeException(), isNull);
    });
  }

  testWidgets('fits on a tablet with text scaled 1.3', (tester) async {
    tester.platformDispatcher.textScaleFactorTestValue = 1.3;
    addTearDown(tester.platformDispatcher.clearTextScaleFactorTestValue);
    await tester.pumpWidget(
      _app(_ok(_json(canEdit: true, attachments: [_att(_photoId, 'a.jpg')]))),
    );
    tester.view.devicePixelRatio = 1.0;
    tester.view.physicalSize = const Size(834, 1112);
    addTearDown(tester.view.reset);
    await tester.pumpAndSettle();
    await tester.tap(find.text('open'));
    await tester.pumpAndSettle();
    expect(tester.takeException(), isNull);
    expect(find.byType(PhotoGallery), findsOneWidget);
  });

  testWidgets('the gallery fits three columns at 390px', (tester) async {
    tester.view.devicePixelRatio = 3.0;
    tester.view.physicalSize = const Size(390 * 3, 844 * 3);
    addTearDown(tester.view.reset);
    final photos = [
      for (var i = 0; i < 4; i++)
        RecordAttachment(
          id: '2222222$i-2222-2222-2222-222222222222',
          fileName: 'p$i.jpg',
          isImage: true,
        ),
    ];
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          attachmentBytesProvider.overrideWith((ref, id) async => Uint8List(0)),
        ],
        child: MaterialApp(
          home: Scaffold(
            body: CustomScrollView(
              slivers: [
                SliverPadding(
                  padding: const EdgeInsets.all(16),
                  sliver: PhotoGallery(photos: photos),
                ),
              ],
            ),
          ),
        ),
      ),
    );
    await tester.pump();
    expect(tester.takeException(), isNull);
    final tiles = find.byKey(const ValueKey('photo-tile'));
    expect(tiles, findsNWidgets(4));
    final first = tester.getTopLeft(tiles.at(0));
    final third = tester.getTopLeft(tiles.at(2));
    final fourth = tester.getTopLeft(tiles.at(3));
    expect(third.dy, first.dy);
    expect(fourth.dy, greaterThan(first.dy));
  });

  testWidgets('loads only the photos near the viewport', (tester) async {
    final requested = <String>[];
    final photos = [
      for (var i = 0; i < 30; i++)
        _att(
          '${i.toString().padLeft(8, '0')}-2222-2222-2222-222222222222',
          'p$i.jpg',
        ),
    ];
    await _open(
      tester,
      _app(_ok(_json(attachments: photos)), requestedBytes: requested),
    );

    expect(requested, isNotEmpty);
    expect(requested.length, lessThan(30));
  });

  testWidgets('returning from edit refetches the record', (tester) async {
    final api = _FakeApi(_ok(_json(canEdit: true)));
    await _open(tester, _app(_ok(_json(canEdit: true)), api: api));
    expect(api.fetches, 1);

    await tester.tap(find.byTooltip('Edit'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('save'));
    await tester.pumpAndSettle();
    expect(api.fetches, 2);
  });
}
