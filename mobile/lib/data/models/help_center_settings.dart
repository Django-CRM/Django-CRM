/// The org's public help center: an on/off switch and the address it lives at.
///
/// The public pages themselves are web-only (`/help-center/<slug>` on the web
/// app); this is the admin's switch for them. Everything here comes from
/// `GET /api/org/help-center/`, including [canEdit], which the API derives
/// from the caller's role so the phone never guesses who may write.
library;

class HelpCenterSettings {
  const HelpCenterSettings({
    this.enabled = false,
    this.slug,
    this.publicUrl,
    this.canEdit = false,
  });

  final bool enabled;

  /// Null until an admin chooses one. Stored lowercase by the API.
  final String? slug;

  /// The absolute web address, built by the API from its `FRONTEND_URL`.
  /// Null while there is no slug.
  final String? publicUrl;

  /// Admins and superusers. A courtesy for hiding the form: the API refuses a
  /// member's write whatever this says.
  final bool canEdit;

  /// Whether a stranger following [publicUrl] would find anything.
  bool get isLive => enabled && publicUrl != null;

  factory HelpCenterSettings.fromJson(Map<String, dynamic> json) {
    final slug = json['help_center_slug'];
    final url = json['public_url'];
    return HelpCenterSettings(
      enabled: json['help_center_enabled'] == true,
      slug: slug is String && slug.isNotEmpty ? slug : null,
      publicUrl: url is String && url.isNotEmpty ? url : null,
      canEdit: json['can_edit'] == true,
    );
  }
}
