import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../config/api_config.dart';
import '../data/api_envelope.dart';
import '../data/models/web_form.dart';
import '../services/api_service.dart';

/// Embeddable web forms (issue #634).
///
/// Reading is open to any member of the org; every write is admin-only
/// server-side. `isOrgAdminProvider` is what the screens use to hide controls,
/// and that is a courtesy rather than the boundary: `is_org_admin(request
/// .profile)` in `webforms/views.py` decides, and the mutation methods below
/// return its message rather than swallowing the 403.
///
/// Mutations refresh instead of patching local state. A publish can be refused
/// for reasons this app does not evaluate (the server checks the form's shape
/// as well as its state), and an optimistic flip would show a form as live
/// when it is not. That is the one piece of state on this screen where being
/// wrong means an open endpoint on somebody's website, or a silent one.

/// The list, plus the org-wide totals the server computes.
class WebFormsState {
  const WebFormsState({
    this.forms = const [],
    this.totals = const WebFormTotals(),
    this.truncated = false,
  });

  final List<WebForm> forms;

  /// Counted over every form in the org, not over the page. The list is
  /// paginated, so counting `forms` would be right until the eleventh form and
  /// quietly wrong afterwards.
  final WebFormTotals totals;

  /// Whether the API had more rows than the page asked for. Surfaced rather
  /// than swallowed: a list that silently stops at a cap reads as "that is all
  /// of them".
  final bool truncated;
}

/// Enough forms that no real org is truncated, small enough to stay one page.
const int _listLimit = 100;

class WebFormsNotifier extends AsyncNotifier<WebFormsState> {
  final ApiService _api = ApiService();

  @override
  Future<WebFormsState> build() => _fetch();

  Future<void> refresh() async {
    state = const AsyncValue.loading();
    state = await AsyncValue.guard(_fetch);
  }

  Future<WebFormsState> _fetch() async {
    final response = await _api.get('${ApiConfig.webForms}?limit=$_listLimit');
    if (!response.success || response.data == null) {
      throw Exception(response.message ?? 'Failed to load web forms');
    }
    final body = response.data!;
    final forms = listFromEnvelope(body, const [
      'results',
    ]).map(WebForm.fromJson).toList(growable: false);
    final count = body['count'];
    return WebFormsState(
      forms: forms,
      totals: WebFormTotals.fromJson(
        body['totals'] is Map<String, dynamic>
            ? body['totals'] as Map<String, dynamic>
            : null,
      ),
      truncated: count is int && count > forms.length,
    );
  }

  /// A new form starts as a name and nothing else.
  ///
  /// It is created unpublished and with no fields, because a form with no
  /// email field cannot be published at all and asking for the whole field
  /// list before the form exists would put the editor in two places. Returns
  /// the new id on success, or throws with the server's message.
  Future<String> createWebForm(String name) async {
    final response = await _api.post(ApiConfig.webForms, {'name': name});
    if (!response.success || response.data == null) {
      throw Exception(response.message ?? 'Could not create the form');
    }
    await refresh();
    return response.data!['id']?.toString() ?? '';
  }

  /// PUT, which the view runs with `partial=True`, so this is a partial update
  /// despite the verb. `fields`, when present, replaces the whole list in one
  /// request rather than one request per row.
  Future<String?> updateWebForm(String id, Map<String, dynamic> payload) async {
    final response = await _api.put(ApiConfig.webForm(id), payload);
    if (!response.success) return response.message ?? 'Could not save the form';
    await refresh();
    return null;
  }

  /// Start accepting submissions.
  ///
  /// The 400 body is the whole point of the failure path here: "add an email
  /// field before publishing" is the response, and returning a generic message
  /// instead would throw away the only instruction the person needs.
  Future<String?> publish(String id) async {
    final response = await _api.post(ApiConfig.webFormPublish(id), const {});
    if (!response.success) {
      return response.message ?? 'Could not publish the form';
    }
    await refresh();
    return null;
  }

  Future<String?> unpublish(String id) async {
    final response = await _api.post(ApiConfig.webFormUnpublish(id), const {});
    if (!response.success) {
      return response.message ?? 'Could not unpublish the form';
    }
    await refresh();
    return null;
  }

  /// A hard delete, and it takes the submissions with it (the FK cascades).
  /// Leads it already created stay. There is no soft delete: an unpublished
  /// form already accepts nothing, which is what "switch it off but keep the
  /// history" means.
  Future<String?> removeWebForm(String id) async {
    final response = await _api.delete(ApiConfig.webForm(id));
    if (!response.success) {
      return response.message ?? 'Could not remove the form';
    }
    await refresh();
    return null;
  }
}

final webFormsProvider = AsyncNotifierProvider<WebFormsNotifier, WebFormsState>(
  WebFormsNotifier.new,
);

/// One form with its field list, both embed snippets, its recent submissions
/// and its 30-day analytics.
class WebFormDetail {
  const WebFormDetail({
    required this.form,
    this.submissions = const [],
    this.submissionCount = 0,
    this.analytics,
  });

  final WebForm form;
  final List<WebFormSubmission> submissions;
  final int submissionCount;

  /// Null when the analytics call failed. It is context beside the editor, not
  /// the editor itself, so the screen shows the form without the tiles rather
  /// than an error over the whole page.
  final WebFormAnalytics? analytics;
}

/// The detail fetch, keyed by form id.
///
/// The form itself is not best-effort: a 404 means the id belongs to another
/// org or to nothing, and that is the answer rather than a detail to paper
/// over. The two calls beside it are.
final webFormDetailProvider = FutureProvider.family<WebFormDetail, String>((
  ref,
  id,
) async {
  final api = ApiService();

  final formResponse = await api.get(ApiConfig.webForm(id));
  if (!formResponse.success || formResponse.data == null) {
    throw Exception(formResponse.message ?? 'Failed to load the web form');
  }
  final form = WebForm.fromJson(formResponse.data!);

  var submissions = const <WebFormSubmission>[];
  var submissionCount = 0;
  final submissionsResponse = await api.get(
    '${ApiConfig.webFormSubmissions(id)}?limit=25',
  );
  if (submissionsResponse.success && submissionsResponse.data != null) {
    final body = submissionsResponse.data!;
    submissions = listFromEnvelope(body, const [
      'results',
    ]).map(WebFormSubmission.fromJson).toList(growable: false);
    final count = body['count'];
    submissionCount = count is int ? count : submissions.length;
  }

  WebFormAnalytics? analytics;
  final analyticsResponse = await api.get(ApiConfig.webFormAnalytics(id));
  if (analyticsResponse.success && analyticsResponse.data != null) {
    analytics = WebFormAnalytics.fromJson(analyticsResponse.data!);
  }

  return WebFormDetail(
    form: form,
    submissions: submissions,
    submissionCount: submissionCount,
    analytics: analytics,
  );
});
