import 'package:bottle_crm/data/api_envelope.dart';
import 'package:bottle_crm/data/models/account.dart';
import 'package:bottle_crm/data/models/contact.dart';
import 'package:flutter_test/flutter_test.dart';

/// Parsing is where these two modules can fail silently.
///
/// A wrong envelope path yields an empty list and a screen that says "No
/// accounts found" in an org with hundreds, which is exactly how the deal
/// form's account picker sat broken until it was driven by hand. A misspelled
/// rollup key yields a null, and a panel that renders nothing.
///
/// The fixtures below are the real response shapes, taken from
/// `accounts/views.py` and `contacts/views.py` rather than invented.
void main() {
  group('the accounts list envelope', () {
    final body = <String, dynamic>{
      'per_page': 10,
      'page_number': 1,
      'active_accounts': {
        'offset': 0,
        'open_accounts': [
          {'id': 'a1', 'name': 'Acme Corp'},
          {'id': 'a2', 'name': 'Globex'},
        ],
        'open_accounts_count': 2,
      },
      'closed_accounts': {
        'offset': 0,
        'close_accounts': [
          {'id': 'a9', 'name': 'Defunct Ltd'},
        ],
        'close_accounts_count': 1,
      },
    };

    test('open rows are two levels down, not at results', () {
      // The trap: `/api/accounts/` publishes neither `results` nor `accounts`.
      expect(listFromEnvelope(body, const ['results']), isEmpty);
      expect(
        listFromEnvelope(body, const ['active_accounts.open_accounts']),
        hasLength(2),
      );
    });

    test('closed rows live under a differently named key again', () {
      // `close_accounts`, not `closed_accounts`. Reusing the outer name here
      // would give an empty Closed tab that looks like a tidy org.
      expect(
        listFromEnvelope(body, const ['closed_accounts.close_accounts']),
        hasLength(1),
      );
    });
  });

  group('an account row', () {
    final json = <String, dynamic>{
      'id': 'a1',
      'name': 'Acme Corp',
      'email': 'hello@acme.test',
      'website': 'https://acme.test',
      'city': 'Pune',
      'country': 'IN',
      'country_display': 'India',
      'number_of_employees': 250,
      'currency': 'INR',
      'is_active': true,
      'assigned_to': [
        {
          'id': 'p1',
          'user_details': {'name': 'Ada Lovelace', 'email': 'ada@acme.test'},
        },
      ],
      'tags': [
        {'id': 't1', 'name': 'Enterprise'},
      ],
      'contacts': [
        {'id': 'c1', 'first_name': 'Grace', 'last_name': 'Hopper'},
      ],
      'opportunities': [
        {'id': 'o1', 'name': 'Renewal', 'stage': 'NEGOTIATION', 'amount': '10'},
      ],
      'created_by': {'id': 'u1', 'email': 'owner@acme.test'},
      'rollups': {
        'won_amount': '15000.00',
        'won_count': 3,
        'open_pipeline': '4200.50',
        'open_deal_count': 2,
        'overdue_amount': '0',
        'open_tickets': 1,
        'first_won_on': '2026-01-15',
        'by_currency': [
          {
            'currency': 'USD',
            'won_amount': '15000.00',
            'open_pipeline': '4200.50',
            'overdue_amount': '0',
          },
        ],
      },
    };

    test('reads the rollup keys the server actually sends', () {
      final account = Account.fromJson(json);

      // These names come from `accounts.views.ROLLUP_FIELDS`. My first attempt
      // guessed all seven wrong, which parses to a panel of nulls rather than
      // to an error anyone would notice.
      final usd = account.rollups!.money.single;
      expect(usd.currency, 'USD');
      expect(usd.wonAmount, 15000.0);
      expect(usd.openPipeline, 4200.5);
      expect(account.rollups!.openDealCount, 2);
      expect(account.rollups!.openTickets, 1);
      expect(account.rollups!.firstWonOn, DateTime(2026, 1, 15));
    });

    test('two currencies stay two entries, never one sum', () {
      final rollups = AccountRollups.fromJson({
        'won_amount': null,
        'won_count': 2,
        'open_pipeline': null,
        'open_deal_count': 0,
        'overdue_amount': null,
        'open_tickets': 0,
        'by_currency': [
          {
            'currency': 'EUR',
            'won_amount': '300.00',
            'open_pipeline': '0',
            'overdue_amount': '70.00',
          },
          {
            'currency': 'USD',
            'won_amount': '1000.00',
            'open_pipeline': '0',
            'overdue_amount': '0',
          },
        ],
      });

      expect(rollups.money.map((m) => m.currency), ['EUR', 'USD']);
      expect(rollups.money.map((m) => m.wonAmount), [300.0, 1000.0]);
      expect(rollups.money.first.overdueAmount, 70.0);
    });

    test('a server with plain figures only keeps them under no code', () {
      final rollups = AccountRollups.fromJson({
        'won_amount': 15000,
        'open_pipeline': '10',
        'overdue_amount': 0,
        'open_tickets': 1,
      });

      final plain = rollups.money.single;
      expect(plain.currency, '');
      expect(plain.wonAmount, 15000.0);
      expect(plain.openPipeline, 10.0);
    });

    test('absent rollups stay null rather than becoming zero', () {
      final account = Account.fromJson({'id': 'a1', 'name': 'Acme'});

      // The server sends null from any endpoint that did not run the
      // annotation. A zero here would be a claim the screen has not earned.
      expect(account.rollups, isNull);
    });

    test('nested assignees and tags are unwrapped', () {
      final account = Account.fromJson(json);

      expect(account.assignedToIds, ['p1']);
      expect(account.assignedToNames, ['Ada Lovelace']);
      expect(account.tagIds, ['t1']);
      expect(account.tagNames, ['Enterprise']);
    });

    test('the creator is kept as an email, not an id', () {
      final account = Account.fromJson(json);

      // `isAdminOrOwner` is handed `currentUserProvider?.email` on every
      // detail screen here. An id on one side and an email on the other
      // compares false forever and hides the Delete action from its owner.
      expect(account.createdByEmail, 'owner@acme.test');
    });

    test('relations carry a usable label', () {
      final account = Account.fromJson(json);

      expect(account.contacts.single.label, 'Grace Hopper');
      expect(account.opportunities.single.label, 'Renewal');
      expect(account.opportunities.single.detail, 'NEGOTIATION');
    });

    test('a deal shows its stage label, not the code', () {
      final labelled = Map<String, dynamic>.from(json)
        ..['opportunities'] = [
          {
            'id': 'o1',
            'name': 'Renewal',
            'stage': 'DEMO_BOOKED',
            'stage_label': 'Demo booked',
            'amount': '10',
          },
        ];
      expect(
        Account.fromJson(labelled).opportunities.single.detail,
        'Demo booked',
      );
    });

    test('the location line prefers the human country name', () {
      expect(Account.fromJson(json).locationLine, 'Pune, India');
    });

    test('the payload sends the code, never the display name', () {
      final payload = Account.fromJson(json).toPayload();

      // `country` is an ISO alpha-2 column. Posting "India" is a 400 the user
      // cannot act on, and `country_display` is read-only.
      expect(payload['country'], 'IN');
      expect(payload.containsKey('country_display'), isFalse);
    });
  });

  group('a contact row', () {
    final json = <String, dynamic>{
      'id': 'c1',
      'first_name': 'Grace',
      'last_name': 'Hopper',
      'email': 'grace@navy.test',
      'title': 'Rear Admiral',
      'do_not_call': true,
      'account_detail': {'id': 'a1', 'name': 'Acme Corp'},
      'linked_accounts': [
        {'id': 'a1', 'name': 'Acme Corp'},
        {'id': 'a2', 'name': 'Globex'},
      ],
      'assigned_to': [
        {
          'id': 'p1',
          'user_details': {'name': 'Ada Lovelace'},
        },
      ],
      'created_by': {'id': 'u1', 'email': 'owner@acme.test'},
    };

    test('the primary account comes from account_detail', () {
      final contact = Contact.fromJson(json);

      expect(contact.accountId, 'a1');
      expect(contact.accountName, 'Acme Corp');
    });

    test('the many-to-many is kept separately from the FK', () {
      final contact = Contact.fromJson(json);

      // Two links on this record, and the web app treats them as different
      // relationships. Collapsing them would make a well-linked contact look
      // unattached whenever the FK is empty, which on this data it often is.
      expect(contact.linkedAccounts.map((a) => a.id), ['a1', 'a2']);
    });

    test('do not call survives the round trip', () {
      final contact = Contact.fromJson(json);

      expect(contact.doNotCall, isTrue);
      expect(contact.toPayload()['do_not_call'], isTrue);
    });

    test('the subtitle reads as a role at a company', () {
      expect(Contact.fromJson(json).subtitle, 'Rear Admiral at Acme Corp');
    });

    test('a contact with no name at all falls back to the email', () {
      final contact = Contact.fromJson({
        'id': 'c2',
        'first_name': '',
        'last_name': '',
        'email': 'anon@example.test',
      });

      expect(contact.fullName, 'anon@example.test');
      expect(contact.initials, '?');
    });

    test('the payload carries only fields the create serializer accepts', () {
      final payload = Contact.fromJson(json).toPayload();

      // `CreateContactSerializer.Meta.fields` is the list. Sending
      // `linked_accounts` or `account_detail` is noise the serializer drops,
      // and shipping it would suggest the form can edit them when it cannot.
      expect(payload.containsKey('linked_accounts'), isFalse);
      expect(payload.containsKey('account_detail'), isFalse);
      expect(payload['account'], 'a1');
    });
  });
}
