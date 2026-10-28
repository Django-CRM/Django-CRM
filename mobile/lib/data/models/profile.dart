import '../api/schema.g.dart' as api;
import 'auth_response.dart';

/// User's profile within the current organization.
///
/// Returned by GET /api/auth/profile/ (ProfileDetailSerializer).
/// The mobile app can only PATCH the `phone` field via /api/profile/.
/// Other fields are display-only by API contract.
class Profile {
  final String id;
  final AuthUser user;
  final Organization? org;
  final String? role;
  final bool isOrganizationAdmin;
  final bool hasSalesAccess;
  final bool hasMarketingAccess;
  final String? phone;
  final DateTime? dateOfJoining;
  final bool isActive;

  /// The teams you are on, by name. Only ever your own: the endpoint
  /// serializes `request.profile` and takes no id.
  final List<String> teams;

  /// When you last signed in. Null on a profile that never has, and on any
  /// payload from a build older than this field.
  final DateTime? lastLogin;

  /// The two lead alert switches, which default to on.
  final bool leadAlertsEmail;
  final bool leadAlertsPush;

  const Profile({
    required this.id,
    required this.user,
    this.org,
    this.role,
    this.isOrganizationAdmin = false,
    this.hasSalesAccess = false,
    this.hasMarketingAccess = false,
    this.phone,
    this.dateOfJoining,
    this.isActive = true,
    this.teams = const [],
    this.lastLogin,
    this.leadAlertsEmail = true,
    this.leadAlertsPush = true,
  });

  /// Reads a `ProfileDetail`. A profile without an id or user throws.
  factory Profile.fromJson(Map<String, dynamic> json) {
    final w = api.ProfileDetail.fromJson(json);
    final org = w.org;
    return Profile(
      id: w.id!,
      user: AuthUser.fromJson(w.user!.json),
      org: org == null ? null : Organization.fromJson(org.json),
      role: w.role,
      isOrganizationAdmin: w.isOrganizationAdmin ?? false,
      hasSalesAccess: w.hasSalesAccess ?? false,
      hasMarketingAccess: w.hasMarketingAccess ?? false,
      phone: w.phone,
      dateOfJoining: DateTime.tryParse(w.dateOfJoining ?? ''),
      isActive: w.isActive ?? true,
      teams: w.teams ?? const [],
      lastLogin: DateTime.tryParse(w.lastLogin ?? ''),
      leadAlertsEmail: w.leadAlertsEmail ?? true,
      leadAlertsPush: w.leadAlertsPush ?? true,
    );
  }

  Profile copyWith({
    String? phone,
    bool? leadAlertsEmail,
    bool? leadAlertsPush,
  }) {
    return Profile(
      id: id,
      user: user,
      org: org,
      role: role,
      isOrganizationAdmin: isOrganizationAdmin,
      hasSalesAccess: hasSalesAccess,
      hasMarketingAccess: hasMarketingAccess,
      phone: phone ?? this.phone,
      dateOfJoining: dateOfJoining,
      isActive: isActive,
      teams: teams,
      lastLogin: lastLogin,
      leadAlertsEmail: leadAlertsEmail ?? this.leadAlertsEmail,
      leadAlertsPush: leadAlertsPush ?? this.leadAlertsPush,
    );
  }
}
