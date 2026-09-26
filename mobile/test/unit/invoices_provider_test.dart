import 'dart:convert';

import 'package:bottle_crm/data/models/invoice.dart';
import 'package:bottle_crm/providers/invoices_provider.dart';
import 'package:bottle_crm/services/api_service.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;

/// Invoices, module 15's first slice.
///
/// Four things are worth pinning.
///
/// **One endpoint serialises `account` two ways.** The list sends a bare uuid
/// with `account_name` beside it; the detail nests the whole object. A parser
/// that reads only one produces the literal text of a map as an account name
/// on the other, which is the bug `TimeEntry` shipped with.
///
/// **Amounts arrive as decimal strings.** `"1250.00"`, not `1250.0`. `as
/// double` throws on every one of them.
///
/// **Each offered action mirrors a refusal the API enforces.** The server
/// rejects sending a paid invoice and cancelling a paid one, so the buttons
/// have to agree, and every predicate has to be able to answer both ways.
///
/// **A settled invoice has no age.** Once it is Paid or Cancelled the due date
/// stops meaning anything, and "4d late" against a paid invoice was on screen
/// in v1.
class _FakeClient extends http.BaseClient {
  _FakeClient({this.body = '{}'});

  int status = 200;
  String body;
  final List<http.BaseRequest> sent = [];

  @override
  Future<http.StreamedResponse> send(http.BaseRequest request) async {
    sent.add(request);
    await request.finalize().toBytes();
    return http.StreamedResponse(
      Stream.value(utf8.encode(body)),
      status,
      request: request,
    );
  }
}

/// Two rows in one currency, plus the `totals` block the header reads.
const _list = '''
{
  "count": 2,
  "next": null,
  "previous": null,
  "results": [
    {
      "id": "inv-1",
      "invoice_number": "INV-0001",
      "invoice_title": "March retainer",
      "status": "Overdue",
      "account": "acc-1",
      "account_name": "Northwind",
      "contact_name": "Ada Lovelace",
      "client_name": "Northwind Ltd",
      "client_email": "ap@northwind.example",
      "issue_date": "2026-07-01",
      "due_date": "2026-07-15",
      "total_amount": "1250.00",
      "amount_paid": "0.00",
      "amount_due": "1250.00",
      "currency": "USD",
      "is_overdue": true,
      "line_items_count": 3,
      "created_at": "2026-07-01T09:00:00Z"
    },
    {
      "id": "inv-2",
      "invoice_number": "INV-0002",
      "invoice_title": "Setup",
      "status": "Paid",
      "account": "acc-2",
      "account_name": "Initech",
      "client_name": "Initech",
      "client_email": "",
      "issue_date": "2026-06-01",
      "due_date": "2026-06-15",
      "total_amount": "400.00",
      "amount_paid": "400.00",
      "amount_due": "0.00",
      "currency": "USD",
      "is_overdue": false,
      "line_items_count": 1,
      "created_at": "2026-06-01T09:00:00Z"
    }
  ],
  "totals": {
    "count": 2,
    "outstanding": "1250.00",
    "overdue": "1250.00",
    "due_this_month": "0",
    "paid_this_quarter": "400.00",
    "draft": "0",
    "action_needed": 1,
    "by_currency": [
      {
        "currency": "USD",
        "count": 2,
        "action_needed": 1,
        "outstanding": "1250.00",
        "overdue": "1250.00",
        "due_this_month": "0",
        "paid_this_quarter": "400.00",
        "draft": "0"
      }
    ]
  }
}
''';

/// The same two invoices billed in different currencies.
const _mixedList = '''
{
  "count": 2,
  "next": null,
  "results": [
    {"id": "a", "invoice_number": "INV-1", "status": "Sent",
     "total_amount": "100.00", "amount_due": "100.00", "currency": "USD"},
    {"id": "b", "invoice_number": "INV-2", "status": "Sent",
     "total_amount": "100.00", "amount_due": "100.00", "currency": "EUR"}
  ],
  "totals": {
    "count": 2,
    "outstanding": null,
    "by_currency": [
      {"currency": "EUR", "count": 1, "outstanding": "100.00"},
      {"currency": "USD", "count": 1, "outstanding": "100.00"}
    ]
  }
}
''';

/// The detail shape: `account` nested, line items and payments present.
const _detail = '''
{
  "invoice": {
    "id": "inv-1",
    "invoice_number": "INV-0001",
    "invoice_title": "March retainer",
    "status": "Partially_Paid",
    "account": {"id": "acc-1", "name": "Northwind"},
    "contact": {"id": "c-1", "first_name": "Ada", "last_name": "Lovelace"},
    "client_name": "Northwind Ltd",
    "client_email": "ap@northwind.example",
    "issue_date": "2026-07-01",
    "due_date": "2026-07-15",
    "total_amount": "1250.00",
    "amount_paid": "250.00",
    "amount_due": "1000.00",
    "currency": "USD",
    "is_overdue": true,
    "line_items": [
      {"id": "li-1", "name": "Retainer", "product_name": "Support plan",
       "quantity": "1.00", "unit_price": "1000.00", "total": "1000.00"},
      {"id": "li-2", "name": "", "product_name": "Extra hours",
       "quantity": "2.50", "unit_price": "100.00", "total": "250.00"}
    ],
    "payments": [
      {"id": "p-1", "amount": "250.00", "payment_date": "2026-07-20",
       "payment_method": "BANK_TRANSFER", "reference_number": "TX-9"}
    ]
  }
}
''';

Invoice _invoice({
  String status = 'Sent',
  String amountDue = '100.00',
  String? dueDate = '2026-07-15',
}) {
  return Invoice.fromJson({
    'id': 'i',
    'invoice_number': 'INV-1',
    'status': status,
    'amount_due': amountDue,
    'total_amount': '100.00',
    'due_date': dueDate,
    'currency': 'USD',
  });
}

void main() {
  late ProviderContainer container;
  late _FakeClient client;

  setUp(() {
    client = _FakeClient(body: _list);
    ApiService().setClientForTesting(client);
    container = ProviderContainer();
  });

  tearDown(() => container.dispose());

  group('one endpoint, two shapes for account', () {
    test('a list row reads account_name beside the bare uuid', () async {
      final data = await container.read(invoicesProvider.future);
      final first = data.invoices.first;

      expect(first.accountId, 'acc-1');
      expect(first.accountName, 'Northwind');
      expect(first.contactName, 'Ada Lovelace');
    });

    test('a detail row reads the nested account object', () async {
      client.body = _detail;
      final invoice = await container
          .read(invoicesProvider.notifier)
          .getInvoice('inv-1');

      expect(invoice, isNotNull);
      expect(invoice!.accountId, 'acc-1');
      // Not "{id: acc-1, name: Northwind}", which is what toString() gives.
      expect(invoice.accountName, 'Northwind');
    });

    test('the nested contact is joined into one name', () async {
      client.body = _detail;
      final invoice = await container
          .read(invoicesProvider.notifier)
          .getInvoice('inv-1');

      expect(invoice!.contactName, 'Ada Lovelace');
    });
  });

  group('amounts are decimal strings', () {
    test('they parse rather than throw', () async {
      final data = await container.read(invoicesProvider.future);

      expect(data.invoices.first.totalAmount, 1250.00);
      expect(data.invoices.first.amountDue, 1250.00);
      expect(data.invoices[1].amountPaid, 400.00);
    });

    test('the totals block parses its strings too', () async {
      final data = await container.read(invoicesProvider.future);

      final usd = data.totals.money['USD']!;
      expect(data.totals.money.keys, ['USD']);
      expect(usd.outstanding, 1250.00);
      expect(usd.overdue, 1250.00);
      expect(usd.paidThisQuarter, 400.00);
      // A count, not an amount.
      expect(data.totals.actionNeeded, 1);
      expect(container.read(invoiceActionCountProvider), 1);
    });

    test('a missing amount is zero, not a crash', () {
      final invoice = Invoice.fromJson({'id': 'x', 'invoice_number': 'INV-9'});

      expect(invoice.totalAmount, 0);
      expect(invoice.amountDue, 0);
    });
  });

  group('status', () {
    test('Partially_Paid keeps its wire value and reads as prose', () async {
      client.body = _detail;
      final invoice = await container
          .read(invoicesProvider.notifier)
          .getInvoice('inv-1');

      expect(invoice!.status, InvoiceStatus.partiallyPaid);
      // The filter matches on the stored value, underscore included.
      expect(invoice.status!.value, 'Partially_Paid');
      expect(invoice.statusLabel, 'Partially paid');
    });

    test('an unknown status is shown, not silently defaulted', () {
      final invoice = _invoice(status: 'Refunded');

      expect(invoice.status, isNull);
      // It keeps the server's spelling instead of rendering as "Draft".
      expect(invoice.statusLabel, 'Refunded');
    });
  });

  group('the offered actions mirror what the API refuses', () {
    test('send is offered on a draft and on an overdue invoice', () {
      expect(_invoice(status: 'Draft').canSend, isTrue);
      expect(_invoice(status: 'Overdue').canSend, isTrue);
    });

    test('send is not offered on a paid or cancelled invoice', () {
      // The API answers 400: "Cannot send a paid invoice".
      expect(_invoice(status: 'Paid', amountDue: '0').canSend, isFalse);
      expect(_invoice(status: 'Cancelled').canSend, isFalse);
    });

    test('a payment is offered while there is a balance', () {
      expect(_invoice(status: 'Sent').canRecordPayment, isTrue);
      expect(_invoice(status: 'Partially_Paid').canRecordPayment, isTrue);
    });

    test('a payment is not offered with nothing left to pay', () {
      expect(
        _invoice(status: 'Paid', amountDue: '0.00').canRecordPayment,
        isFalse,
      );
    });

    test('a payment is not offered on a cancelled invoice', () {
      // The API refuses this too, in `PaymentCreateSerializer.validate`. It
      // did not when this screen was built: both payment endpoints checked
      // only the amount, and a cancelled invoice keeps a balance, so recording
      // a payment moved it back out of Cancelled.
      expect(_invoice(status: 'Cancelled').canRecordPayment, isFalse);
    });

    test('cancel is offered on anything not already settled', () {
      expect(_invoice(status: 'Draft').canCancel, isTrue);
      expect(_invoice(status: 'Sent').canCancel, isTrue);
      expect(_invoice(status: 'Overdue').canCancel, isTrue);
    });

    test('cancel is not offered on a paid or cancelled invoice', () {
      expect(_invoice(status: 'Paid', amountDue: '0').canCancel, isFalse);
      expect(_invoice(status: 'Cancelled').canCancel, isFalse);
    });

    test('no action is offered for a status this build cannot read', () {
      final unknown = _invoice(status: 'Refunded');

      expect(unknown.canSend, isFalse);
      expect(unknown.canCancel, isFalse);
    });
  });

  group('a settled invoice has no age', () {
    test('an overdue invoice counts days late', () {
      final invoice = _invoice(status: 'Overdue', dueDate: '2020-01-01');

      expect(invoice.daysLate, greaterThan(0));
      expect(invoice.isLate, isTrue);
    });

    test('a paid invoice is never late, however old the due date', () {
      final invoice = _invoice(
        status: 'Paid',
        amountDue: '0',
        dueDate: '2020-01-01',
      );

      expect(invoice.daysLate, isNull);
      expect(invoice.isLate, isFalse);
    });

    test('a cancelled invoice is never late either', () {
      final invoice = _invoice(status: 'Cancelled', dueDate: '2020-01-01');

      expect(invoice.daysLate, isNull);
    });

    test('an invoice with no due date has no age', () {
      expect(_invoice(status: 'Sent', dueDate: null).daysLate, isNull);
    });

    test('a due date is read as a local date, not UTC midnight', () {
      // Parsed with an explicit midnight. A bare `DateTime.parse` gives UTC,
      // which anywhere behind Greenwich reads as the day before and reports an
      // invoice due today as one day overdue.
      final invoice = _invoice(status: 'Sent', dueDate: '2026-07-15');

      expect(invoice.dueDate!.year, 2026);
      expect(invoice.dueDate!.month, 7);
      expect(invoice.dueDate!.day, 15);
      expect(invoice.dueDate!.isUtc, isFalse);
    });
  });

  group('line items', () {
    test('a line uses its own name, falling back to the product', () async {
      client.body = _detail;
      final invoice = await container
          .read(invoicesProvider.notifier)
          .getInvoice('inv-1');

      expect(invoice!.lineItems.first.name, 'Retainer');
      // Blank name, so the catalogue entry behind it is used.
      expect(invoice.lineItems[1].name, 'Extra hours');
    });

    test('a whole quantity drops its decimals', () async {
      client.body = _detail;
      final invoice = await container
          .read(invoicesProvider.notifier)
          .getInvoice('inv-1');

      expect(invoice!.lineItems.first.quantityLabel, '1');
      expect(invoice.lineItems[1].quantityLabel, '2.5');
    });

    test(
      'the count comes from the rows on a detail and the count on a row',
      () async {
        final list = await container.read(invoicesProvider.future);
        expect(list.invoices.first.itemCount, 3);

        client.body = _detail;
        final invoice = await container
            .read(invoicesProvider.notifier)
            .getInvoice('inv-1');
        expect(invoice!.itemCount, 2);
      },
    );
  });

  group('payments', () {
    test('a recorded payment carries its method as prose', () async {
      client.body = _detail;
      final invoice = await container
          .read(invoicesProvider.notifier)
          .getInvoice('inv-1');

      final payment = invoice!.payments.single;
      expect(payment.amount, 250.00);
      expect(payment.methodLabel, 'Bank transfer');
      expect(payment.referenceNumber, 'TX-9');
    });

    test('an unrecognised method falls back to its own value', () {
      final payment = Payment.fromJson({'id': 'p', 'payment_method': 'CRYPTO'});

      expect(payment.methodLabel, 'CRYPTO');
    });
  });

  group('totals never add two currencies', () {
    test('two currencies stay two figures', () async {
      client.body = _mixedList;
      await container.read(invoicesProvider.notifier).refresh();
      final data = container.read(invoicesProvider).value!;

      // The server used to add 100 USD to 100 EUR and call it 200. It now
      // sends one row per currency, and nothing here adds them back up.
      expect(data.totals.money.keys, ['EUR', 'USD']);
      expect(data.totals.money['EUR']!.outstanding, 100.00);
      expect(data.totals.money['USD']!.outstanding, 100.00);
      expect(data.totals.count, 2);
    });

    test('a server with plain figures only keeps them under no code', () {
      final totals = InvoiceTotals.fromJson({
        'count': 1,
        'outstanding': '75.00',
        'action_needed': 1,
      });

      expect(totals.money.keys, ['']);
      expect(totals.money['']!.outstanding, 75.00);
      expect(totals.actionNeeded, 1);
    });
  });

  group('the request', () {
    test('asks for oldest due first', () async {
      await container.read(invoicesProvider.future);

      expect(client.sent.single.url.queryParameters['sort'], 'due_date');
    });

    test('a status filter sends the wire value', () async {
      await container.read(invoicesProvider.future);
      await container
          .read(invoicesProvider.notifier)
          .filterByStatus(InvoiceStatus.partiallyPaid);

      expect(client.sent.last.url.queryParameters['status'], 'Partially_Paid');
      expect(
        container.read(invoicesProvider).value!.status,
        InvoiceStatus.partiallyPaid,
      );
    });

    test('clearing the filter drops the parameter entirely', () async {
      final notifier = container.read(invoicesProvider.notifier);
      await container.read(invoicesProvider.future);
      await notifier.filterByStatus(InvoiceStatus.draft);
      await notifier.filterByStatus(null);

      expect(
        client.sent.last.url.queryParameters.containsKey('status'),
        isFalse,
      );
    });

    test('paging follows the server\'s own next link, not a count', () async {
      // `totals.count` ignores the active filter by design, so comparing an
      // offset against it would page past the end of a filtered list.
      final data = await container.read(invoicesProvider.future);

      expect(data.hasMore, isFalse);
    });
  });

  group('writes', () {
    test('sending posts to the send endpoint', () async {
      await container.read(invoicesProvider.future);
      final error = await container
          .read(invoicesProvider.notifier)
          .send('inv-1');

      expect(error, isNull);
      expect(client.sent[1].url.path, endsWith('/api/invoices/inv-1/send/'));
    });

    test(
      'an untouched payment sends no amount, so the server settles it',
      () async {
        await container.read(invoicesProvider.future);
        await container
            .read(invoicesProvider.notifier)
            .recordPayment('inv-1', method: 'CASH');

        final request = client.sent[1] as http.Request;
        final body = jsonDecode(request.body) as Map<String, dynamic>;
        expect(body.containsKey('amount'), isFalse);
        expect(body['payment_method'], 'CASH');
      },
    );

    test('a partial payment sends the amount it was given', () async {
      await container.read(invoicesProvider.future);
      await container
          .read(invoicesProvider.notifier)
          .recordPayment('inv-1', amount: 250, reference: 'TX-9');

      final request = client.sent[1] as http.Request;
      final body = jsonDecode(request.body) as Map<String, dynamic>;
      expect(body['amount'], '250.00');
      expect(body['reference_number'], 'TX-9');
    });

    test('a blank reference is omitted rather than sent empty', () async {
      await container.read(invoicesProvider.future);
      await container
          .read(invoicesProvider.notifier)
          .recordPayment('inv-1', reference: '   ');

      final request = client.sent[1] as http.Request;
      final body = jsonDecode(request.body) as Map<String, dynamic>;
      expect(body.containsKey('reference_number'), isFalse);
    });

    test(
      'a refusal surfaces the server\'s message, not a generic one',
      () async {
        await container.read(invoicesProvider.future);
        client.status = 400;
        client.body =
            '{"error": true, "message": "Cannot send a paid invoice"}';

        final error = await container
            .read(invoicesProvider.notifier)
            .send('inv-1');

        expect(error, 'Cannot send a paid invoice');
      },
    );

    test('a field error surfaces too, since 400 arrives in two shapes', () async {
      await container.read(invoicesProvider.future);
      client.status = 400;
      client.body =
          '{"error": true, "errors": {"amount": ["Payment amount exceeds the amount due (100)."]}}';

      final error = await container
          .read(invoicesProvider.notifier)
          .recordPayment('inv-1', amount: 500);

      expect(error, 'Payment amount exceeds the amount due (100).');
    });

    test('a detail miss returns null rather than throwing', () async {
      client.status = 404;
      client.body = '{"error": true, "message": "Invoice not found"}';

      final invoice = await container
          .read(invoicesProvider.notifier)
          .getInvoice('nope');

      expect(invoice, isNull);
    });

    test('an empty id never reaches the network', () async {
      await container.read(invoicesProvider.future);
      final before = client.sent.length;

      // `ApiConfig.invoice('')` would resolve to `/api/invoices//`.
      final invoice = await container
          .read(invoicesProvider.notifier)
          .getInvoice('');

      expect(invoice, isNull);
      expect(client.sent, hasLength(before));
    });
  });
}
