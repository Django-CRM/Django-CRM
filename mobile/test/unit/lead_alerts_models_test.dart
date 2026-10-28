import 'package:bottle_crm/data/models/app_notification.dart';
import 'package:bottle_crm/data/models/org_settings.dart';
import 'package:bottle_crm/data/models/profile.dart';
import 'package:bottle_crm/data/models/webhook.dart';
import 'package:bottle_crm/routes/app_router.dart';
import 'package:bottle_crm/screens/settings/organization_edit_screen.dart'
    show withCurrentMinutes;
import 'package:flutter_test/flutter_test.dart';

const _id = '3f2b8c1e-5a4d-4e8b-9c3a-1d2e3f4a5b6c';

AppNotification _lead(String verb, {String source = 'Google ads'}) =>
    AppNotification.fromJson({
      'id': 'n1',
      'verb': verb,
      'actor': null,
      'entity_name': 'Maria Lopez',
      'data': {'source': source},
      'link': '/leads/$_id',
      'read_at': null,
      'created_at': '2026-10-28T09:00:00Z',
    });

AppNotification _record(String verb, String name) => AppNotification.fromJson({
  'id': 'n2',
  'verb': verb,
  'actor': null,
  'entity_name': name,
  'data': <String, dynamic>{},
  'link': '/objects/class/$_id',
  'read_at': null,
  'created_at': '2026-10-28T09:00:00Z',
});

void main() {
  group('custom record alert notifications', () {
    test('read as the web does', () {
      expect(
        _record('real_estate.match', 'Asha Raman').recordAlertText,
        'Asha Raman fits an open requirement',
      );
      expect(
        _record('education.seat_open', 'Year 7 Maths').recordAlertText,
        'A seat opened in Year 7 Maths',
      );
      expect(
        _record('real_estate.match', '').recordAlertText,
        'A record fits an open requirement',
      );
      expect(
        _record('education.seat_open', '').recordAlertText,
        'A seat opened in a record',
      );
      expect(_record('lead.waiting', 'x').recordAlertText, isNull);
    });

    test('are known verbs that open the record', () {
      for (final verb in ['real_estate.match', 'education.seat_open']) {
        final n = _record(verb, 'x');
        expect(n.isKnownVerb, isTrue, reason: verb);
        expect(
          n.destinationPath,
          AppRoutes.objectRecord('class', _id),
          reason: verb,
        );
      }
    });
  });

  group('lead alert notifications', () {
    test('read as the push text does', () {
      expect(
        _lead('lead.assigned').leadText,
        'New lead assigned: Maria Lopez (Google ads)',
      );
      expect(
        _lead('lead.created').leadText,
        'New lead: Maria Lopez (Google ads)',
      );
      expect(
        _lead('lead.enquired').leadText,
        'Maria Lopez enquired again (Google ads)',
      );
      expect(
        _lead('lead.waiting').leadText,
        'Maria Lopez is waiting for a reply',
      );
    });

    test('drop the source when there is none', () {
      expect(
        _lead('lead.created', source: '').leadText,
        'New lead: Maria Lopez',
      );
    });

    test('are known verbs that open the lead', () {
      for (final verb in [
        'lead.assigned',
        'lead.created',
        'lead.enquired',
        'lead.waiting',
      ]) {
        final n = _lead(verb);
        expect(n.isKnownVerb, isTrue, reason: verb);
        expect(n.destinationPath, '/leads/$_id', reason: verb);
      }
    });

    test('other verbs have no lead text', () {
      expect(_lead('case.commented').leadText, isNull);
    });
  });

  group('a push link', () {
    test('resolves like a notification link', () {
      expect(AppNotification.destinationFor('/leads/$_id'), '/leads/$_id');
      expect(AppNotification.destinationFor('/tickets/t1'), '/tickets/t1');
    });

    test('never leaves the app or reaches an unknown screen', () {
      expect(AppNotification.destinationFor(null), isNull);
      expect(AppNotification.destinationFor('https://evil.example/x'), isNull);
      expect(AppNotification.destinationFor('/leads/not-a-uuid'), isNull);
      expect(AppNotification.destinationFor('/admin/'), isNull);
    });
  });

  group('the profile alert switches', () {
    Map<String, dynamic> profile([Map<String, dynamic> extra = const {}]) => {
      'id': 'p1',
      'user': {'id': 'u1', 'email': 'a@example.com'},
      ...extra,
    };

    test('are read from the profile', () {
      final p = Profile.fromJson(
        profile({'lead_alerts_email': false, 'lead_alerts_push': true}),
      );
      expect(p.leadAlertsEmail, isFalse);
      expect(p.leadAlertsPush, isTrue);
    });

    test('default to on when the server does not send them', () {
      final p = Profile.fromJson(profile());
      expect(p.leadAlertsEmail, isTrue);
      expect(p.leadAlertsPush, isTrue);
    });

    test('copyWith moves one and keeps the other', () {
      final p = Profile.fromJson(profile()).copyWith(leadAlertsPush: false);
      expect(p.leadAlertsPush, isFalse);
      expect(p.leadAlertsEmail, isTrue);
    });
  });

  group('the lead reminder', () {
    test('is off unless the org sets it', () {
      expect(OrgSettings.fromJson(const {}).leadReminderMinutes, isNull);
      expect(
        OrgSettings.fromJson(const {
          'lead_reminder_minutes': 15,
        }).leadReminderMinutes,
        15,
      );
    });

    test('travels in the save, null for off', () {
      Map<String, dynamic> body(int? minutes, {bool send = true}) =>
          orgSettingsPayload(
            name: 'A',
            companyName: '',
            addressLine: '',
            city: '',
            state: '',
            postcode: '',
            country: '',
            phone: '',
            email: '',
            website: '',
            taxId: '',
            defaultCurrency: 'USD',
            defaultCountry: '',
            timezone: 'UTC',
            csatEnabled: true,
            autoCloseChildren: false,
            invoiceNumberPrefix: 'INV-',
            estimateNumberPrefix: 'EST-',
            numberDatePart: 'DAY',
            numberPadding: 4,
            invoiceNumberStart: 1,
            leadReminderMinutes: minutes,
            sendLeadReminder: send,
          );
      expect(body(30)['lead_reminder_minutes'], 30);
      expect(body(null).containsKey('lead_reminder_minutes'), isTrue);
      expect(body(null)['lead_reminder_minutes'], isNull);
      expect(
        body(30, send: false).containsKey('lead_reminder_minutes'),
        isFalse,
      );
    });

    test('the picker keeps a stored value it does not list', () {
      expect(withCurrentMinutes(null), [5, 15, 30, 60]);
      expect(withCurrentMinutes(45), [5, 15, 30, 60, 45]);
    });
  });

  test('a Teams webhook is labelled', () {
    final endpoint = WebhookEndpoint.fromJson(const {
      'id': 'w1',
      'url': 'https://example.webhook.office.com/x',
      'format': 'teams',
    });
    expect(endpoint.formatLabel, 'Teams message');
  });
}
