import 'package:bottle_crm/data/models/invoice.dart';
import 'package:bottle_crm/data/models/lookup_models.dart';
import 'package:bottle_crm/screens/invoices/line_item_sheet.dart';
import 'package:flutter_test/flutter_test.dart';

/// Raising an invoice.
///
/// Two rules carry this form, and both are easy to get subtly wrong.
///
/// **Which contacts may be billed for an account.**
/// `InvoiceCreateSerializer.validate` refuses only a contact carrying a
/// *different* account; one with no account attaches to anyone. That is not a
/// nicety: the `Contact.account` FK is usually empty (the populated link is
/// the `Account.contacts` M2M, which this rule does not consult), so a picker
/// filtering on strict equality would be all but empty. The web filters the
/// same way, and this pins that mobile agrees.
///
/// **What a line item is allowed to send.** `subtotal`, `tax_amount` and
/// `total` are computed server-side and are not accepted on write, so a
/// payload carrying them would be sending numbers the API ignores while
/// implying the client decided them.
void main() {
  group('which contacts can be billed for an account', () {
    ContactLookup contact({String? accountId}) => ContactLookup(
      id: 'c1',
      firstName: 'Ada',
      lastName: 'Lovelace',
      accountId: accountId,
    );

    test('a contact bound to the chosen account is offered', () {
      expect(contact(accountId: 'acc-1').billableFor('acc-1'), isTrue);
    });

    test('a contact bound to another account is not', () {
      // The API answers 400 "Contact does not belong to the selected account".
      expect(contact(accountId: 'acc-2').billableFor('acc-1'), isFalse);
    });

    test('a contact with no account attaches to any of them', () {
      // The common case, and the reason a strict filter would empty the
      // picker: 0 of 15 seeded contacts had the FK set.
      expect(contact().billableFor('acc-1'), isTrue);
      expect(contact(accountId: '').billableFor('acc-1'), isTrue);
    });

    test('the account is read from account_detail, not the M2M', () {
      final parsed = ContactLookup.fromJson({
        'id': 'c1',
        'first_name': 'Ada',
        'last_name': 'Lovelace',
        'account_detail': {'id': 'acc-1', 'name': 'Northwind'},
        // Present and deliberately ignored: this is the other account link,
        // and it is not the one the invoice serializer checks.
        'linked_accounts': [
          {'id': 'acc-9', 'name': 'Initech'},
        ],
      });

      expect(parsed.accountId, 'acc-1');
      expect(parsed.accountName, 'Northwind');
      expect(parsed.billableFor('acc-9'), isFalse);
    });

    test('a null account_detail parses as unbound rather than crashing', () {
      final parsed = ContactLookup.fromJson({
        'id': 'c1',
        'first_name': 'Ada',
        'last_name': 'Lovelace',
        'account_detail': null,
      });

      expect(parsed.accountId, isNull);
      expect(parsed.billableFor('acc-1'), isTrue);
    });
  });

  group('the line item payload', () {
    test('carries only the keys the create serializer accepts', () {
      final payload = const LineItemDraft(
        name: 'Consulting',
        description: 'March',
        quantity: 10,
        unitPrice: 100,
      ).toPayload(order: 0);

      expect(payload.keys, containsAll(['name', 'quantity', 'unit_price']));
      // Server-computed. Sending them would imply the client decided them.
      expect(payload.containsKey('total'), isFalse);
      expect(payload.containsKey('subtotal'), isFalse);
      expect(payload.containsKey('tax_amount'), isFalse);
      expect(payload.containsKey('discount_amount'), isFalse);
    });

    test('omits product entirely on a free-text line', () {
      // `product` is a FK; sending null is fine but sending the key at all on
      // a line that has no product is noise the serializer has to handle.
      final payload = const LineItemDraft(
        name: 'Ad hoc work',
      ).toPayload(order: 0);

      expect(payload.containsKey('product'), isFalse);
    });

    test('keeps the product id when the line came from the catalogue', () {
      final payload = const LineItemDraft(
        name: 'Support plan',
        productId: 'prod-1',
      ).toPayload(order: 2);

      expect(payload['product'], 'prod-1');
      // The name and price still travel as plain values: InvoiceLineItem
      // denormalises both, so a later catalogue price change must not rewrite
      // an invoice already raised.
      expect(payload['name'], 'Support plan');
    });

    test('order is the position in the list, so lines keep their sequence', () {
      final first = const LineItemDraft(name: 'A').toPayload(order: 0);
      final third = const LineItemDraft(name: 'C').toPayload(order: 2);

      expect(first['order'], 0);
      expect(third['order'], 2);
    });

    test('amounts are sent as strings, matching the decimal fields', () {
      final payload = const LineItemDraft(
        name: 'Consulting',
        quantity: 2.5,
        unitPrice: 99.5,
      ).toPayload(order: 0);

      expect(payload['unit_price'], '99.50');
      expect(payload['quantity'], '2.5');
    });

    test('a line discount counts and is sent only when there is one', () {
      // The server's worked numbers: 2 x 100 less 10% = 180, 1 x 50 less 5 = 45.
      const pct = LineItemDraft(
        name: 'Seats',
        quantity: 2,
        unitPrice: 100,
        discountType: 'PERCENTAGE',
        discountValue: 10,
      );
      const flat = LineItemDraft(
        name: 'Setup',
        unitPrice: 50,
        discountType: 'FIXED',
        discountValue: 5,
      );
      expect(pct.netAmount, 180);
      expect(flat.netAmount, 45);
      expect(pct.toPayload(order: 0)['discount_value'], '10.00');
      expect(pct.toPayload(order: 0)['discount_type'], 'PERCENTAGE');

      final plain = const LineItemDraft(name: 'x').toPayload(order: 0);
      expect(plain.containsKey('discount_type'), isFalse);
      expect(plain.containsKey('discount_value'), isFalse);
    });

    test("a saved line shows the server's net_amount, not its total", () {
      final line = InvoiceLineItem.fromJson({
        'id': 'l1',
        'name': 'Seats',
        'quantity': '2.00',
        'unit_price': '100.00',
        'discount_amount': '20.00',
        'net_amount': '180.00',
        // Carries the line's own tax, which the invoice does not charge.
        'total': '198.00',
      });
      expect(line.amount, 180);
      expect(line.discountAmount, 20);

      // A server before 1.10.0 sends no net_amount.
      final older = InvoiceLineItem.fromJson({'id': 'l2', 'total': '50.00'});
      expect(older.amount, 50);
    });

    test('a whole quantity reads without decimals', () {
      expect(const LineItemDraft(name: 'x', quantity: 3).quantityLabel, '3');
      expect(
        const LineItemDraft(name: 'x', quantity: 1.5).quantityLabel,
        '1.5',
      );
    });
  });
}
