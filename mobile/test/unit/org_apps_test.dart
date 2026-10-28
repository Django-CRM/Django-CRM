import 'dart:convert';

import 'package:bottle_crm/data/models/auth_response.dart';
import 'package:bottle_crm/data/models/org_app.dart';
import 'package:bottle_crm/providers/auth_provider.dart';
import 'package:bottle_crm/providers/org_apps_provider.dart';
import 'package:bottle_crm/services/api_service.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import '../support/apps_fixtures.dart';

Map<String, dynamic> _app(String status, {Object? installed}) => {
  'id': 'real-estate',
  'name': 'Real Estate',
  'description': 'Brokers and agencies.',
  'version': 2,
  'status': status,
  'installed_version': installed,
  'update_available': false,
  'skipped_modules': [],
  'installed_at': null,
  'installed_by_details': null,
};

class _OrgSwitch extends Notifier<Organization?> {
  @override
  Organization? build() => Organization.fromJson({'id': 'a', 'name': 'A'});
}

void main() {
  group('OrgApp', () {
    test('reads the contract, numbers as text, and junk as defaults', () {
      final answer = appsAnswer();
      final re = answer.apps[1];
      expect(re.id, 'real-estate');
      expect(re.version, '2');
      expect(re.installedVersion, '1');
      expect(re.status, AppStatus.installed);
      expect(re.updateAvailable, isTrue);
      expect(re.skippedModules, ['invoices']);
      expect(re.installedByName, 'Ana Admin');
      expect(re.installedAt, DateTime.utc(2026, 10, 2, 9));
      expect(answer.apps[2].status, AppStatus.removed);

      final odd = OrgApp.fromJson({
        'id': 'x',
        'status': 'weird',
        'skipped_modules': 'invoices',
        'installed_by_details': 'Ana',
      });
      expect(odd.status, AppStatus.notInstalled);
      expect(odd.skippedModules, isEmpty);
      expect(odd.installedByName, isNull);
      expect(
        OrgAppsAnswer.fromJson({
          'apps': [
            'junk',
            {'name': 'no id'},
            _app('installed'),
          ],
        }).apps.single.id,
        'real-estate',
      );
      expect(OrgAppsAnswer.fromJson({'apps': 'x'}).apps, isEmpty);
    });

    test('offers install, or update and remove, as the state allows', () {
      final apps = appsAnswer().apps;
      expect(apps[0].actions, [AppAction.install]);
      expect(apps[2].actions, [AppAction.install]);
      expect(apps[1].actions, [AppAction.upgrade, AppAction.remove]);

      final current = OrgApp.fromJson(_app('installed', installed: 2));
      expect(current.actions, [AppAction.remove]);
      // Skipped modules alone do not offer an update; the server flags it.
      final skipped = OrgApp.fromJson({
        ..._app('installed', installed: 2),
        'skipped_modules': ['invoices'],
      });
      expect(skipped.actions, [AppAction.remove]);
      final flagged = OrgApp.fromJson({
        ..._app('installed', installed: 2),
        'skipped_modules': ['invoices'],
        'update_available': true,
      });
      expect(flagged.actions, [AppAction.upgrade, AppAction.remove]);
    });

    test('withApp replaces the row of the same id only', () {
      final answer = appsAnswer();
      final next = answer.withApp(
        OrgApp.fromJson(_app('removed', installed: 1)),
      );
      expect(next.apps.map((a) => a.id), answer.apps.map((a) => a.id));
      expect(next.apps[1].status, AppStatus.removed);
      expect(next.apps[0].status, AppStatus.notInstalled);
    });
  });

  group('AppPreview', () {
    test('groups by type in a fixed order, served order within a group', () {
      final preview = previewFor(AppAction.install);
      expect(preview.groups.map((g) => g.title), [
        'Wording',
        'Fields',
        'Pipelines',
        'Stages',
        'Tags',
        'Lead sources',
        'Products',
      ]);
      expect(preview.groups[1].items.map((i) => i.label), [
        'Budget (max)',
        'Preferred location',
      ]);
      expect(preview.hasHandWork, isTrue);
      expect(preview.sampleOffered, isTrue);
      expect(preview.sampleDefaultOn, isTrue);
      expect(preview.sampleRecords, 58);
    });

    test('groups objects right after wording and labels their effects', () {
      final preview = AppPreview.fromJson({
        'items': [
          {'type': 'tag', 'label': 'VIP', 'effect': 'add'},
          {'type': 'object', 'label': 'Property', 'effect': 'add'},
          {'type': 'object', 'label': 'Requirement', 'effect': 'conflict'},
          {'type': 'object', 'label': 'Site visit', 'effect': 'restore'},
          {'type': 'wording', 'label': 'Leads are Enquiries', 'effect': 'keep'},
        ],
      }, AppAction.install);
      expect(preview.groups.map((g) => g.title), [
        'Wording',
        'Objects',
        'Tags',
      ]);
      expect(preview.groups[1].items.map((i) => i.label), [
        'Property',
        'Requirement',
        'Site visit',
      ]);
      expect(appEffectLabel('restore'), 'Comes back');
      expect(appEffectLabel('conflict'), 'Not added');
      expect(appEffectNote('conflict'), "None of the app's objects is added");
      expect(appEffectNote('add'), isNull);
    });

    test('an unknown type follows the known ones under its own name', () {
      final preview = AppPreview.fromJson({
        'items': [
          {'type': 'saved_view', 'label': 'Hot leads', 'effect': 'add'},
          {'type': 'tag', 'label': 'VIP', 'effect': 'keep'},
        ],
      }, AppAction.upgrade);
      expect(preview.action, AppAction.upgrade);
      expect(preview.groups.map((g) => g.title), ['Tags', 'Saved view']);
      expect(preview.sampleOffered, isFalse);
      expect(preview.hasHandWork, isFalse);
    });

    test('labels every effect and target the contract names', () {
      expect(
        [
          'add',
          'keep',
          'turn_off',
          'turn_on',
          'restore_wording',
          'skipped_module',
          'add_by_hand',
          'something_new',
        ].map(appEffectLabel),
        [
          'New',
          'Kept',
          'Turned off',
          'Back on',
          'Back to default',
          'Module off',
          'Add by hand',
          'Something new',
        ],
      );
      expect(appTargetLabel('lead'), 'Leads');
      expect(appTargetLabel('Case'), 'Tickets');
      expect(appTargetLabel('opportunity'), 'Deals');
      expect(appTargetLabel('widget'), 'Widget');
    });
  });

  group('orgAppsProvider', () {
    late Map<String, http.Response Function(http.Request)> routes;
    late List<http.Request> sent;
    late ProviderContainer container;

    setUp(() {
      sent = [];
      routes = {};
      ApiService().setClientForTesting(
        MockClient((request) async {
          sent.add(request);
          return routes['${request.method} ${request.url.path}']?.call(
                request,
              ) ??
              http.Response('{}', 404);
        }),
      );
      container = ProviderContainer(
        overrides: [selectedOrgProvider.overrideWith((ref) => ref.watch(_org))],
      );
    });

    tearDown(() {
      container.dispose();
      ApiService().setClientForTesting(http.Client());
    });

    void answer(String key, Object body, {int status = 200}) =>
        routes[key] = (_) => http.Response(jsonEncode(body), status);

    Future<OrgAppsAnswer?> load() {
      container.listen(orgAppsProvider, (_, _) {});
      return container.read(orgAppsProvider.future);
    }

    test('loads for the selected org', () async {
      answer('GET /api/apps/', {
        'apps': [_app('not_installed')],
      });
      final loaded = await load();
      expect(loaded!.orgId, 'a');
      expect(loaded.apps.single.name, 'Real Estate');
      expect(container.read(shownAppsProvider), isNotNull);
    });

    test('a 404 from an older server hides the apps, not the screen', () async {
      final loaded = await load();
      expect(loaded, isNull);
      expect(container.read(orgAppsProvider).hasError, isFalse);
      expect(container.read(shownAppsProvider), isNull);
    });

    test('any other failure is an error', () async {
      answer('GET /api/apps/', {}, status: 500);
      await expectLater(load(), throwsA(isA<Exception>()));
      expect(container.read(shownAppsProvider), isNull);
    });

    test('a preview asks for its action and parses the body', () async {
      routes['GET /api/apps/real-estate/preview/'] = (request) {
        expect(request.url.queryParameters['action'], 'remove');
        return http.Response(
          jsonEncode({
            'action': 'remove',
            'items': [
              {
                'type': 'field',
                'target': 'lead',
                'label': 'Budget',
                'effect': 'turn_off',
              },
            ],
            'sample_data': {'offered': false},
          }),
          200,
        );
      };
      final result = await container
          .read(orgAppsProvider.notifier)
          .preview('real-estate', AppAction.remove);
      expect(result.error, isNull);
      expect(result.preview!.items.single.effect, 'turn_off');
    });

    test('a member is told the server said no', () async {
      answer('GET /api/apps/real-estate/preview/', {
        'detail': 'You do not have permission to perform this action.',
      }, status: 403);
      final result = await container
          .read(orgAppsProvider.notifier)
          .preview('real-estate', AppAction.install);
      expect(result.preview, isNull);
      expect(result.error, contains('permission'));
    });

    test('install sends the sample choice and puts the answered app in '
        'place', () async {
      answer('GET /api/apps/', {
        'apps': [_app('not_installed')],
      });
      answer('POST /api/apps/real-estate/install/', {
        'app': _app('installed', installed: 2),
      });
      await load();

      final error = await container
          .read(orgAppsProvider.notifier)
          .install('real-estate', sampleData: false);

      expect(error, isNull);
      final post = sent.singleWhere((r) => r.method == 'POST');
      expect(jsonDecode(post.body), {'sample_data': false});
      expect(
        container.read(orgAppsProvider).value!.apps.single.status,
        AppStatus.installed,
      );
    });

    test('upgrade and remove post to their own routes and return the '
        'refusal', () async {
      answer('POST /api/apps/real-estate/upgrade/', {
        'error': 'Nothing new to add.',
      }, status: 409);
      answer('POST /api/apps/real-estate/remove/', {
        'app': _app('removed', installed: 2),
      });
      final notifier = container.read(orgAppsProvider.notifier);
      expect(await notifier.upgrade('real-estate'), 'Nothing new to add.');
      expect(await notifier.remove('real-estate'), isNull);
    });

    test('signup installs with sample data', () async {
      answer('POST /api/apps/real-estate/install/', {
        'app': _app('installed', installed: 2),
      });
      final error = await container
          .read(orgAppsProvider.notifier)
          .installAtSignup('real-estate');
      expect(error, isNull);
      final post = sent.singleWhere((r) => r.method == 'POST');
      expect(jsonDecode(post.body), {'sample_data': true});
    });

    test('signup on a server without apps applies the pack instead', () async {
      answer('POST /api/packs/real-estate/apply/', {
        'report': {'created': [], 'skipped': []},
      });
      final error = await container
          .read(orgAppsProvider.notifier)
          .installAtSignup('real-estate');
      expect(error, isNull);
      expect(sent.where((r) => r.method == 'POST').map((r) => r.url.path), [
        '/api/apps/real-estate/install/',
        '/api/packs/real-estate/apply/',
      ]);
    });

    test('clear sample data counts what it kept', () async {
      answer('DELETE /api/packs/sample-data/', {
        'deleted': 12,
        'retained_by_type': {'lead': 1, 'contact': 2},
      });
      final result = await container
          .read(orgAppsProvider.notifier)
          .clearSampleData();
      expect(result.error, isNull);
      expect(result.deleted, 12);
      expect(result.retained, 3);
    });
  });
}

final _org = NotifierProvider<_OrgSwitch, Organization?>(_OrgSwitch.new);
