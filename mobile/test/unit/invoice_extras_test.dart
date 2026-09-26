import 'dart:convert';

import 'package:bottle_crm/data/models/estimate.dart';
import 'package:bottle_crm/data/models/invoice_report.dart';
import 'package:bottle_crm/data/models/invoice_template.dart';
import 'package:bottle_crm/data/models/product.dart';
import 'package:bottle_crm/data/models/recurring_invoice.dart';
import 'package:bottle_crm/providers/invoice_extras_provider.dart';
import 'package:bottle_crm/services/api_service.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;

/// The five smaller invoice surfaces.
///
/// What is worth pinning here is mostly about NOT lying:
///
/// - A paused schedule keeps its `next_generation_date`, so showing it would
///   promise an invoice that is not coming.
/// - A converted estimate must not offer to convert again; the server refuses,
///   and the row exists to tell the two apart.
/// - The template model must never carry the raw HTML or CSS, because that is
///   a server-side PDF render input and a stored-XSS sink anywhere it lands in
///   a markup renderer.
/// - The ageing report's own `total` is over every unpaid invoice while its
///   bucket lists are capped at ten, so the two disagree by design.
class _FakeClient extends http.BaseClient {
  int status = 200;
  String body = '{}';
  final List<http.BaseRequest> sent = [];

  /// Set to answer per-URL, for the reports screen which calls two endpoints.
  Map<String, ({int status, String body})>? routes;

  @override
  Future<http.StreamedResponse> send(http.BaseRequest request) async {
    sent.add(request);
    await request.finalize().toBytes();
    var code = status;
    var payload = body;
    if (routes != null) {
      for (final entry in routes!.entries) {
        if (request.url.path.contains(entry.key)) {
          code = entry.value.status;
          payload = entry.value.body;
          break;
        }
      }
    }
    return http.StreamedResponse(
      Stream.value(utf8.encode(payload)),
      code,
      request: request,
    );
  }
}

void main() {
  late ProviderContainer container;
  late _FakeClient client;

  setUp(() {
    client = _FakeClient();
    ApiService().setClientForTesting(client);
    container = ProviderContainer();
  });

  tearDown(() => container.dispose());

  // Reading a provider while something listens to it.
  //
  // Riverpod 3 providers are auto-dispose, so a bare
  // `container.read(p.future)` holds no subscription: the provider can be torn
  // down while its request is still in flight, and the future then completes
  // with "disposed during loading state" rather than the result. It is
  // timing-dependent, which is the dangerous part: a test written without this
  // passes until the call it fakes takes a moment longer. One helper per
  // provider because Riverpod 3 does not export a type that would let a single
  // generic one take both the provider and its `.future`.

  Future<List<Estimate>> readEstimates() {
    container.listen(estimatesProvider, (_, _) {});
    return container.read(estimatesProvider.future);
  }

  Future<List<RecurringInvoice>> readRecurring() {
    container.listen(recurringProvider, (_, _) {});
    return container.read(recurringProvider.future);
  }

  Future<List<Product>> readProducts() {
    container.listen(productsProvider, (_, _) {});
    return container.read(productsProvider.future);
  }

  Future<List<InvoiceTemplate>> readTemplates() {
    container.listen(invoiceTemplatesProvider, (_, _) {});
    return container.read(invoiceTemplatesProvider.future);
  }

  Future<InvoiceReports> readReports() {
    // onError as well: without it Riverpod rethrows a provider failure into
    // the zone, and the awaiting test hangs instead of seeing the error.
    container.listen(invoiceReportsProvider, (_, _) {}, onError: (_, _) {});
    return container.read(invoiceReportsProvider.future);
  }

  group('estimates', () {
    const feed = '''
    {"count": 2, "results": [
      {"id": "e1", "estimate_number": "EST-1", "title": "Rebuild",
       "status": "Accepted", "account_name": "Northwind",
       "total_amount": "5000.00", "currency": "USD",
       "expiry_date": "2026-09-01", "is_expired": false,
       "converted_to_invoice": null},
      {"id": "e2", "estimate_number": "EST-2", "title": "Support",
       "status": "Accepted", "account_name": "Initech",
       "total_amount": "1000.00", "currency": "USD",
       "converted_to_invoice": {"id": "inv-9", "invoice_number": "INV-0009"}}
    ]}''';

    test('an unconverted estimate offers to raise an invoice', () async {
      client.body = feed;
      final estimates = await readEstimates();

      expect(estimates.first.canConvert, isTrue);
      expect(estimates.first.isConverted, isFalse);
    });

    test('a converted estimate does not offer it again', () async {
      client.body = feed;
      final estimates = await readEstimates();

      // The server answers 400 "Estimate already converted to invoice".
      expect(estimates[1].canConvert, isFalse);
      expect(estimates[1].convertedInvoiceNumber, 'INV-0009');
      expect(estimates[1].convertedInvoiceId, 'inv-9');
    });

    test('converting returns the new invoice id to navigate to', () async {
      client.body = feed;
      await readEstimates();
      client.body = '{"error": false, "invoice": {"id": "inv-new"}}';

      final result = await container
          .read(estimatesProvider.notifier)
          .convert('e1');

      expect(result.error, isNull);
      expect(result.invoiceId, 'inv-new');
    });

    test('a refused conversion surfaces the server message', () async {
      client.body = feed;
      await readEstimates();
      client.status = 400;
      client.body =
          '{"error": true, "message": "Estimate already converted to invoice"}';

      final result = await container
          .read(estimatesProvider.notifier)
          .convert('e2');

      expect(result.invoiceId, isNull);
      expect(result.error, 'Estimate already converted to invoice');
    });

    test('send is offered on draft, sent and viewed only', () {
      // Mirrors EstimateSendView, which refuses these three outright.
      Estimate at(String status) =>
          Estimate.fromJson({'id': 'e', 'status': status});

      expect(at('Draft').canSend, isTrue);
      expect(at('Sent').canSend, isTrue);
      expect(at('Viewed').canSend, isTrue);
      expect(at('Accepted').canSend, isFalse);
      expect(at('Declined').canSend, isFalse);
      expect(at('Expired').canSend, isFalse);
    });

    test('send is withheld from a lapsed quote still labelled Sent', () {
      // The window between the expiry date passing and the daily
      // `check_expired_estimates` task relabelling it. The server refuses on
      // `is_expired` separately from the status for exactly this, so a client
      // reading only the status would draw a button that answers 400.
      final lapsed = Estimate.fromJson({
        'id': 'e',
        'status': 'Sent',
        'is_expired': true,
      });

      expect(lapsed.status, EstimateStatus.sent);
      expect(lapsed.canSend, isFalse);
    });

    test('the status filter sends the wire value', () async {
      client.body = feed;
      await readEstimates();
      await container
          .read(estimatesProvider.notifier)
          .filterByStatus(EstimateStatus.declined);

      expect(client.sent.last.url.queryParameters['status'], 'Declined');
    });
  });

  group('recurring schedules', () {
    RecurringInvoice schedule({
      required bool isActive,
      String frequency = 'MONTHLY',
      int? customDays,
    }) {
      return RecurringInvoice.fromJson({
        'id': 'r1',
        'title': 'Retainer',
        'frequency': frequency,
        'custom_days': customDays,
        'is_active': isActive,
        'next_generation_date': '2026-09-01',
      });
    }

    test('a running schedule shows its next run', () {
      expect(schedule(isActive: true).effectiveNextRun, isNotNull);
    });

    test('a paused schedule shows none, though the server still sends one', () {
      // The date is still on the row. Printing it would promise an invoice
      // that is not coming while the schedule is paused.
      final paused = schedule(isActive: false);
      expect(paused.nextGenerationDate, isNotNull);
      expect(paused.effectiveNextRun, isNull);
    });

    test('a custom cadence reads as its interval, not the word Custom', () {
      expect(
        schedule(
          isActive: true,
          frequency: 'CUSTOM',
          customDays: 10,
        ).frequencyLabel,
        'Every 10 days',
      );
    });

    test('a custom cadence with no interval falls back to the label', () {
      expect(
        schedule(isActive: true, frequency: 'CUSTOM').frequencyLabel,
        'Custom',
      );
    });

    test('a known frequency reads as prose', () {
      expect(
        schedule(isActive: true, frequency: 'SEMI_ANNUALLY').frequencyLabel,
        'Twice a year',
      );
    });

    test('a custom cadence is incomplete without a positive interval', () {
      // Mirrors RecurringInvoiceCreateSerializer.validate. Zero counts as
      // missing because it is falsy in calculate_next_date, where it fell
      // through to a monthly step.
      expect(cadenceIsComplete('CUSTOM', null), isFalse);
      expect(cadenceIsComplete('CUSTOM', 0), isFalse);
      expect(cadenceIsComplete('CUSTOM', -3), isFalse);
    });

    test('a custom cadence with an interval is complete', () {
      expect(cadenceIsComplete('CUSTOM', 1), isTrue);
      expect(cadenceIsComplete('CUSTOM', 10), isTrue);
    });

    test('every other frequency carries its own interval', () {
      // The rule must not leak onto the other six and block an ordinary
      // monthly schedule.
      for (final f in [
        'WEEKLY',
        'BIWEEKLY',
        'MONTHLY',
        'QUARTERLY',
        'SEMI_ANNUALLY',
        'YEARLY',
      ]) {
        expect(cadenceIsComplete(f, null), isTrue, reason: f);
      }
    });

    test('an unknown frequency keeps the server spelling', () {
      expect(
        schedule(isActive: true, frequency: 'FORTNIGHTLY').frequencyLabel,
        'FORTNIGHTLY',
      );
    });

    test(
      'the active filter sends true or false, and nothing when cleared',
      () async {
        client.body = '{"count": 0, "results": []}';
        final notifier = container.read(recurringProvider.notifier);
        await readRecurring();

        await notifier.filterByActive(false);
        expect(client.sent.last.url.queryParameters['is_active'], 'false');

        await notifier.filterByActive(null);
        expect(
          client.sent.last.url.queryParameters.containsKey('is_active'),
          isFalse,
        );
      },
    );
  });

  group('products', () {
    test('the payload carries only fields the create serializer accepts', () {
      final payload = const Product(
        id: 'p1',
        name: 'Support plan',
        price: 99.5,
        currency: 'USD',
        isActive: true,
      ).toPayload();

      expect(payload['name'], 'Support plan');
      expect(payload['price'], '99.50');
      // `org` and `id` are server-derived and must never be sent.
      expect(payload.containsKey('org'), isFalse);
      expect(payload.containsKey('id'), isFalse);
      expect(payload.containsKey('used_on'), isFalse);
    });

    test('a blank sku is omitted rather than sent empty', () {
      // SKU is unique per org, so two products with '' would collide.
      final payload = const Product(id: 'p', name: 'X').toPayload();
      expect(payload.containsKey('sku'), isFalse);
    });

    test('a 403 on write surfaces the admin-only message', () async {
      client.body = '{"count": 0, "results": []}';
      await readProducts();
      client.status = 403;
      client.body =
          '{"error": true, "message": "Only an administrator can change the product catalog."}';

      final error = await container
          .read(productsProvider.notifier)
          .createProduct(const Product(id: '', name: 'X', price: 1));

      expect(error, 'Only an administrator can change the product catalog.');
    });

    test('used_on is read so a retired product still shows its history', () {
      final product = Product.fromJson({
        'id': 'p',
        'name': 'Old plan',
        'is_active': false,
        'used_on': 12,
      });

      expect(product.isActive, isFalse);
      expect(product.usedOn, 12);
    });
  });

  group('templates', () {
    test('the model cannot carry the raw markup', () async {
      client.body = '''
      {"count": 1, "results": [
        {"id": "t1", "name": "House style", "is_default": true,
         "primary_color": "#3B82F6", "has_logo": true,
         "has_custom_html": true, "used_on_invoices": 4,
         "template_html": "<script>alert(1)</script>"}
      ]}''';

      final templates = await readTemplates();
      final t = templates.single;

      expect(t.name, 'House style');
      expect(t.isDefault, isTrue);
      // It knows custom markup EXISTS and never what it says. The list
      // serializer does not send it either; this pins that the client would
      // not keep it if a future serializer regressed and started to.
      expect(t.hasCustomHtml, isTrue);
      expect(
        InvoiceTemplate.fromJson({
          'id': 't',
          'template_html': '<script>alert(1)</script>',
        }).toString(),
        isNot(contains('script')),
      );
    });

    test('the editable text fields come off the list row', () async {
      // The form pre-fills from the catalogue rather than a detail call, which
      // only works while the list serializer carries these three.
      client.body = '''
      {"count": 1, "results": [
        {"id": "t1", "name": "House style", "default_notes": "Thanks.",
         "default_terms": "Net 30", "footer_text": "Ask for Dana"}
      ]}''';

      final t = (await readTemplates()).single;

      expect(t.defaultNotes, 'Thanks.');
      expect(t.defaultTerms, 'Net 30');
      expect(t.footerText, 'Ask for Dana');
    });

    group('the draft a form submits', () {
      const draft = InvoiceTemplateDraft(
        name: '  House style  ',
        primaryColor: '#3B82F6',
        secondaryColor: '#1E40AF',
        defaultNotes: 'Thanks.',
      );

      test('never names either markup field', () {
        // The whole reason a template form on a phone is safe: the PUT is
        // partial, so a key the payload omits is a key the serializer never
        // sees, and the web editor's markup survives a save from here.
        final payload = draft.toPayload();

        expect(payload.containsKey('template_html'), isFalse);
        expect(payload.containsKey('template_css'), isFalse);
      });

      test('trims the name and keeps the rest verbatim', () {
        final payload = draft.toPayload();

        expect(payload['name'], 'House style');
        expect(payload['primary_color'], '#3B82F6');
        expect(payload['default_notes'], 'Thanks.');
      });

      test('omits is_default when the form does not own it', () {
        // An edit sends null, and an absent key is what leaves the org's
        // current default where it is. Sending `false` would demote it.
        expect(draft.toPayload().containsKey('is_default'), isFalse);
      });

      test('sends is_default when the create form does own it', () {
        const creating = InvoiceTemplateDraft(
          name: 'New',
          primaryColor: '#3B82F6',
          secondaryColor: '#1E40AF',
          isDefault: false,
        );

        expect(creating.toPayload()['is_default'], isFalse);
      });

      test('spells booleans out for a multipart body', () {
        const creating = InvoiceTemplateDraft(
          name: 'New',
          primaryColor: '#3B82F6',
          secondaryColor: '#1E40AF',
          isDefault: true,
        );

        expect(creating.toMultipartFields()['is_default'], 'true');
      });
    });

    group('the hex colour rule', () {
      // Mirrors `_validate_hex_color` in the serializer, which is the check
      // that counts. Both directions, since a rule that only ever says yes is
      // the shape that has bitten this codebase before.
      test('accepts six hex digits in either case', () {
        expect(InvoiceTemplateDraft.isHexColor('#3B82F6'), isTrue);
        expect(InvoiceTemplateDraft.isHexColor('#a1b2c3'), isTrue);
        expect(InvoiceTemplateDraft.isHexColor('  #000000  '), isTrue);
      });

      test('refuses a colour name, a short form and a missing hash', () {
        expect(InvoiceTemplateDraft.isHexColor('purple'), isFalse);
        expect(InvoiceTemplateDraft.isHexColor('#abc'), isFalse);
        expect(InvoiceTemplateDraft.isHexColor('3B82F6'), isFalse);
        expect(InvoiceTemplateDraft.isHexColor(''), isFalse);
      });

      test('refuses something that would break the PDF stylesheet', () {
        // `pdf.py` substitutes this straight into the CSS, so the seven
        // characters the column allows are seven characters of stylesheet.
        expect(InvoiceTemplateDraft.isHexColor('}a{b:c'), isFalse);
      });
    });

    group('the writes', () {
      const feed =
          '{"count": 1, "results": [{"id": "t1", "name": "House style"}]}';
      const draft = InvoiceTemplateDraft(
        name: 'House style',
        primaryColor: '#3B82F6',
        secondaryColor: '#1E40AF',
      );

      Future<InvoiceTemplatesNotifier> notifier() async {
        client.body = feed;
        await readTemplates();
        return container.read(invoiceTemplatesProvider.notifier);
      }

      test('an edit PUTs to the template it names', () async {
        final n = await notifier();
        client.sent.clear();

        expect(await n.updateTemplate('t1', draft), isNull);

        final write = client.sent.first;
        expect(write.method, 'PUT');
        expect(write.url.path, endsWith('/invoices/templates/t1/'));
      });

      test('setting the default sends only that flag', () async {
        // A swap, not a field write: the model clears `is_default` on every
        // other row. Sending anything else here would carry stale values from
        // whenever the list was last loaded.
        final n = await notifier();
        client.sent.clear();

        expect(await n.setDefault('t1'), isNull);

        final write = client.sent.first as http.Request;
        expect(jsonDecode(write.body), {'is_default': true});
      });

      test(
        'a refused write returns the server message and does not throw',
        () async {
          final n = await notifier();
          client.status = 403;
          client.body =
              '{"error": true, "message": "Only an administrator can change '
              'invoice templates."}';

          expect(
            await n.createTemplate(draft),
            contains('Only an administrator'),
          );
        },
      );
    });
  });

  group('reports', () {
    // An org invoicing in two currencies. The server never adds them, so every
    // plain money figure is null and the amounts live in `by_currency`.
    const dashboard = '''
    {"summary": {"total_invoiced": null, "total_paid": null, "total_due": null,
                 "by_currency": [
                   {"currency": "EUR", "total_invoiced": "800.00",
                    "total_paid": "500.00", "total_due": "300.00"},
                   {"currency": "USD", "total_invoiced": "10000.00",
                    "total_paid": "6000.00", "total_due": "4000.00"}]},
     "invoice_count": 12, "average_days_to_pay": 21,
     "status_counts": {"Paid": 6, "Overdue": 2},
     "overdue": {"count": 3, "amount": null,
                 "by_currency": [{"currency": "USD", "count": 2,
                                  "amount": "1500.00"},
                                 {"currency": "EUR", "count": 1,
                                  "amount": "90.00"}]},
     "recent_activity": {"revenue_30d": null, "invoiced_30d": null,
                         "by_currency": [{"currency": "USD",
                                          "revenue_30d": "2000.00",
                                          "invoiced_30d": "3000.00"}],
                         "invoices_created_30d": 4, "invoices_paid_30d": 3},
     "estimates": {"pending": 1, "accepted": 2, "declined": 0}}''';

    const aging = '''
    {"current": {"count": 3, "amount": "900.00", "invoices": [],
                 "by_currency": [{"currency": "USD", "count": 3,
                                  "amount": "900.00"}]},
     "1_30_days": {"count": 3, "amount": null, "invoices": [],
                   "by_currency": [{"currency": "EUR", "count": 1,
                                    "amount": "90.00"},
                                   {"currency": "USD", "count": 2,
                                    "amount": "500.00"}]},
     "31_60_days": {"count": 1, "amount": "300.00", "invoices": [],
                    "by_currency": [{"currency": "USD", "count": 1,
                                     "amount": "300.00"}]},
     "61_90_days": {"count": 0, "amount": "0", "invoices": [],
                    "by_currency": []},
     "over_90_days": {"count": 1, "amount": "700.00", "invoices": [],
                      "by_currency": [{"currency": "USD", "count": 1,
                                       "amount": "700.00"}]},
     "overdue": {"count": 5, "amount": null,
                 "by_currency": [{"currency": "EUR", "count": 1,
                                  "amount": "90.00"},
                                 {"currency": "USD", "count": 4,
                                  "amount": "1500.00"}]},
     "total": {"count": 8, "amount": null,
               "by_currency": [{"currency": "EUR", "count": 1,
                                "amount": "90.00"},
                               {"currency": "USD", "count": 7,
                                "amount": "2400.00"}]}}''';

    test('each currency parses on its own, never summed', () async {
      client.routes = {
        'dashboard': (status: 200, body: dashboard),
        'aging': (status: 200, body: aging),
      };
      final reports = await readReports();

      expect(reports.currencies, ['EUR', 'USD']);
      final usd = reports.dashboard.money['USD']!;
      final eur = reports.dashboard.money['EUR']!;
      expect(usd.totalInvoiced, 10000.00);
      expect(eur.totalInvoiced, 800.00);
      expect(usd.overdueCount, 2);
      expect(eur.overdueAmount, 90.00);
      // No EUR activity in the window: zero, not USD's figure.
      expect(eur.revenue30d, 0);
      expect(usd.revenue30d, 2000.00);
      expect(reports.dashboard.averageDaysToPay, 21);
      expect(reports.dashboard.statusCounts['Paid'], 6);

      final usdAging = reports.aging['USD']!;
      final eurAging = reports.aging['EUR']!;
      expect(usdAging.buckets, hasLength(5));
      expect(usdAging.buckets.first.label, 'Not yet due');
      expect(usdAging.buckets[1].amount, 500.00);
      expect(eurAging.buckets[1].amount, 90.00);
      expect(eurAging.buckets.first.amount, 0);
      expect(usdAging.overdue, 1500.00);
      expect(eurAging.overdue, 90.00);
    });

    test('the total is the server figure, not a sum of the buckets', () async {
      client.routes = {
        'dashboard': (status: 200, body: dashboard),
        'aging': (status: 200, body: aging),
      };
      final reports = await readReports();

      // USD buckets add to 2400 here, but the bucket invoice lists are capped
      // at ten each, so the server's own total is what is shown.
      expect(reports.aging['USD']!.total, 2400.00);
      expect(reports.aging['EUR']!.total, 90.00);
    });

    test('a server without the breakdown keeps its plain figures', () async {
      client.routes = {
        'dashboard': (
          status: 200,
          body:
              '{"summary": {"total_invoiced": "10.00"}, '
              '"overdue": {"count": 1, "amount": "4.00"}}',
        ),
        'aging': (
          status: 200,
          body:
              '{"1_30_days": {"count": 1, "amount": "4.00"}, '
              '"total": {"count": 1, "amount": "4.00"}}',
        ),
      };
      final reports = await readReports();

      // Kept under an empty code, which the screen labels in the org currency.
      expect(reports.currencies, ['']);
      expect(reports.dashboard.money['']!.totalInvoiced, 10.00);
      expect(reports.dashboard.money['']!.overdueAmount, 4.00);
      expect(reports.aging['']!.buckets[1].amount, 4.00);
      expect(reports.aging['']!.total, 4.00);
    });

    test('a currency with nothing unpaid ages as all zeros', () {
      expect(AgingReport.empty.buckets, hasLength(5));
      expect(AgingReport.empty.total, 0);
    });

    /// Whatever the provider failed with, or null if it succeeded.
    ///
    /// Reads the `AsyncValue` rather than awaiting `.future`. On the success
    /// path either works, but a `FutureProvider` whose body throws never
    /// completes its `.future` here: the await hangs until the test times out,
    /// and the error that finally surfaces is the container being disposed
    /// rather than the real one. The error state itself is reliable, so the
    /// test settles the event loop and reads that.
    Future<Object?> reportsError() async {
      container.listen(invoiceReportsProvider, (_, _) {}, onError: (_, _) {});
      for (var i = 0; i < 100; i++) {
        final state = container.read(invoiceReportsProvider);
        // `error`, not `!isLoading`. A failed provider settles as
        // `AsyncLoading(error: ...)` here and `isLoading` stays true forever,
        // which is the same reason awaiting `.future` never returns.
        if (state.error != null) return state.error;
        if (state.hasValue) return null;
        await Future<void>.delayed(Duration.zero);
      }
      return null;
    }

    test('a 403 is a distinct failure, not a generic one', () async {
      client.routes = {
        'dashboard': (
          status: 403,
          body:
              '{"error": true, "message": "Only an administrator can view invoice reports."}',
        ),
      };

      // The screen shows "Reports are for administrators" and NO retry button
      // on this, because asking again cannot turn a 403 into a 200.
      expect(await reportsError(), isA<ReportsForbidden>());
    });

    test(
      'a non-403 failure stays an ordinary error, and keeps its retry',
      () async {
        client.routes = {'dashboard': (status: 500, body: '{}')};

        final error = await reportsError();
        expect(error, isNotNull);
        expect(error, isNot(isA<ReportsForbidden>()));
      },
    );
  });
}
