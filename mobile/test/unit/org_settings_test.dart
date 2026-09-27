import 'package:bottle_crm/data/models/deal.dart';
import 'package:bottle_crm/data/models/org_settings.dart';
import 'package:bottle_crm/data/models/ticket.dart';
import 'package:flutter_test/flutter_test.dart';

/// The org as `OrgSettingsSerializer` sends it.
Map<String, dynamic> orgJson({
  String name = 'MicroPyramid',
  String companyName = 'MicroPyramid Informatics Pvt Ltd',
  String country = 'IN',
  String defaultCountry = 'IN',
  String currency = 'USD',
  String timezone = 'Asia/Kolkata',
  bool csat = true,
  bool cascade = false,
  String vertical = '',
}) => {
  'id': 'org-1',
  'name': name,
  'company_name': companyName,
  'address_line': '12 Road',
  'city': 'Hyderabad',
  'state': 'Telangana',
  'postcode': '500081',
  'country': country,
  'phone': '+91 40 1234',
  'email': 'hello@example.com',
  'website': 'https://example.com',
  'tax_id': 'GSTIN123',
  'default_currency': currency,
  'default_country': defaultCountry,
  'currency_symbol': '\$',
  'timezone': timezone,
  'csat_enabled': csat,
  'auto_close_children_on_parent_close': cascade,
  'vertical': vertical,
  'terminology': const {},
  'member_count': 4,
  'created_at': '2026-01-05T10:00:00Z',
};

void main() {
  group('OrgSettings.fromJson', () {
    test('reads the company profile and the two switches', () {
      final org = OrgSettings.fromJson(orgJson(csat: false, cascade: true));
      expect(org.companyName, 'MicroPyramid Informatics Pvt Ltd');
      expect(org.taxId, 'GSTIN123');
      expect(org.csatEnabled, isFalse);
      expect(org.autoCloseChildren, isTrue);
      expect(org.memberCount, 4);
    });

    test('a null text field reads as empty rather than the string "null"', () {
      final json = orgJson()..['tax_id'] = null;
      expect(OrgSettings.fromJson(json).taxId, '');
    });

    test('an org with no api_key on the payload is the normal case', () {
      // The key authenticates as the org's first admin, so it is excluded from
      // the serializer entirely. Nothing on this model could hold one.
      final org = OrgSettings.fromJson(orgJson());
      expect(org.id, 'org-1');
      expect(org.addressSummary, contains('Hyderabad'));
    });

    test('defaults are sane when the payload is empty', () {
      final org = OrgSettings.fromJson(const {});
      expect(org.defaultCurrency, 'USD');
      expect(org.timezone, 'UTC');
      expect(org.csatEnabled, isTrue);
      expect(org.autoCloseChildren, isFalse);
    });
  });

  group('addressSummary', () {
    test('joins what is set, in printing order', () {
      expect(
        OrgSettings.fromJson(orgJson()).addressSummary,
        '12 Road, Hyderabad, Telangana, 500081, IN',
      );
    });

    test('drops the parts that are blank rather than leaving gaps', () {
      final json = orgJson()
        ..['city'] = ''
        ..['state'] = null;
      expect(OrgSettings.fromJson(json).addressSummary, '12 Road, 500081, IN');
    });
  });

  group('timezoneLabel', () {
    test('opens the underscores out for reading', () {
      final json = orgJson(timezone: 'America/New_York');
      expect(OrgSettings.fromJson(json).timezoneLabel, 'America/New York');
    });
  });

  group('orgSettingsPayload', () {
    final body = orgSettingsPayload(
      name: '  MicroPyramid  ',
      companyName: 'MicroPyramid Informatics',
      addressLine: '12 Road',
      city: 'Hyderabad',
      state: 'Telangana',
      postcode: '500081',
      country: 'IN',
      phone: '+91 40 1234',
      email: ' hello@example.com ',
      website: 'https://example.com',
      taxId: 'GSTIN123',
      defaultCurrency: 'INR',
      defaultCountry: 'IN',
      timezone: 'Asia/Kolkata',
      csatEnabled: false,
      autoCloseChildren: true,
    );

    test('trims every text field', () {
      expect(body['name'], 'MicroPyramid');
      expect(body['email'], 'hello@example.com');
    });

    test('sends both booleans explicitly, so off is a real value', () {
      expect(body['csat_enabled'], false);
      expect(body['auto_close_children_on_parent_close'], true);
    });

    test('never sends anything the server owns', () {
      for (final key in [
        'id',
        'api_key',
        'is_active',
        'vertical',
        'terminology',
        'member_count',
        'created_at',
        'currency_symbol',
      ]) {
        expect(body.containsKey(key), isFalse, reason: key);
      }
    });

    test('covers exactly the allow-list the web sends', () {
      expect(body.keys.toSet(), {...editableOrgFields, ...orgBooleanFields});
    });
  });

  group('orgSettingsProblem', () {
    test('accepts an ordinary pair', () {
      expect(
        orgSettingsProblem(
          email: 'hello@example.com',
          website: 'https://example.com',
        ),
        isNull,
      );
    });

    test('accepts both being empty, which the model allows', () {
      expect(orgSettingsProblem(email: '', website: ''), isNull);
    });

    test('refuses an address that is not one', () {
      expect(
        orgSettingsProblem(email: 'hello', website: ''),
        contains('email'),
      );
    });

    test('refuses a website without a scheme, which URLField rejects', () {
      expect(
        orgSettingsProblem(email: '', website: 'example.com'),
        contains('http'),
      );
    });
  });

  group('orgCountryOptions', () {
    test('offers South Africa (issue #770)', () {
      expect(orgCountryOptions, contains((value: 'ZA', label: 'South Africa')));
    });

    test('offers every country the backend accepts, not a short list', () {
      // The web org form lists the same 246 codes as common/utils COUNTRIES.
      expect(orgCountryOptions.length, 246);
      expect(orgCountryOptions.map((o) => o.value).toSet().length, 246);
    });
  });

  group('Currency', () {
    test('knows the South African rand (issue #770)', () {
      final rand = Currency.fromString('ZAR');
      expect(rand.value, 'ZAR');
      expect(rand.symbol, 'R');
      expect(Currency.symbolFor('ZAR'), 'R');
    });
  });

  group('withCurrent', () {
    const options = [
      (value: 'US', label: 'United States'),
      (value: 'IN', label: 'India'),
    ];

    test('leaves the list alone when the stored value is offered', () {
      expect(withCurrent(options, 'IN'), options);
    });

    test('appends a stored value the offered list does not carry', () {
      // An installed build can lag the server's list of accepted codes.
      // Dropping a stored value would make the picker save one the org never
      // chose, and a Flutter dropdown with no matching item throws outright.
      final out = withCurrent(options, 'NZ');
      expect(out.last.value, 'NZ');
      expect(out.length, 3);
    });

    test('an empty current value adds nothing', () {
      expect(withCurrent(options, ''), options);
      expect(withCurrent(options, '   '), options);
    });
  });

  group('TimezoneOption', () {
    test('labels the offset in hours and minutes', () {
      const zone = TimezoneOption(name: 'Asia/Kolkata', offsetMinutes: 330);
      expect(zone.label, 'Asia/Kolkata (UTC+05:30)');
    });

    test('a negative offset reads as negative', () {
      const zone = TimezoneOption(
        name: 'America/New_York',
        offsetMinutes: -240,
      );
      expect(zone.label, 'America/New York (UTC-04:00)');
    });

    test('zero is plain UTC, not UTC+00:00', () {
      const zone = TimezoneOption(name: 'UTC', offsetMinutes: 0);
      expect(zone.label, 'UTC (UTC)');
    });
  });

  group('cascadeDefaultExplanation', () {
    test('says the prompt starts ticked when the setting is on', () {
      expect(cascadeDefaultExplanation(true), contains('starts with'));
      expect(cascadeDefaultExplanation(true), contains('ticked'));
      expect(cascadeDefaultExplanation(true), contains('still confirm'));
    });

    test('says unticked when it is off', () {
      expect(cascadeDefaultExplanation(false), contains('unticked'));
    });

    test('never claims the setting closes children on its own', () {
      // `CaseCloseWithChildrenView` honours the org default only when the
      // caller omits `cascade`, and both clients send it. The setting reaches
      // a person through the checkbox and nowhere else.
      for (final enabled in [true, false]) {
        final text = cascadeDefaultExplanation(enabled);
        expect(text, isNot(contains('automatically')));
      }
    });
  });

  group('csatExplanation', () {
    test('names the org-wide reach either way', () {
      expect(csatExplanation(true), contains('org-wide'));
      expect(csatExplanation(false), contains('anywhere in this organization'));
    });
  });

  group('PackApplyReport', () {
    test('counts created and skipped from the report block', () {
      final report = PackApplyReport.fromJson(const {
        'report': {
          'created': [
            {'name': 'a'},
            {'name': 'b'},
          ],
          'skipped': [
            {'name': 'c'},
          ],
        },
      });
      expect(report.created, 2);
      expect(report.skipped, 1);
      expect(report.summary, 'Created 2 items, skipped 1 you already had.');
    });

    test('all-skipped reads as already having it, not as a failure', () {
      final report = PackApplyReport.fromJson(const {
        'report': {
          'created': [],
          'skipped': [
            {'name': 'c'},
          ],
        },
      });
      expect(report.summary, contains('Already had everything'));
    });

    test('an empty report says so rather than claiming a create', () {
      final report = PackApplyReport.fromJson(const {'report': {}});
      expect(report.summary, contains('already has all of it'));
    });
  });

  group('sampleDataResult', () {
    test('reports the deletion', () {
      expect(
        sampleDataResult(deleted: 3, retained: 0),
        'Deleted 3 sample records.',
      );
    });

    test('explains what was kept, so the number is not a surprise', () {
      final text = sampleDataResult(deleted: 3, retained: 2);
      expect(text, contains('Kept 2 records'));
      expect(text, contains('attached real work'));
    });

    test('says nothing to clear rather than "deleted 0"', () {
      expect(
        sampleDataResult(deleted: 0, retained: 0),
        'No sample data to clear.',
      );
    });
  });

  group('cascadeCloseMessage', () {
    test('counts what the server actually cascaded', () {
      expect(
        cascadeCloseMessage(cascade: true, cascaded: 3),
        'Ticket closed, and 3 linked tickets with it',
      );
      expect(
        cascadeCloseMessage(cascade: true, cascaded: 1),
        'Ticket closed, and 1 linked ticket with it',
      );
    });

    test('says nothing else changed when nothing was open', () {
      // The old message reported `child_count`, so a parent whose children were
      // all closed already announced "and 3 linked" while nothing happened.
      expect(
        cascadeCloseMessage(cascade: true, cascaded: 0),
        contains('Nothing linked was open'),
      );
    });

    test('an unticked cascade says only that the ticket closed', () {
      expect(cascadeCloseMessage(cascade: false, cascaded: 0), 'Ticket closed');
    });
  });

  group('cascadedCount', () {
    test('counts the ids the endpoint returned', () {
      expect(
        cascadedCount(const {
          'cascaded_case_ids': ['a', 'b'],
        }),
        2,
      );
    });

    test('a body without the key means none, not unknown', () {
      expect(cascadedCount(const {}), 0);
      expect(cascadedCount(null), 0);
    });
  });
}
