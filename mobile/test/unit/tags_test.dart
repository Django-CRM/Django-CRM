import 'dart:convert';

import 'package:bottle_crm/data/models/tag.dart';
import 'package:bottle_crm/providers/lookup_provider.dart';
import 'package:bottle_crm/providers/settings_provider.dart';
import 'package:bottle_crm/services/api_service.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;

/// Tags, the labels shared across every record type.
///
/// The things worth pinning:
///
/// - Usage is summed over whatever keys the server sends. `_TAGGABLE` held
///   four of seven models once, and a tag in real use on contacts reported as
///   unused, which is the one number an admin reads before turning a tag off.
/// - Duplicate detection runs over ACTIVE tags only. That filter is what makes
///   the two invariants `TagsMergeView` enforces hold on this side: the
///   destination is never archived and never the source.
/// - Turning a tag off is a soft archive and the copy has to say so. Merging is
///   the one action here that cannot be undone by pressing the other button.
class _FakeClient extends http.BaseClient {
  int status = 200;
  String body = '{}';

  /// Responses in order, for the calls where a write and the refresh that
  /// follows it must answer differently. Falls back to [status] / [body].
  final List<({int status, String body})> queue = [];

  final List<http.BaseRequest> sent = [];
  final List<String> bodies = [];

  @override
  Future<http.StreamedResponse> send(http.BaseRequest request) async {
    sent.add(request);
    final bytes = await request.finalize().toBytes();
    bodies.add(utf8.decode(bytes));
    final next = queue.isNotEmpty
        ? queue.removeAt(0)
        : (status: status, body: body);
    return http.StreamedResponse(
      Stream.value(utf8.encode(next.body)),
      next.status,
      request: request,
    );
  }
}

Tag _tag({
  String id = 't1',
  String name = 'Renewal',
  bool isActive = true,
  Map<String, int> usage = const {},
  String description = '',
}) {
  return Tag(
    id: id,
    name: name,
    slug: name.toLowerCase(),
    isActive: isActive,
    usage: usage,
    description: description,
  );
}

void main() {
  group('usage', () {
    test('sums every key the server sent, including one it did not know', () {
      // The list of taggable models lives on the server and has grown before.
      // A client that names the keys itself under-reports the moment it does
      // again, and under-reporting reads as "safe to turn off".
      final tag = _tag(usage: {'leads': 3, 'cases': 2, 'something_new': 4});
      expect(tag.used, 9);
    });

    test('is zero when the block is missing entirely', () {
      expect(Tag.fromJson({'id': 't1', 'name': 'Renewal'}).used, 0);
    });

    test('the breakdown drops the zeroes and ranks by count', () {
      final tag = _tag(
        usage: {'leads': 3, 'accounts': 0, 'cases': 9, 'tasks': 1},
      );
      expect(tag.usageBreakdown.map((e) => e.key), ['cases', 'leads', 'tasks']);
    });

    test('the summary uses the words the rest of the app uses', () {
      // "opportunities" and "cases" are the API's names for them. Nobody in
      // this app calls a deal an opportunity.
      final tag = _tag(usage: {'opportunities': 4, 'cases': 2});
      expect(tagUsageSummary(tag), '4 deals, 2 tickets');
    });

    test('the summary says one record, not one records', () {
      // Equal counts fall back to the label, so "API key" leads "lead".
      final tag = _tag(usage: {'leads': 1, 'api_settings': 1});
      expect(tagUsageSummary(tag), '1 API key, 1 lead');
    });

    test('the parts always add up to the total beside them', () {
      // Not a top-few list. The web's table has a column per model and a Total
      // that includes a model it has no column for, so its parts can come up
      // short of its own total.
      final tag = _tag(
        usage: {'leads': 3, 'cases': 2, 'api_settings': 1, 'tasks': 0},
      );
      final summed = tag.usageBreakdown.fold(0, (sum, e) => sum + e.value);
      expect(summed, tag.used);
      expect(tagUsageSummary(tag), '3 leads, 2 tickets, 1 API key');
    });
  });

  group('normalising a name', () {
    test('ignores case, spacing and punctuation', () {
      expect(normalizeTagName('Follow Up'), normalizeTagName('follow-up'));
    });

    test('ignores a trailing plural', () {
      expect(normalizeTagName('Renewal'), normalizeTagName('Renewals'));
    });

    test('keeps genuinely different names apart', () {
      expect(normalizeTagName('Renewal'), isNot(normalizeTagName('Refund')));
    });

    test('keeps letters in any script', () {
      // Only a-z and 0-9 used to survive, which reduced every one of these to "".
      expect(normalizeTagName('日本'), '日本');
      expect(normalizeTagName('Привет Мир'), 'приветмир');
      expect(normalizeTagName('किला'), isNot(normalizeTagName('कल')));
    });
  });

  group('duplicate groups', () {
    test('pairs two names that mean the same thing', () {
      final groups = duplicateTagGroups([
        _tag(id: 'a', name: 'Renewal', usage: {'leads': 2}),
        _tag(id: 'b', name: 'Renewals', usage: {'leads': 9}),
      ]);
      expect(groups.single.all.map((t) => t.id), ['b', 'a']);
    });

    test('the tag the org already voted for is the one kept', () {
      final groups = duplicateTagGroups([
        _tag(id: 'small', name: 'Renewal', usage: {'leads': 2}),
        _tag(id: 'big', name: 'Renewals', usage: {'leads': 9}),
      ]);
      expect(groups.single.keep.id, 'big');
      expect(groups.single.merge.map((t) => t.id), ['small']);
    });

    test('two different non-Latin names are not a duplicate', () {
      expect(
        duplicateTagGroups([
          _tag(id: 'a', name: '日本'),
          _tag(id: 'b', name: '中国'),
        ]),
        isEmpty,
      );
    });

    test('the same non-Latin name spelled two ways is a duplicate', () {
      // Two slugs on the server ("日本-中国", "日本中国"), so both can exist.
      final groups = duplicateTagGroups([
        _tag(id: 'a', name: '日本-中国'),
        _tag(id: 'b', name: '日本中国'),
      ]);
      expect(groups.single.all.map((t) => t.id).toSet(), {'a', 'b'});
    });

    test('a name on its own is not a duplicate', () {
      expect(duplicateTagGroups([_tag(name: 'Renewal')]), isEmpty);
    });

    test('an archived tag is a former duplicate, not a current one', () {
      // It is not offered on new records, so it cannot be splitting anyone's
      // work. Counting it also means the banner never goes away: a merge
      // archives the tag it empties, so it would come straight back offering
      // to merge a tag with no records left.
      final groups = duplicateTagGroups([
        _tag(id: 'a', name: 'Renewal'),
        _tag(id: 'b', name: 'Renewals', isActive: false),
      ]);
      expect(groups, isEmpty);
    });

    test('never offers an archived tag as the destination', () {
      // `TagsMergeView` refuses one: moving records onto a tag the screen
      // renders as "Off" reads as data loss.
      final groups = duplicateTagGroups([
        _tag(id: 'a', name: 'Renewal', usage: {'leads': 1}),
        _tag(id: 'b', name: 'Renewals', usage: {'leads': 2}),
        _tag(id: 'c', name: 'renewals', isActive: false, usage: {'leads': 99}),
      ]);
      expect(groups.single.keep.isActive, isTrue);
      expect(groups.single.all.every((t) => t.isActive), isTrue);
    });

    test('never offers a tag merging into itself', () {
      // The other refusal `TagsMergeView` carries.
      final groups = duplicateTagGroups([
        _tag(id: 'a', name: 'Renewal'),
        _tag(id: 'b', name: 'Renewals'),
        _tag(id: 'c', name: 'RENEWALS'),
      ]);
      for (final group in groups) {
        expect(group.merge.any((t) => t.id == group.keep.id), isFalse);
      }
    });

    test('groups three names, and each loser is offered separately', () {
      // The endpoint takes a pair, so a group of three needs a person to say
      // which two go where.
      final groups = duplicateTagGroups([
        _tag(id: 'a', name: 'Renewal', usage: {'leads': 1}),
        _tag(id: 'b', name: 'Renewals', usage: {'leads': 5}),
        _tag(id: 'c', name: 're-newal', usage: {'leads': 3}),
      ]);
      expect(groups.single.keep.id, 'b');
      expect(groups.single.merge.map((t) => t.id), ['c', 'a']);
    });
  });

  group('what goes on the wire', () {
    test('creating a tag sends the trimmed name and nothing else', () {
      // `org` is a JWT claim, `slug` is derived, `usage` and the totals are
      // computed. A client that could name any of them would be naming facts
      // the server owns.
      expect(tagCreatePayload('  Renewal  '), {'name': 'Renewal'});
    });

    test('a merge sends only the destination id', () {
      expect(tagMergePayload('t2'), {'into': 't2'});
    });

    test('a blank name never leaves the phone', () {
      expect(validateTagName('   '), isNotNull);
      expect(validateTagName('Renewal'), isNull);
    });
  });

  group('what the confirm dialogs promise', () {
    test('turning off says the records keep the tag', () {
      // `TagsDetailView.delete` flips `is_active` and leaves the row and every
      // record's link to it in place. Nothing hard-deletes a tag anywhere.
      final text = tagArchiveExplanation(_tag(usage: {'leads': 4}));
      expect(text, contains('4 records keep this tag'));
      expect(text, contains('turn it back on'));
      expect(text.toLowerCase(), isNot(contains('delete')));
    });

    test('turning off an unused tag does not claim records keep it', () {
      final text = tagArchiveExplanation(_tag());
      expect(text, contains('Nothing carries this tag'));
    });

    test('turning off says record, not records, for one', () {
      expect(
        tagArchiveExplanation(_tag(usage: {'leads': 1})),
        contains('1 record keeps this tag'),
      );
    });

    test('merging says it cannot be undone', () {
      // Unlike turning a tag off, this is not reversed by pressing the other
      // button. The source is archived so its name survives, but the records
      // have moved and nothing remembers which ones came from where.
      final text = tagMergeExplanation(
        from: _tag(id: 'a', name: 'Renewal', usage: {'leads': 3}),
        into: _tag(id: 'b', name: 'Renewals'),
      );
      expect(text, contains('3 records move from Renewal to Renewals'));
      expect(text, contains('Renewal is turned off'));
      expect(text, contains('cannot be undone'));
    });
  });

  group('the provider', () {
    late ProviderContainer container;
    late _FakeClient client;

    setUp(() {
      client = _FakeClient();
      ApiService().setClientForTesting(client);
      container = ProviderContainer();
    });

    tearDown(() => container.dispose());

    Future<TagsState> readTags() {
      container.listen(tagSettingsProvider, (_, _) {});
      return container.read(tagSettingsProvider.future);
    }

    test('reads the rows, the usage block and the totals', () async {
      client.body = '''
      {"tags": [
        {"id": "t1", "name": "Renewal", "slug": "renewal", "color": "blue",
         "description": "", "is_active": true,
         "usage": {"leads": 2, "cases": 1}}
      ],
       "totals": {"count": 5, "active": 4, "unused": 2}}
      ''';
      final state = await readTags();
      expect(state.tags.single.name, 'Renewal');
      expect(state.tags.single.used, 3);
      expect(state.count, 5);
      expect(state.active, 4);
      expect(state.unused, 2);
      expect(state.archived, 1);
    });

    test('the settings list asks for the turned-off tags too', () async {
      // It is the screen that turns one back on, so it cannot filter them out.
      client.body = '{"tags": []}';
      await readTags();
      expect(
        client.sent.single.url.queryParameters['include_archived'],
        'true',
      );
    });

    test('the record-form picker does not', () async {
      // A turned-off tag must never be offered on a new record. These are two
      // reads on one endpoint for that reason, not one read shared.
      client.body = '{"tags": []}';
      container.listen(tagsLookupProvider, (_, _) {});
      await container.read(tagsLookupProvider.future);
      expect(
        client.sent.single.url.queryParameters.containsKey('include_archived'),
        isFalse,
      );
    });

    test('the list is ranked most used first', () async {
      client.body = '''
      {"tags": [
        {"id": "t1", "name": "Quiet", "is_active": true, "usage": {"leads": 1}},
        {"id": "t2", "name": "Busy", "is_active": true, "usage": {"leads": 9}}
      ]}
      ''';
      final state = await readTags();
      expect(state.tags.map((t) => t.id), ['t2', 't1']);
    });

    test('creating one posts the name to the tag list endpoint', () async {
      client.body = '{"tags": []}';
      await readTags();
      await container.read(tagSettingsProvider.notifier).createTag('Renewal');
      final post = client.sent.firstWhere((r) => r.method == 'POST');
      expect(post.url.path, endsWith('/tags/'));
      expect(jsonDecode(client.bodies[client.sent.indexOf(post)]), {
        'name': 'Renewal',
      });
    });

    test('a 201 is a new tag', () async {
      client.body = '{"tags": []}';
      await readTags();
      client.queue.add((status: 201, body: '{"tag": {"id": "t9"}}'));
      client.queue.add((status: 200, body: '{"tags": []}'));
      final result = await container
          .read(tagSettingsProvider.notifier)
          .createTag('Renewal');
      expect(result.error, isNull);
      expect(result.revived, isFalse);
    });

    test('a 200 means an archived tag came back instead', () async {
      // `TagsListView.post` reactivates an archived tag whose slug matches
      // rather than refusing the name. That is not the same event: the revived
      // tag arrives with its old colour and description, and every record that
      // carried it before still carries it.
      client.body = '{"tags": []}';
      await readTags();
      client.queue.add((status: 200, body: '{"tag": {"id": "t9"}}'));
      client.queue.add((status: 200, body: '{"tags": []}'));
      final result = await container
          .read(tagSettingsProvider.notifier)
          .createTag('Renewal');
      expect(result.revived, isTrue);
    });

    test('turning one off is a DELETE on the row', () async {
      client.body = '{"tags": []}';
      await readTags();
      await container.read(tagSettingsProvider.notifier).archiveTag('t1');
      final sent = client.sent.firstWhere((r) => r.method == 'DELETE');
      expect(sent.url.path, endsWith('/tags/t1/'));
    });

    test('turning one back on has its own endpoint', () async {
      // Not a PUT. `TagsDetailView.put` requires a name and rewrites the row,
      // and this control has nothing to say about the name.
      client.body = '{"tags": []}';
      await readTags();
      await container.read(tagSettingsProvider.notifier).restoreTag('t1');
      expect(client.sent.any((r) => r.method == 'PUT'), isFalse);
      final post = client.sent.firstWhere((r) => r.method == 'POST');
      expect(post.url.path, endsWith('/tags/t1/restore/'));
    });

    test('a merge posts the destination and reports what moved', () async {
      client.body = '{"tags": []}';
      await readTags();
      client.queue.add((status: 200, body: '{"moved": 7}'));
      client.queue.add((status: 200, body: '{"tags": []}'));
      final result = await container
          .read(tagSettingsProvider.notifier)
          .mergeTags(from: 't1', into: 't2');
      final post = client.sent.firstWhere((r) => r.method == 'POST');
      expect(post.url.path, endsWith('/tags/t1/merge/'));
      expect(jsonDecode(client.bodies[client.sent.indexOf(post)]), {
        'into': 't2',
      });
      // A merge that moved nothing looks identical to one that moved two
      // hundred records unless this number comes back.
      expect(result.moved, 7);
    });

    test('a write re-reads the list', () async {
      // Usage counts, the totals and the duplicate banners all change when a
      // tag does, and none of them can be recomputed from the write's answer.
      client.body = '{"tags": []}';
      await readTags();
      final before = client.sent.length;
      await container.read(tagSettingsProvider.notifier).archiveTag('t1');
      expect(
        client.sent.skip(before).where((r) => r.method == 'GET').length,
        1,
      );
    });

    test(
      'the admin-only refusal is reported in the server own words',
      () async {
        // "Only admins can create tags" is the difference between a bug and a
        // rule the user can act on.
        client.body = '{"tags": []}';
        await readTags();
        client.queue.add((
          status: 403,
          body: '{"error": true, "errors": "Only admins can create tags"}',
        ));
        final result = await container
            .read(tagSettingsProvider.notifier)
            .createTag('Renewal');
        expect(result.error, 'Only admins can create tags');
        expect(result.revived, isFalse);
      },
    );

    test('a duplicate name comes back as the field error', () async {
      client.body = '{"tags": []}';
      await readTags();
      client.queue.add((
        status: 400,
        body:
            '{"error": true, "errors": {"name": '
            '["A tag with this name already exists."]}}',
      ));
      final result = await container
          .read(tagSettingsProvider.notifier)
          .createTag('Renewal');
      expect(result.error, 'A tag with this name already exists.');
    });

    test('a refused merge does not report records moved', () async {
      client.body = '{"tags": []}';
      await readTags();
      client.queue.add((
        status: 400,
        body:
            '{"error": true, "errors": {"into": '
            '["Restore that tag before merging records onto it."]}}',
      ));
      final result = await container
          .read(tagSettingsProvider.notifier)
          .mergeTags(from: 't1', into: 't2');
      expect(result.error, 'Restore that tag before merging records onto it.');
      expect(result.moved, 0);
    });
  });
}
