import 'package:bottle_crm/config/api_config.dart';
import 'package:bottle_crm/data/models/custom_object.dart';
import 'package:bottle_crm/data/models/record_matches.dart';
import 'package:flutter_test/flutter_test.dart';

const _id = '11111111-1111-1111-1111-111111111111';
const _other = '22222222-2222-2222-2222-222222222222';

void main() {
  test('reads a matches answer and drops blank facts', () {
    final m = RecordMatches.fromJson({
      'side': 'requirement',
      'object': {
        'key': 'property',
        'label': 'Property',
        'label_plural': 'Properties',
      },
      'matches': [
        {
          'id': _other,
          'name': '4 Mill Road',
          'facts': [
            {'label': 'Price', 'value': '250,000'},
            {'label': 'Locality', 'value': ''},
          ],
        },
        {'name': 'no id'},
      ],
      'dismissed_count': 3,
    });
    expect(m.heading, 'Matching properties');
    expect(m.objectKey, 'property');
    expect(m.matches, hasLength(1));
    expect(m.matches.single.facts.single.value, '250,000');
    expect(m.dismissedCount, 3);
  });

  test('a malformed answer reads as empty', () {
    final m = RecordMatches.fromJson({'matches': 'x', 'object': 3});
    expect(m.matches, isEmpty);
    expect(m.dismissedCount, 0);
    expect(m.heading, 'Matching records');
  });

  test('the object summary reads app_id, writable and matches defensively', () {
    final s = ObjectSummary.fromJson({
      'key': 'property',
      'label': 'Property',
      'app_id': 'real-estate',
      'writable': false,
      'matches': true,
    });
    expect(s.appId, 'real-estate');
    expect(s.writable, isFalse);
    expect(s.matches, isTrue);

    final old = ObjectSummary.fromJson({'key': 'property', 'label': 'P'});
    expect(old.appId, '');
    expect(old.writable, isNull);
    expect(old.matches, isFalse);
  });

  test('match URLs check the ids that go into the path', () {
    expect(
      ApiConfig.realEstateMatches(_id),
      endsWith('/real-estate/matches/$_id/'),
    );
    expect(
      ApiConfig.realEstateMatchDismissed(_id, _other),
      endsWith('/real-estate/matches/$_id/dismissed/$_other/'),
    );
    expect(() => ApiConfig.realEstateMatches('../x'), throwsArgumentError);
    expect(
      () => ApiConfig.realEstateMatchDismissed(_id, 'x'),
      throwsArgumentError,
    );
  });
}
