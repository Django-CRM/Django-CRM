/// The at-risk band on a ticket: the amber step between on-track and breached.
///
/// Taken from the server and never recomputed here. The band is a fraction of
/// the ticket's own configured target, and that target is measured in business
/// hours against the org's calendar, which this app does not have. The existing
/// breach getters fall back to wall-clock arithmetic when the API omits the
/// flag, and that fallback is exactly why a phone can disagree with the server
/// about a deadline. At-risk does not repeat the mistake.
library;

import 'package:bottle_crm/data/models/ticket.dart';
import 'package:flutter_test/flutter_test.dart';

Map<String, dynamic> _json({bool? firstResponse, bool? resolution}) => {
  'id': 't1',
  'name': 'Printer is on fire',
  'status': 'New',
  'priority': 'Normal',
  'account': {'id': 'a1', 'name': 'Acme'},
  'assigned_to': const [],
  'tags': const [],
  'is_sla_first_response_at_risk': ?firstResponse,
  'is_sla_resolution_at_risk': ?resolution,
};

void main() {
  group('at-risk flags', () {
    test('reads both halves off the API', () {
      final ticket = Ticket.fromJson(
        _json(firstResponse: true, resolution: false),
      );
      expect(ticket.isFirstResponseSlaAtRisk, isTrue);
      expect(ticket.isResolutionSlaAtRisk, isFalse);
    });

    test('defaults to false when the API omits them', () {
      final ticket = Ticket.fromJson(_json());
      expect(ticket.isFirstResponseSlaAtRisk, isFalse);
      expect(ticket.isResolutionSlaAtRisk, isFalse);
    });

    test('reports at risk when either half is', () {
      expect(Ticket.fromJson(_json(resolution: true)).isSlaAtRisk, isTrue);
      expect(Ticket.fromJson(_json(firstResponse: true)).isSlaAtRisk, isTrue);
      expect(Ticket.fromJson(_json()).isSlaAtRisk, isFalse);
    });

    test('a breached ticket is not also at risk', () {
      // The two are exclusive server-side. Guarded here as well so a card can
      // never wear both an amber and a red chip.
      final ticket = Ticket.fromJson({
        ..._json(firstResponse: true),
        'is_sla_first_response_breached': true,
      });
      expect(ticket.isFirstResponseSlaBreached, isTrue);
      expect(ticket.isSlaAtRisk, isFalse);
    });
  });
}
