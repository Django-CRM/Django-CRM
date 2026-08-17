import 'package:bottle_crm/config/api_config.dart';
import 'package:bottle_crm/data/models/web_form.dart';
import 'package:flutter_test/flutter_test.dart';

/// Embeddable web forms (issue #634).
///
/// The things worth pinning here are the ones where getting it wrong is
/// invisible until a stranger is refused on somebody's website:
///
/// - `captcha_secret` is write-only on the serializer, so it never comes back.
///   A `toJson` that emitted an empty string for it would blank a working
///   Turnstile secret every time an unrelated setting was saved, and because
///   the value is never read back nothing would show that it had happened.
///   Turnstile fails closed, so the next visitor is simply refused.
/// - `order` decides what a visitor is asked and when. The server assigns it
///   from list position, so the client's job is to send the list in the right
///   order, not to send its own numbers.
/// - `is_published` is what separates a form that collects leads from one that
///   collects nothing. Defaulting it to anything but false on a malformed
///   payload would be a form that looks live and is not, or vice versa.
void main() {
  group('WebForm.fromJson', () {
    test('reads a full payload', () {
      final form = WebForm.fromJson({
        'id': 'f1',
        'name': 'Contact us',
        'is_published': true,
        'allowed_origins': ['https://example.com'],
        'submit_button_label': 'Send',
        'success_mode': 'redirect',
        'success_message': 'Thanks.',
        'redirect_url': 'https://example.com/thanks',
        'assign_to': 'p1',
        'notify_profiles': ['p1', 'p2'],
        'lead_source': 'other',
        'tags': ['t1'],
        'captcha_provider': 'turnstile',
        'captcha_site_key': 'site-key',
        'has_captcha_secret': true,
        'reject_disposable_email': true,
        'embed_html': '<iframe src="https://api.example.com/…"></iframe>',
        'embed_js': '<script src="https://api.example.com/…"></script>',
        'fields': [
          {
            'id': 'wf1',
            'source': 'lead',
            'lead_field': 'email',
            'label': 'Email',
            'is_required': true,
          },
        ],
      });

      expect(form.id, 'f1');
      expect(form.name, 'Contact us');
      expect(form.isPublished, isTrue);
      expect(form.allowedOrigins, ['https://example.com']);
      expect(form.submitButtonLabel, 'Send');
      expect(form.successMode, WebForm.successRedirect);
      expect(form.redirectUrl, 'https://example.com/thanks');
      expect(form.assignTo, 'p1');
      expect(form.notifyProfiles, ['p1', 'p2']);
      expect(form.leadSource, 'other');
      expect(form.tags, ['t1']);
      expect(form.captchaProvider, 'turnstile');
      expect(form.captchaSiteKey, 'site-key');
      expect(form.hasCaptchaSecret, isTrue);
      expect(form.rejectDisposableEmail, isTrue);
      expect(form.embedHtml, contains('<iframe'));
      expect(form.embedJs, contains('<script'));
      expect(form.fields.single.leadField, 'email');
    });

    test('tolerates every optional key being absent', () {
      final form = WebForm.fromJson({'id': 'f1'});

      expect(form.name, '');
      expect(form.isPublished, isFalse);
      expect(form.allowedOrigins, isEmpty);
      expect(form.fields, isEmpty);
      expect(form.notifyProfiles, isEmpty);
      expect(form.tags, isEmpty);
      expect(form.assignTo, isNull);
      expect(form.hasCaptchaSecret, isFalse);
      expect(form.successMode, WebForm.successMessage);
    });

    test('is_published defaults to false, never null', () {
      // A form that looks published and is not collects nothing while
      // appearing to work. The other direction is worse: an admin who thinks
      // a form is off leaves an open endpoint on their site.
      expect(WebForm.fromJson({'id': 'f1'}).isPublished, isFalse);
      expect(
        WebForm.fromJson({'id': 'f1', 'is_published': null}).isPublished,
        isFalse,
      );
    });

    test('sorts fields by order even when the server sends them out of it', () {
      final form = WebForm.fromJson({
        'id': 'f1',
        'fields': [
          {
            'source': 'lead',
            'lead_field': 'phone',
            'label': 'Phone',
            'order': 2,
          },
          {
            'source': 'lead',
            'lead_field': 'email',
            'label': 'Email',
            'order': 0,
          },
          {
            'source': 'lead',
            'lead_field': 'first_name',
            'label': 'First',
            'order': 1,
          },
        ],
      });

      expect(form.fields.map((f) => f.leadField), [
        'email',
        'first_name',
        'phone',
      ]);
    });

    test('keeps the given order when no order key is present', () {
      // The list endpoint orders by `order` already, so an absent key means
      // "as sent". Falling back to 0 for all of them and then sorting would be
      // a stable no-op, which is exactly what is wanted; this pins it.
      final form = WebForm.fromJson({
        'id': 'f1',
        'fields': [
          {'source': 'lead', 'lead_field': 'email', 'label': 'Email'},
          {'source': 'lead', 'lead_field': 'phone', 'label': 'Phone'},
        ],
      });

      expect(form.fields.map((f) => f.leadField), ['email', 'phone']);
    });
  });

  group('WebFormField', () {
    test('reads a custom-field row', () {
      final field = WebFormField.fromJson({
        'id': 'wf1',
        'source': 'custom',
        'custom_field': 'cf1',
        'label': 'Budget',
        'placeholder': 'e.g. 5000',
        'is_required': false,
      });

      expect(field.isCustom, isTrue);
      expect(field.customField, 'cf1');
      expect(field.leadField, '');
      expect(field.placeholder, 'e.g. 5000');
      expect(field.isRequired, isFalse);
    });

    test('a row is complete only with a label and a target', () {
      // Mirrors `isFieldComplete` in
      // `frontend/src/lib/v2/webform-fields.js`, and behind both of them the
      // `web_form_field_exactly_one_target` check constraint.
      const withTarget = WebFormField(
        source: WebFormField.sourceLead,
        leadField: 'email',
        label: 'Email',
      );
      const noLabel = WebFormField(
        source: WebFormField.sourceLead,
        leadField: 'email',
      );
      const noTarget = WebFormField(
        source: WebFormField.sourceLead,
        label: 'Email',
      );
      const customNoDefinition = WebFormField(
        source: WebFormField.sourceCustom,
        label: 'Budget',
      );

      expect(withTarget.isComplete, isTrue);
      expect(noLabel.isComplete, isFalse);
      expect(noTarget.isComplete, isFalse);
      expect(customNoDefinition.isComplete, isFalse);
    });

    test('emits only the keys the field serializer accepts', () {
      const field = WebFormField(
        id: 'wf1',
        source: WebFormField.sourceLead,
        leadField: 'email',
        label: 'Email',
        isRequired: true,
      );

      // `order` is absent on purpose: the server assigns it from list
      // position, so a client sending its own could not reorder anything by
      // lying about it, and including it would suggest otherwise. `id` is
      // read-only; the list is replaced wholesale on every save.
      expect(field.toJson().keys.toSet(), {
        'source',
        'lead_field',
        'custom_field',
        'label',
        'placeholder',
        'is_required',
      });
    });
  });

  group('WebForm.toJson', () {
    test('round-trips the fields the update serializer accepts', () {
      final form = WebForm.fromJson({
        'id': 'f1',
        'name': 'Contact us',
        'submit_button_label': 'Send',
        'success_mode': 'message',
        'success_message': 'Thanks.',
        'lead_source': 'other',
        'allowed_origins': ['https://example.com'],
        'reject_disposable_email': true,
        'fields': [
          {'source': 'lead', 'lead_field': 'email', 'label': 'Email'},
        ],
      });

      final json = form.toJson();

      expect(json['name'], 'Contact us');
      expect(json['submit_button_label'], 'Send');
      expect(json['success_mode'], 'message');
      expect(json['allowed_origins'], ['https://example.com']);
      expect(json['reject_disposable_email'], isTrue);
      expect((json['fields'] as List).single['lead_field'], 'email');
    });

    test('never emits is_published', () {
      // Read-only on the serializer, so sending it is silently ignored rather
      // than rejected. That silence is the trap: a client that sent it would
      // appear to publish and would never publish anything. Publishing has its
      // own endpoint, which validates the source state and the form's shape.
      final form = WebForm.fromJson({'id': 'f1', 'is_published': true});
      expect(form.toJson().containsKey('is_published'), isFalse);
    });

    test('omits captcha_secret when it was not edited', () {
      final form = WebForm.fromJson({
        'id': 'f1',
        'captcha_provider': 'turnstile',
        'has_captcha_secret': true,
      });

      expect(form.toJson().containsKey('captcha_secret'), isFalse);
    });

    test('includes captcha_secret only when one was typed', () {
      final form = WebForm.fromJson({'id': 'f1'});

      expect(
        form.toJson(captchaSecret: 'new-secret')['captcha_secret'],
        'new-secret',
      );
      // Blank and whitespace both mean "leave the stored one alone", because
      // the box is rendered empty every time (the value is never returned).
      expect(
        form.toJson(captchaSecret: '').containsKey('captcha_secret'),
        isFalse,
      );
      expect(
        form.toJson(captchaSecret: '   ').containsKey('captcha_secret'),
        isFalse,
      );
    });

    test('sends the field list in list order', () {
      final form = WebForm.fromJson({
        'id': 'f1',
        'fields': [
          {
            'source': 'lead',
            'lead_field': 'email',
            'label': 'Email',
            'order': 0,
          },
          {
            'source': 'lead',
            'lead_field': 'phone',
            'label': 'Phone',
            'order': 1,
          },
        ],
      });

      final reordered = form.copyWith(fields: [form.fields[1], form.fields[0]]);

      expect(
        (reordered.toJson()['fields'] as List).map((f) => f['lead_field']),
        ['phone', 'email'],
      );
    });
  });

  group('WebFormSubmission.fromJson', () {
    test('reads an accepted submission', () {
      final submission = WebFormSubmission.fromJson({
        'id': 's1',
        'created_at': '2026-08-17T09:00:00Z',
        'status': 'accepted',
        'lead': 'l1',
        'lead_name': 'Dana',
        'submitted_ip': '203.0.113.5',
        'referer': 'https://example.com/contact',
      });

      expect(submission.status, 'accepted');
      expect(submission.isAccepted, isTrue);
      expect(submission.leadId, 'l1');
      expect(submission.leadName, 'Dana');
    });

    test('counts a merged duplicate as accepted', () {
      // A returning visitor whose details merged into an existing lead reached
      // the org just as surely as a new one.
      final submission = WebFormSubmission.fromJson({
        'id': 's1',
        'status': 'accepted_duplicate',
      });

      expect(submission.isAccepted, isTrue);
    });

    test('a rejected submission is not accepted and has no lead', () {
      final submission = WebFormSubmission.fromJson({
        'id': 's1',
        'status': 'rejected_spam',
      });

      expect(submission.isAccepted, isFalse);
      expect(submission.leadId, isNull);
    });

    test('never carries a reject reason', () {
      // The API leaves `reject_reason` out of the response on purpose: naming
      // the control that caught a bot is how the next bot gets past it. If a
      // field for it appeared here, somebody would wire it to a screen.
      final submission = WebFormSubmission.fromJson({
        'id': 's1',
        'status': 'rejected_spam',
        'reject_reason': 'honeypot',
      });

      expect(submission.toString().contains('honeypot'), isFalse);
    });
  });

  group('ApiConfig web form routes', () {
    test('point at the authenticated management endpoints', () {
      expect(ApiConfig.webForms, endsWith('/webforms/'));
      expect(ApiConfig.webForm('f1'), endsWith('/webforms/f1/'));
      expect(ApiConfig.webFormPublish('f1'), endsWith('/webforms/f1/publish/'));
      expect(
        ApiConfig.webFormUnpublish('f1'),
        endsWith('/webforms/f1/unpublish/'),
      );
      expect(
        ApiConfig.webFormSubmissions('f1'),
        endsWith('/webforms/f1/submissions/'),
      );
      expect(
        ApiConfig.webFormAnalytics('f1'),
        endsWith('/webforms/f1/analytics/'),
      );
    });

    test('none of them is the anonymous public endpoint', () {
      // `/api/public/forms/…` is called by a visitor's browser, never by this
      // app, and it takes no credential. A route here pointing at it would be
      // this app sending a JWT to an endpoint that does not want one.
      for (final url in [
        ApiConfig.webForms,
        ApiConfig.webForm('f1'),
        ApiConfig.webFormPublish('f1'),
        ApiConfig.webFormSubmissions('f1'),
        ApiConfig.webFormAnalytics('f1'),
      ]) {
        expect(url, isNot(contains('/public/')));
      }
    });
  });

  group('WEBFORM_LEAD_FIELDS', () {
    test('mirrors the backend whitelist, in the backend order', () {
      // `backend/webforms/constants.py::LEAD_FIELD_CHOICES`, and
      // `frontend/src/lib/v2/webform-fields.js` carries the same list.
      // `validate_lead_field` rejects anything outside it, so a stale entry
      // here is a clean 400 rather than a bad write, but the picker would
      // still be wrong.
      expect(webFormLeadFields.map((f) => f.value), [
        'salutation',
        'first_name',
        'last_name',
        'email',
        'phone',
        'company_name',
        'job_title',
        'website',
        'title',
        'description',
        'city',
        'state',
        'country',
        'postcode',
        'industry',
      ]);
    });

    test('labels title as Subject and keeps Salutation separate', () {
      expect(leadFieldLabel('title'), 'Subject');
      expect(leadFieldLabel('salutation'), 'Salutation');
    });

    test('falls back to the raw value for an unknown field', () {
      expect(leadFieldLabel('invented_field'), 'invented_field');
    });
  });

  group('publishing rules', () {
    test('a form with an email field can be published', () {
      const fields = [
        WebFormField(
          source: WebFormField.sourceLead,
          leadField: 'email',
          label: 'Email',
        ),
      ];
      expect(hasRequiredField(fields), isTrue);
    });

    test('a form without one cannot', () {
      const fields = [
        WebFormField(
          source: WebFormField.sourceLead,
          leadField: 'phone',
          label: 'Phone',
        ),
      ];
      expect(hasRequiredField(fields), isFalse);
      expect(hasRequiredField(const []), isFalse);
    });

    test('a custom field labelled Email does not satisfy the rule', () {
      // `WebFormPublishView` filters on `lead_field`, so matching that exactly
      // keeps this screen's explanation from contradicting the server's 400.
      const fields = [
        WebFormField(
          source: WebFormField.sourceCustom,
          customField: 'cf1',
          label: 'Email',
        ),
      ];
      expect(hasRequiredField(fields), isFalse);
    });
  });
}
