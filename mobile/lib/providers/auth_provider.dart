import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../data/models/auth_response.dart';
import '../services/auth_service.dart';
import 'accounts_provider.dart';
import 'analytics_provider.dart';
import 'approvals_provider.dart';
import 'board_provider.dart';
import 'calendar_feed_provider.dart';
import 'contacts_provider.dart';
import 'dashboard_provider.dart';
import 'deal_pipelines_provider.dart';
import 'deals_provider.dart';
import 'documents_provider.dart';
import 'goals_provider.dart';
import 'help_center_provider.dart';
import 'invoice_extras_provider.dart';
import 'invoices_provider.dart';
import 'lead_board_provider.dart';
import 'lead_pipelines_provider.dart';
import 'leads_provider.dart';
import 'lookup_provider.dart';
import 'notifications_provider.dart';
import 'profile_provider.dart';
import 'settings_provider.dart';
import 'solutions_provider.dart';
import 'support_provider.dart';
import 'tasks_provider.dart';
import 'team_provider.dart';
import 'ticket_board_provider.dart';
import 'tickets_provider.dart';
import 'time_report_provider.dart';
import 'timesheet_provider.dart';
import 'web_forms_provider.dart';
import 'webhooks_provider.dart';

/// Authentication state for the app
class AuthState {
  final AuthUser? user;
  final List<Organization>? organizations;
  final Organization? selectedOrganization;
  final bool isLoading;
  final bool isAuthenticated;
  final String? error;

  const AuthState({
    this.user,
    this.organizations,
    this.selectedOrganization,
    this.isLoading = false,
    this.isAuthenticated = false,
    this.error,
  });

  /// Initial state
  const AuthState.initial()
    : user = null,
      organizations = null,
      selectedOrganization = null,
      isLoading = false,
      isAuthenticated = false,
      error = null;

  /// Check if user needs to select an organization
  bool get needsOrgSelection =>
      isAuthenticated &&
      organizations != null &&
      organizations!.isNotEmpty &&
      selectedOrganization == null;

  /// Create a copy with updated fields
  /// Use [clearSelectedOrganization] to explicitly set selectedOrganization to null
  AuthState copyWith({
    AuthUser? user,
    List<Organization>? organizations,
    Organization? selectedOrganization,
    bool? isLoading,
    bool? isAuthenticated,
    String? error,
    bool clearError = false,
    bool clearSelectedOrganization = false,
  }) {
    return AuthState(
      user: user ?? this.user,
      organizations: organizations ?? this.organizations,
      selectedOrganization: clearSelectedOrganization
          ? null
          : (selectedOrganization ?? this.selectedOrganization),
      isLoading: isLoading ?? this.isLoading,
      isAuthenticated: isAuthenticated ?? this.isAuthenticated,
      error: clearError ? null : (error ?? this.error),
    );
  }

  @override
  String toString() =>
      'AuthState(isAuthenticated: $isAuthenticated, user: ${user?.email}, isLoading: $isLoading)';
}

/// Notifier for authentication state changes
class AuthNotifier extends Notifier<AuthState> {
  final AuthService _authService = AuthService();

  @override
  AuthState build() => const AuthState.initial();

  /// Check if user is already authenticated (on app launch)
  Future<void> checkAuthStatus() async {
    debugPrint('AuthNotifier: Checking auth status...');

    state = state.copyWith(isLoading: true, clearError: true);

    // AuthService.initialize() should have been called before this
    final isLoggedIn = _authService.isLoggedIn;

    state = state.copyWith(
      isLoading: false,
      isAuthenticated: isLoggedIn,
      user: _authService.currentUser,
      organizations: _authService.organizations,
      selectedOrganization: _authService.selectedOrganization,
    );

    debugPrint(
      'AuthNotifier: Auth status checked, isAuthenticated: ${state.isAuthenticated}',
    );
  }

  /// Sign in with Google
  Future<bool> signInWithGoogle() async {
    debugPrint('AuthNotifier: Starting Google sign-in...');

    state = state.copyWith(isLoading: true, clearError: true);

    try {
      final success = await _authService.signInWithGoogle();

      if (success) {
        // Fresh Google sign-in clears selected org - user must select again
        state = state.copyWith(
          isLoading: false,
          isAuthenticated: true,
          user: _authService.currentUser,
          organizations: _authService.organizations,
          clearSelectedOrganization: true,
        );

        debugPrint('AuthNotifier: Google sign-in successful');
        return true;
      } else {
        state = state.copyWith(
          isLoading: false,
          error: 'Sign-in was cancelled or failed. Please try again.',
        );

        debugPrint('AuthNotifier: Google sign-in failed');
        return false;
      }
    } catch (e) {
      debugPrint('AuthNotifier: Google sign-in error: $e');

      state = state.copyWith(
        isLoading: false,
        error: 'An error occurred during sign-in: ${e.toString()}',
      );

      return false;
    }
  }

  /// Sync the cached user's name after a successful profile PATCH so the
  /// greeting/profile header updates without a re-login.
  Future<void> updateUserName(String name) async {
    await _authService.updateCachedUserName(name);
    state = state.copyWith(user: _authService.currentUser);
  }

  /// Request a 6-digit sign-in code by email (mobile OTP flow).
  Future<bool> requestMagicCode(String email) async {
    state = state.copyWith(isLoading: true, clearError: true);
    final ok = await _authService.requestMagicCode(email);
    state = state.copyWith(
      isLoading: false,
      error: ok
          ? null
          : 'Could not send code. Check your connection and try again.',
    );
    return ok;
  }

  /// Verify a 6-digit OTP code and sign in.
  Future<bool> signInWithMagicCode({
    required String email,
    required String code,
  }) async {
    state = state.copyWith(isLoading: true, clearError: true);
    try {
      final ok = await _authService.signInWithMagicCode(
        email: email,
        code: code,
      );
      if (!ok) {
        state = state.copyWith(
          isLoading: false,
          error: 'Invalid or expired code. Please try again.',
        );
        return false;
      }
      state = state.copyWith(
        isLoading: false,
        isAuthenticated: true,
        user: _authService.currentUser,
        organizations: _authService.organizations,
        selectedOrganization: _authService.selectedOrganization,
      );
      return true;
    } catch (e) {
      state = state.copyWith(
        isLoading: false,
        error: 'Sign-in failed: ${e.toString()}',
      );
      return false;
    }
  }

  /// Switch to a different organization
  Future<bool> switchOrganization(Organization org) async {
    debugPrint('AuthNotifier: Switching organization to ${org.name}...');

    state = state.copyWith(isLoading: true, clearError: true);

    try {
      final success = await _authService.selectOrganization(org);

      if (success) {
        // The service's copy, not `org`: the switch re-read the membership
        // facts (role, admin) and `org` may predate them.
        state = state.copyWith(
          isLoading: false,
          organizations: _authService.organizations,
          selectedOrganization: _authService.selectedOrganization,
        );
        _dropSessionCaches();

        debugPrint('AuthNotifier: Organization switched to ${org.name}');
        return true;
      } else {
        state = state.copyWith(
          isLoading: false,
          error: 'Failed to switch organization',
        );

        return false;
      }
    } catch (e) {
      debugPrint('AuthNotifier: Switch organization error: $e');

      state = state.copyWith(
        isLoading: false,
        error: 'An error occurred: ${e.toString()}',
      );

      return false;
    }
  }

  /// Create an organization, then move the session into it.
  ///
  /// Returns null on success, or a message to show. The org list and selected
  /// org both move, so the caller can navigate straight to the dashboard.
  Future<String?> createOrganization(String name, {String? timezone}) async {
    state = state.copyWith(isLoading: true, clearError: true);

    final failure = await _authService.createOrganization(
      name,
      timezone: timezone,
    );

    if (failure != null) {
      state = state.copyWith(isLoading: false, error: failure);
      return failure;
    }

    state = state.copyWith(
      isLoading: false,
      organizations: _authService.organizations,
      selectedOrganization: _authService.selectedOrganization,
    );
    // A fresh org has none of the previous one's records; the same caches the
    // org switch drops have to go here too.
    _dropSessionCaches();
    return null;
  }

  /// Sign out
  Future<void> signOut() async {
    debugPrint('AuthNotifier: Signing out...');

    state = state.copyWith(isLoading: true, clearError: true);

    await _authService.signOut();

    state = const AuthState.initial();
    _dropSessionCaches();

    debugPrint('AuthNotifier: Signed out');
  }

  /// Throw away every cache that belongs to the session that just ended.
  ///
  /// Nothing else did this, so signing out and back in as someone else left
  /// the previous session's rows on screen under the new user's name until
  /// they pulled to refresh. The same applies to an org switch: the rows are
  /// another tenant's and must not survive it.
  ///
  /// Providers are listed explicitly rather than swept, because forgetting one
  /// shows stale data and invalidating an unrelated one only costs a refetch.
  /// Every provider here is kept alive (not autoDispose), so nothing else
  /// would ever drop it. Derived providers that only watch one of these
  /// rebuild on their own and are not listed. A family is listed once and
  /// that drops every member. `session_caches_test.dart` fails when a new
  /// kept-alive provider under `lib/providers/` is missing from this list.
  void _dropSessionCaches() {
    // Records and their boards.
    ref.invalidate(dashboardProvider);
    ref.invalidate(leadsProvider);
    ref.invalidate(leadBoardProvider);
    ref.invalidate(leadPipelinesProvider);
    ref.invalidate(dealsProvider);
    ref.invalidate(dealBoardProvider);
    ref.invalidate(dealPipelinesProvider);
    ref.invalidate(accountsProvider);
    ref.invalidate(contactsProvider);
    ref.invalidate(tasksProvider);
    ref.invalidate(boardProvider);
    ref.invalidate(ticketsProvider);
    ref.invalidate(ticketBoardProvider);
    ref.invalidate(solutionsProvider);
    ref.invalidate(approvalsProvider);
    ref.invalidate(documentsProvider);
    ref.invalidate(invoicesProvider);
    ref.invalidate(estimatesProvider);
    ref.invalidate(recurringProvider);
    ref.invalidate(productsProvider);
    ref.invalidate(invoiceTemplatesProvider);
    ref.invalidate(invoiceReportsProvider);
    ref.invalidate(goalsProvider);
    ref.invalidate(goalHistoryProvider);
    ref.invalidate(goalProvider);
    ref.invalidate(timesheetProvider);
    ref.invalidate(timeReportProvider);
    ref.invalidate(analyticsProvider);
    ref.invalidate(notificationsProvider);
    ref.invalidate(supportTicketsProvider);
    // The member themselves.
    ref.invalidate(profileProvider);
    ref.invalidate(calendarFeedProvider);
    ref.invalidate(myAccessTokensProvider);
    // Pickers.
    ref.invalidate(accountsLookupProvider);
    ref.invalidate(contactsLookupProvider);
    ref.invalidate(usersLookupProvider);
    ref.invalidate(teamsLookupProvider);
    ref.invalidate(tagsLookupProvider);
    ref.invalidate(leadsLookupProvider);
    ref.invalidate(opportunitiesLookupProvider);
    ref.invalidate(ticketsLookupProvider);
    ref.invalidate(customFieldDefinitionsProvider);
    // Org settings.
    ref.invalidate(orgSettingsProvider);
    ref.invalidate(teamProvider);
    ref.invalidate(customFieldsProvider);
    ref.invalidate(tagSettingsProvider);
    ref.invalidate(macrosProvider);
    ref.invalidate(activeMacrosProvider);
    ref.invalidate(routingRulesProvider);
    ref.invalidate(escalationProvider);
    ref.invalidate(businessHoursProvider);
    ref.invalidate(reopenPolicyProvider);
    ref.invalidate(approvalRulesProvider);
    ref.invalidate(mailboxesProvider);
    ref.invalidate(accessTokensProvider);
    ref.invalidate(helpCenterSettingsProvider);
    ref.invalidate(webFormsProvider);
    ref.invalidate(webFormDetailProvider);
    ref.invalidate(webhooksProvider);
    ref.invalidate(webhookDeliveriesProvider);
  }

  /// Clear any error message
  void clearError() {
    state = state.copyWith(clearError: true);
  }
}

/// Provider for authentication state
final authProvider = NotifierProvider<AuthNotifier, AuthState>(
  AuthNotifier.new,
);

/// Provider for checking if user is authenticated
final isAuthenticatedProvider = Provider<bool>((ref) {
  return ref.watch(authProvider).isAuthenticated;
});

/// Provider for current user
final currentUserProvider = Provider<AuthUser?>((ref) {
  return ref.watch(authProvider).user;
});

/// Provider for selected organization
final selectedOrgProvider = Provider<Organization?>((ref) {
  return ref.watch(authProvider).selectedOrganization;
});

/// Whether the signed-in user administers the selected org. The app's one
/// admin rule: every admin gate reads this, never `role`.
///
/// It reads `is_organization_admin`, which the API computes with
/// `is_org_admin` (the ADMIN role, or a Django superuser's membership) and
/// sends with the org, so a superuser holding the USER role gets the admin
/// controls the API already lets them use. UI affordances only: the API
/// checks every one of them again.
final isOrgAdminProvider = Provider<bool>((ref) {
  return ref.watch(selectedOrgProvider)?.isOrganizationAdmin ?? false;
});

/// The signed-in user's email, or null.
///
/// The one identifier this app and the API agree on for a person. Several
/// serializers surface an owner or author as an email rather than an id
/// (`User` carries no display name), so an ownership comparison in the UI has
/// to be made on this. UI affordances only: the API checks them again.
final myEmailProvider = Provider<String?>((ref) {
  return ref.watch(authProvider).user?.email;
});

/// Provider for checking if org selection is needed
final needsOrgSelectionProvider = Provider<bool>((ref) {
  return ref.watch(authProvider).needsOrgSelection;
});
