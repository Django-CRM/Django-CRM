import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../config/api_config.dart';
import '../data/models/help_center_settings.dart';
import '../services/api_service.dart';

/// The public help center switch. Readable by any member; the write is
/// admin-only server-side (`HelpCenterSettingsView.patch`), and the slug rules
/// (format, reserved words, case-insensitive uniqueness, "on needs an address")
/// are all the serializer's, so this sends what was typed and shows its answer.
class HelpCenterSettingsNotifier extends AsyncNotifier<HelpCenterSettings> {
  final ApiService _api = ApiService();

  @override
  Future<HelpCenterSettings> build() => _fetch();

  Future<void> refresh() async {
    state = const AsyncValue.loading();
    state = await AsyncValue.guard(_fetch);
  }

  Future<HelpCenterSettings> _fetch() async {
    final response = await _api.get(ApiConfig.helpCenterSettings);
    if (!response.success || response.data == null) {
      throw Exception(response.message ?? 'Failed to load the help center');
    }
    return HelpCenterSettings.fromJson(response.data!);
  }

  /// Saves both fields at once. Returns null on success, or the sentence to
  /// show. Exactly two keys are sent, so nothing else can ride along.
  Future<String?> save({required bool enabled, required String slug}) async {
    final response = await _api.patch(ApiConfig.helpCenterSettings, {
      'help_center_enabled': enabled,
      'help_center_slug': slug.trim(),
    });
    if (!response.success) {
      if (response.statusCode == 403) {
        return 'Only an admin can change the help center.';
      }
      return saveErrorText(response.message);
    }
    state = AsyncValue.data(HelpCenterSettings.fromJson(response.data!));
    return null;
  }
}

/// Every rule on this form is about the address, so the API's field label is
/// dropped and the sentence it wrote is shown on its own, as the web does.
String saveErrorText(String? message) {
  const label = 'Help center slug: ';
  final text = (message ?? '').trim();
  if (text.isEmpty) return 'Could not save the help center.';
  return text.startsWith(label) ? text.substring(label.length) : text;
}

final helpCenterSettingsProvider =
    AsyncNotifierProvider<HelpCenterSettingsNotifier, HelpCenterSettings>(
      HelpCenterSettingsNotifier.new,
    );
