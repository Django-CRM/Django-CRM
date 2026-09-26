import 'package:bottle_crm/data/models/ticket.dart';
import 'package:flutter_test/flutter_test.dart';

/// Duplicate is reached only by merging a ticket; the backend refuses it from
/// an edit, a bulk update and a board move. So no status picker offers it,
/// though a merged ticket still reads as Duplicate.
void main() {
  test('the pickers offer every status but Duplicate', () {
    expect(TicketStatus.settable, isNot(contains(TicketStatus.duplicate)));
    expect(TicketStatus.settable.length, TicketStatus.values.length - 1);
  });

  test('a merged ticket still parses as Duplicate', () {
    expect(TicketStatus.fromString('Duplicate'), TicketStatus.duplicate);
  });
}
