import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

import '../providers/auth_provider.dart';

// Auth Screens
import '../screens/auth/splash_screen.dart';
import '../screens/auth/login_screen.dart';
import '../screens/auth/magic_link_email_screen.dart';
import '../screens/auth/magic_link_code_screen.dart';
import '../screens/accounts/account_detail_screen.dart';
import '../screens/accounts/account_form_screen.dart';
import '../screens/accounts/accounts_list_screen.dart';
import '../screens/auth/org_create_screen.dart';
import '../screens/contacts/contact_detail_screen.dart';
import '../screens/contacts/contact_form_screen.dart';
import '../screens/contacts/contacts_list_screen.dart';
import '../screens/auth/org_selection_screen.dart';

// Main Screens
import '../screens/dashboard/dashboard_screen.dart';
import '../screens/leads/leads_list_screen.dart';
import '../screens/leads/lead_detail_screen.dart';
import '../screens/leads/lead_create_screen.dart';
import '../screens/leads/lead_form_screen.dart';
import '../screens/deals/deals_list_screen.dart';
import '../screens/deals/deal_detail_screen.dart';
import '../screens/deals/deal_form_screen.dart';
import '../screens/tickets/tickets_list_screen.dart';
import '../screens/tickets/ticket_detail_screen.dart';
import '../screens/tickets/ticket_create_screen.dart';
import '../screens/tickets/ticket_form_screen.dart';
import '../screens/tasks/board_screen.dart';
import '../screens/tasks/tasks_list_screen.dart';
import '../screens/tasks/task_detail_screen.dart';
import '../screens/tasks/task_form_screen.dart';
import '../screens/settings/custom_fields_screen.dart';
import '../screens/settings/macros_screen.dart';
import '../screens/settings/more_screen.dart';
import '../screens/settings/profile_screen.dart';
import '../screens/settings/api_tokens_screen.dart';
import '../screens/settings/my_api_tokens_screen.dart';
import '../screens/settings/approval_rules_screen.dart';
import '../screens/settings/business_hours_screen.dart';
import '../screens/settings/escalation_screen.dart';
import '../screens/settings/inbound_email_screen.dart';
import '../screens/settings/organization_edit_screen.dart';
import '../screens/settings/organization_screen.dart';
import '../screens/settings/reopen_screen.dart';
import '../screens/settings/routing_screen.dart';
import '../screens/settings/settings_hub_screen.dart';
import '../screens/settings/tags_screen.dart';
import '../screens/settings/team_screen.dart';
import '../screens/solutions/solutions_list_screen.dart';
import '../screens/solutions/solution_detail_screen.dart';
import '../screens/tickets/approvals_inbox_screen.dart';
import '../screens/tickets/ticket_analytics_screen.dart';
import '../screens/timesheet/timesheet_screen.dart';
import '../screens/notifications/notifications_screen.dart';
import '../screens/documents/documents_list_screen.dart';
import '../screens/documents/document_form_screen.dart';
import '../screens/goals/goals_screen.dart';
import '../screens/goals/goal_form_screen.dart';
import '../screens/invoices/invoices_list_screen.dart';
import '../screens/invoices/invoice_detail_screen.dart';
import '../screens/invoices/estimates_list_screen.dart';
import '../screens/invoices/recurring_list_screen.dart';
import '../screens/invoices/products_list_screen.dart';
import '../screens/invoices/invoice_template_form_screen.dart';
import '../screens/invoices/invoice_templates_screen.dart';
import '../screens/invoices/invoice_reports_screen.dart';
import '../screens/invoices/new_invoice_screen.dart';
import '../screens/invoices/new_recurring_screen.dart';
import '../screens/support/support_create_screen.dart';
import '../screens/support/support_detail_screen.dart';
import '../screens/support/support_list_screen.dart';

// Shell
import '../widgets/common/app_shell.dart';

/// App Routes
class AppRoutes {
  AppRoutes._();

  // Auth routes
  static const String splash = '/splash';
  static const String login = '/login';
  static const String magicLinkEmail = '/magic-link/email';
  static const String magicLinkCode = '/magic-link/code';
  static const String orgSelection = '/org-selection';
  static const String orgCreate = '/org-selection/new';

  // Main routes
  static const String dashboard = '/dashboard';
  static const String leads = '/leads';
  static const String leadDetail = '/leads/:id';
  static const String leadCreate = '/leads/create';
  static const String leadEdit = '/leads/:id/edit';
  static const String deals = '/deals';
  static const String dealDetail = '/deals/:id';
  static const String dealCreate = '/deals/create';
  static const String tickets = '/tickets';
  static const String ticketDetail = '/tickets/:id';
  static const String ticketCreate = '/tickets/create';
  static const String ticketEdit = '/tickets/:id/edit';
  static const String tasks = '/tasks';
  static const String taskBoard = '/tasks/board';
  static const String taskDetail = '/tasks/:id';
  static const String taskCreate = '/tasks/create';
  static const String taskEdit = '/tasks/:id/edit';
  static const String accounts = '/accounts';
  static const String accountCreate = '/accounts/create';
  static const String accountDetail = '/accounts/:id';
  static const String accountEdit = '/accounts/:id/edit';
  static const String contacts = '/contacts';
  static const String contactCreate = '/contacts/create';
  static const String contactDetail = '/contacts/:id';
  static const String contactEdit = '/contacts/:id/edit';
  static const String more = '/more';
  static const String profile = '/more/profile';
  static const String team = '/more/team';

  /// The org settings cluster. Two segments deep under `/more`, matching
  /// `profile` and `team`, so nothing here can be mistaken for a record id the
  /// way `/invoices/estimates` once could.
  static const String settings = '/more/settings';
  static const String settingsCustomFields = '/more/settings/custom-fields';
  static const String settingsMacros = '/more/settings/macros';
  static const String settingsTags = '/more/settings/tags';
  static const String settingsRouting = '/more/settings/routing';
  static const String settingsEscalation = '/more/settings/escalation';
  static const String settingsBusinessHours = '/more/settings/business-hours';
  static const String settingsReopen = '/more/settings/reopen';
  static const String settingsInboundEmail = '/more/settings/inbound-email';
  static const String settingsTicketApprovals =
      '/more/settings/ticket-approvals';
  static const String settingsApiTokens = '/more/settings/api-tokens';

  /// Your OWN tokens, under profile rather than under settings. Settings
  /// holds the admin's org-wide oversight list, which 403s a member; this
  /// is the self-scoped half every member may use.
  static const String profileTokens = '/more/profile/tokens';
  static const String settingsOrganization = '/more/settings/organization';
  static const String settingsOrganizationEdit =
      '/more/settings/organization/edit';

  /// Your own week of logged time. Not under `/more`: it is a workspace
  /// destination reached from the dashboard as often as from the menu, and a
  /// path that says where it lives is the one that survives being linked to.
  static const String timesheet = '/timesheet';

  /// The notification feed. Matches the web app's `/notifications`.
  static const String notifications = '/notifications';

  /// Shared files. Matches the web app's `/documents`. There is no detail
  /// route on either client: the list says everything a document row holds,
  /// and the edit form is where the writes live.
  static const String documents = '/documents';
  static const String documentNew = '/documents/new';
  static const String documentEdit = '/documents/:id/edit';

  static String documentEditFor(String id) => '/documents/$id/edit';

  /// Sales goals. Matches the web app's `/goals`. Same shape as documents,
  /// and for the same reason.
  static const String goals = '/goals';
  static const String goalNew = '/goals/new';
  static const String goalEdit = '/goals/:id/edit';

  static String goalEditFor(String id) => '/goals/$id/edit';

  /// Help: product support from the BottleCRM team. The URL says help, the
  /// API behind it is `/api/support/`, and the screens keep the support name.
  /// Separate from CRM customer tickets, which live at `/tickets`.
  static const String help = '/help';
  static const String helpNew = '/help/new';
  static const String helpDetail = '/help/:id';

  /// Invoices. Matches the web app's `/invoices` and `/invoices/[id]`.
  static const String invoices = '/invoices';
  static const String invoiceDetail = '/invoices/:id';

  /// The sibling pages under invoices, matching the web's own paths. Declared
  /// BEFORE `invoiceDetail` in the route table: `/invoices/:id` would swallow
  /// every one of these as an id otherwise.
  static const String invoiceNew = '/invoices/new';
  static const String recurringNew = '/invoices/recurring/new';
  static const String estimates = '/invoices/estimates';
  static const String recurring = '/invoices/recurring';
  static const String products = '/invoices/products';
  static const String invoiceTemplates = '/invoices/templates';
  static const String invoiceTemplateNew = '/invoices/templates/new';
  static const String invoiceTemplateEdit = '/invoices/templates/:id/edit';
  static const String invoiceReports = '/invoices/reports';

  static String invoiceTemplateEditFor(String id) =>
      '/invoices/templates/$id/edit';

  // Knowledge base
  static const String solutions = '/solutions';
  static const String solutionNew = '/solutions/new';
  static const String solutionDetail = '/solutions/:id';

  // Approvals
  static const String approvalsInbox = '/tickets/approvals';

  // Analytics
  static const String ticketAnalytics = '/tickets/analytics';

  /// Values for the `?view=` parameter on the leads and tasks lists. Each one
  /// opens the list already narrowed to what one dashboard badge counted, so
  /// the badge and the list it opens agree on a number. The filter itself is
  /// built by `TaskFilters` / `LeadFilters`, which is where the definition
  /// lives; these are just the names carried through the URL.
  static const String viewOverdue = 'overdue';
  static const String viewDueToday = 'due-today';
  static const String viewFollowUps = 'follow-ups';
  static const String viewHot = 'hot';
}

/// Navigation shell key for bottom navigation
final _rootNavigatorKey = GlobalKey<NavigatorState>();

/// Auth routes that don't require authentication
const _publicRoutes = [
  AppRoutes.splash,
  AppRoutes.login,
  AppRoutes.magicLinkEmail,
  AppRoutes.magicLinkCode,
];

/// Routes that require authentication but not org selection
// Reachable with a session but no org: the picker, and the screen that
// creates the org the picker has none of.
const _authOnlyRoutes = [AppRoutes.orgSelection, AppRoutes.orgCreate];

/// Router Provider
final appRouterProvider = Provider<GoRouter>((ref) {
  return GoRouter(
    navigatorKey: _rootNavigatorKey,
    initialLocation: AppRoutes.splash,
    debugLogDiagnostics: true,
    redirect: (context, state) {
      final authState = ref.read(authProvider);
      final isAuthenticated = authState.isAuthenticated;
      final needsOrgSelection = authState.needsOrgSelection;
      final currentPath = state.matchedLocation;

      // Allow public routes
      if (_publicRoutes.contains(currentPath)) {
        return null;
      }

      // Redirect to login if not authenticated
      if (!isAuthenticated) {
        return AppRoutes.login;
      }

      // Handle org selection routes
      if (_authOnlyRoutes.contains(currentPath)) {
        // Already on org selection, allow it
        return null;
      }

      // Redirect to org selection if needed
      if (needsOrgSelection) {
        return AppRoutes.orgSelection;
      }

      return null;
    },
    routes: [
      // ============================================
      // AUTH ROUTES (No bottom navigation)
      // ============================================
      GoRoute(
        path: AppRoutes.splash,
        name: 'splash',
        pageBuilder: (context, state) => CustomTransitionPage(
          key: state.pageKey,
          child: const SplashScreen(),
          transitionsBuilder: (context, animation, secondaryAnimation, child) {
            return FadeTransition(opacity: animation, child: child);
          },
        ),
      ),
      GoRoute(
        path: AppRoutes.login,
        name: 'login',
        pageBuilder: (context, state) => CustomTransitionPage(
          key: state.pageKey,
          child: const LoginScreen(),
          transitionsBuilder: (context, animation, secondaryAnimation, child) {
            return FadeTransition(opacity: animation, child: child);
          },
        ),
      ),
      GoRoute(
        path: AppRoutes.magicLinkEmail,
        name: 'magicLinkEmail',
        builder: (context, state) => const MagicLinkEmailScreen(),
      ),
      GoRoute(
        path: AppRoutes.magicLinkCode,
        name: 'magicLinkCode',
        builder: (context, state) {
          final email = state.uri.queryParameters['email'] ?? '';
          return MagicLinkCodeScreen(email: email);
        },
      ),
      GoRoute(
        path: AppRoutes.solutions,
        name: 'solutions',
        parentNavigatorKey: _rootNavigatorKey,
        builder: (context, state) => const SolutionsListScreen(),
      ),
      GoRoute(
        path: AppRoutes.solutionNew,
        name: 'solutionNew',
        parentNavigatorKey: _rootNavigatorKey,
        builder: (context, state) => const SolutionDetailScreen(),
      ),
      GoRoute(
        path: AppRoutes.solutionDetail,
        name: 'solutionDetail',
        parentNavigatorKey: _rootNavigatorKey,
        builder: (context, state) {
          final id = state.pathParameters['id']!;
          return SolutionDetailScreen(solutionId: id);
        },
      ),
      GoRoute(
        path: AppRoutes.accounts,
        name: 'accounts',
        parentNavigatorKey: _rootNavigatorKey,
        builder: (context, state) => const AccountsListScreen(),
      ),
      GoRoute(
        path: AppRoutes.accountCreate,
        name: 'accountCreate',
        parentNavigatorKey: _rootNavigatorKey,
        builder: (context, state) => const AccountFormScreen(),
      ),
      GoRoute(
        path: AppRoutes.accountEdit,
        name: 'accountEdit',
        parentNavigatorKey: _rootNavigatorKey,
        builder: (context, state) =>
            AccountFormScreen(accountId: state.pathParameters['id']!),
      ),
      // Registered after `create` and `:id/edit` so those literal segments are
      // not swallowed by the `:id` parameter.
      GoRoute(
        path: AppRoutes.accountDetail,
        name: 'accountDetail',
        parentNavigatorKey: _rootNavigatorKey,
        builder: (context, state) =>
            AccountDetailScreen(accountId: state.pathParameters['id']!),
      ),
      GoRoute(
        path: AppRoutes.contacts,
        name: 'contacts',
        parentNavigatorKey: _rootNavigatorKey,
        builder: (context, state) => const ContactsListScreen(),
      ),
      GoRoute(
        path: AppRoutes.contactCreate,
        name: 'contactCreate',
        parentNavigatorKey: _rootNavigatorKey,
        builder: (context, state) => const ContactFormScreen(),
      ),
      GoRoute(
        path: AppRoutes.contactEdit,
        name: 'contactEdit',
        parentNavigatorKey: _rootNavigatorKey,
        builder: (context, state) =>
            ContactFormScreen(contactId: state.pathParameters['id']!),
      ),
      GoRoute(
        path: AppRoutes.contactDetail,
        name: 'contactDetail',
        parentNavigatorKey: _rootNavigatorKey,
        builder: (context, state) =>
            ContactDetailScreen(contactId: state.pathParameters['id']!),
      ),
      GoRoute(
        path: AppRoutes.approvalsInbox,
        name: 'approvalsInbox',
        parentNavigatorKey: _rootNavigatorKey,
        builder: (context, state) => const ApprovalsInboxScreen(),
      ),
      GoRoute(
        path: AppRoutes.ticketAnalytics,
        name: 'ticketAnalytics',
        parentNavigatorKey: _rootNavigatorKey,
        builder: (context, state) => const TicketAnalyticsScreen(),
      ),
      GoRoute(
        path: AppRoutes.timesheet,
        name: 'timesheet',
        parentNavigatorKey: _rootNavigatorKey,
        builder: (context, state) => const TimesheetScreen(),
      ),
      GoRoute(
        path: AppRoutes.notifications,
        name: 'notifications',
        parentNavigatorKey: _rootNavigatorKey,
        builder: (context, state) => const NotificationsScreen(),
      ),
      GoRoute(
        path: AppRoutes.documents,
        name: 'documents',
        parentNavigatorKey: _rootNavigatorKey,
        builder: (context, state) => const DocumentsListScreen(),
      ),
      // Literal `new` before the `:id` pattern, or `/documents/new` is read as
      // a document whose id is the word "new".
      GoRoute(
        path: AppRoutes.documentNew,
        name: 'documentNew',
        parentNavigatorKey: _rootNavigatorKey,
        builder: (context, state) => const DocumentFormScreen(),
      ),
      GoRoute(
        path: AppRoutes.documentEdit,
        name: 'documentEdit',
        parentNavigatorKey: _rootNavigatorKey,
        builder: (context, state) =>
            DocumentFormScreen(documentId: state.pathParameters['id'] ?? ''),
      ),
      GoRoute(
        path: AppRoutes.goals,
        name: 'goals',
        parentNavigatorKey: _rootNavigatorKey,
        builder: (context, state) => const GoalsScreen(),
      ),
      GoRoute(
        path: AppRoutes.goalNew,
        name: 'goalNew',
        parentNavigatorKey: _rootNavigatorKey,
        builder: (context, state) => const GoalFormScreen(),
      ),
      GoRoute(
        path: AppRoutes.goalEdit,
        name: 'goalEdit',
        parentNavigatorKey: _rootNavigatorKey,
        builder: (context, state) =>
            GoalFormScreen(goalId: state.pathParameters['id'] ?? ''),
      ),
      GoRoute(
        path: AppRoutes.help,
        name: 'help',
        parentNavigatorKey: _rootNavigatorKey,
        builder: (context, state) => const SupportListScreen(),
      ),
      // Literal `new` must be declared before `:id`.
      GoRoute(
        path: AppRoutes.helpNew,
        name: 'helpNew',
        parentNavigatorKey: _rootNavigatorKey,
        builder: (context, state) => const SupportCreateScreen(),
      ),
      GoRoute(
        path: AppRoutes.helpDetail,
        name: 'helpDetail',
        parentNavigatorKey: _rootNavigatorKey,
        builder: (context, state) =>
            SupportDetailScreen(ticketId: state.pathParameters['id'] ?? ''),
      ),
      GoRoute(
        path: AppRoutes.invoices,
        name: 'invoices',
        parentNavigatorKey: _rootNavigatorKey,
        builder: (context, state) => const InvoicesListScreen(),
      ),
      // These five must stay above `invoiceDetail`. go_router matches in
      // declaration order, so `/invoices/:id` placed first would treat
      // "estimates" as an invoice id and every sibling page would 404 through
      // the detail screen instead of opening.
      GoRoute(
        path: AppRoutes.invoiceNew,
        name: 'invoiceNew',
        parentNavigatorKey: _rootNavigatorKey,
        builder: (context, state) => const NewInvoiceScreen(),
      ),
      GoRoute(
        path: AppRoutes.recurringNew,
        name: 'recurringNew',
        parentNavigatorKey: _rootNavigatorKey,
        builder: (context, state) => const NewRecurringScreen(),
      ),
      GoRoute(
        path: AppRoutes.estimates,
        name: 'estimates',
        parentNavigatorKey: _rootNavigatorKey,
        builder: (context, state) => const EstimatesListScreen(),
      ),
      GoRoute(
        path: AppRoutes.recurring,
        name: 'recurring',
        parentNavigatorKey: _rootNavigatorKey,
        builder: (context, state) => const RecurringListScreen(),
      ),
      GoRoute(
        path: AppRoutes.products,
        name: 'products',
        parentNavigatorKey: _rootNavigatorKey,
        builder: (context, state) => const ProductsListScreen(),
      ),
      GoRoute(
        path: AppRoutes.invoiceTemplates,
        name: 'invoiceTemplates',
        parentNavigatorKey: _rootNavigatorKey,
        builder: (context, state) => const InvoiceTemplatesScreen(),
      ),
      GoRoute(
        path: AppRoutes.invoiceTemplateNew,
        name: 'invoiceTemplateNew',
        parentNavigatorKey: _rootNavigatorKey,
        builder: (context, state) => const InvoiceTemplateFormScreen(),
      ),
      GoRoute(
        path: AppRoutes.invoiceTemplateEdit,
        name: 'invoiceTemplateEdit',
        parentNavigatorKey: _rootNavigatorKey,
        builder: (context, state) =>
            InvoiceTemplateFormScreen(templateId: state.pathParameters['id']),
      ),
      GoRoute(
        path: AppRoutes.invoiceReports,
        name: 'invoiceReports',
        parentNavigatorKey: _rootNavigatorKey,
        builder: (context, state) => const InvoiceReportsScreen(),
      ),
      GoRoute(
        path: AppRoutes.invoiceDetail,
        name: 'invoiceDetail',
        parentNavigatorKey: _rootNavigatorKey,
        builder: (context, state) =>
            InvoiceDetailScreen(invoiceId: state.pathParameters['id'] ?? ''),
      ),
      GoRoute(
        path: AppRoutes.profile,
        name: 'profile',
        parentNavigatorKey: _rootNavigatorKey,
        builder: (context, state) => const ProfileScreen(),
      ),
      GoRoute(
        path: AppRoutes.team,
        name: 'team',
        parentNavigatorKey: _rootNavigatorKey,
        builder: (context, state) => const TeamScreen(),
      ),
      GoRoute(
        path: AppRoutes.settings,
        name: 'settings',
        parentNavigatorKey: _rootNavigatorKey,
        builder: (context, state) => const SettingsHubScreen(),
      ),
      GoRoute(
        path: AppRoutes.settingsCustomFields,
        name: 'settingsCustomFields',
        parentNavigatorKey: _rootNavigatorKey,
        builder: (context, state) => const CustomFieldsScreen(),
      ),
      GoRoute(
        path: AppRoutes.settingsMacros,
        name: 'settingsMacros',
        parentNavigatorKey: _rootNavigatorKey,
        builder: (context, state) => const MacrosScreen(),
      ),
      GoRoute(
        path: AppRoutes.settingsTags,
        name: 'settingsTags',
        parentNavigatorKey: _rootNavigatorKey,
        builder: (context, state) => const TagsScreen(),
      ),
      GoRoute(
        path: AppRoutes.settingsRouting,
        name: 'settingsRouting',
        parentNavigatorKey: _rootNavigatorKey,
        builder: (context, state) => const RoutingScreen(),
      ),
      GoRoute(
        path: AppRoutes.settingsEscalation,
        name: 'settingsEscalation',
        parentNavigatorKey: _rootNavigatorKey,
        builder: (context, state) => const EscalationScreen(),
      ),
      GoRoute(
        path: AppRoutes.settingsBusinessHours,
        name: 'settingsBusinessHours',
        parentNavigatorKey: _rootNavigatorKey,
        builder: (context, state) => const BusinessHoursScreen(),
      ),
      GoRoute(
        path: AppRoutes.settingsReopen,
        name: 'settingsReopen',
        parentNavigatorKey: _rootNavigatorKey,
        builder: (context, state) => const ReopenScreen(),
      ),
      GoRoute(
        path: AppRoutes.settingsInboundEmail,
        name: 'settingsInboundEmail',
        parentNavigatorKey: _rootNavigatorKey,
        builder: (context, state) => const InboundEmailScreen(),
      ),
      GoRoute(
        path: AppRoutes.settingsTicketApprovals,
        name: 'settingsTicketApprovals',
        parentNavigatorKey: _rootNavigatorKey,
        builder: (context, state) => const ApprovalRulesScreen(),
      ),
      GoRoute(
        path: AppRoutes.settingsApiTokens,
        name: 'settingsApiTokens',
        parentNavigatorKey: _rootNavigatorKey,
        builder: (context, state) => const ApiTokensScreen(),
      ),
      GoRoute(
        path: AppRoutes.profileTokens,
        name: 'profileTokens',
        parentNavigatorKey: _rootNavigatorKey,
        builder: (context, state) => const MyApiTokensScreen(),
      ),
      // The edit route is declared before the read one so `/organization/edit`
      // is never captured as a sub-path of `/organization`.
      GoRoute(
        path: AppRoutes.settingsOrganizationEdit,
        name: 'settingsOrganizationEdit',
        parentNavigatorKey: _rootNavigatorKey,
        builder: (context, state) => const OrganizationEditScreen(),
      ),
      GoRoute(
        path: AppRoutes.settingsOrganization,
        name: 'settingsOrganization',
        parentNavigatorKey: _rootNavigatorKey,
        builder: (context, state) => const OrganizationScreen(),
      ),
      GoRoute(
        path: AppRoutes.orgCreate,
        name: 'orgCreate',
        parentNavigatorKey: _rootNavigatorKey,
        builder: (context, state) => const OrgCreateScreen(),
      ),
      GoRoute(
        path: AppRoutes.orgSelection,
        name: 'orgSelection',
        pageBuilder: (context, state) => CustomTransitionPage(
          key: state.pageKey,
          child: const OrgSelectionScreen(),
          transitionsBuilder: (context, animation, secondaryAnimation, child) {
            return FadeTransition(opacity: animation, child: child);
          },
        ),
      ),

      // ============================================
      // MAIN APP ROUTES (With bottom navigation)
      // ============================================
      StatefulShellRoute.indexedStack(
        builder: (context, state, navigationShell) {
          return AppShell(navigationShell: navigationShell);
        },
        branches: [
          // Dashboard Branch
          StatefulShellBranch(
            routes: [
              GoRoute(
                path: AppRoutes.dashboard,
                name: 'dashboard',
                builder: (context, state) => const DashboardScreen(),
              ),
            ],
          ),

          // Leads Branch
          StatefulShellBranch(
            routes: [
              GoRoute(
                path: AppRoutes.leads,
                name: 'leads',
                // Keyed on `view` so arriving from a dashboard badge builds a
                // fresh State. Without the key Flutter reuses the one already
                // in this shell branch, whose initState has long since run,
                // and the list opens unfiltered.
                builder: (context, state) {
                  final view = state.uri.queryParameters['view'];
                  return LeadsListScreen(
                    key: ValueKey('leads-$view'),
                    initialView: view,
                  );
                },
                routes: [
                  GoRoute(
                    path: 'create',
                    name: 'leadCreate',
                    parentNavigatorKey: _rootNavigatorKey,
                    builder: (context, state) => const LeadCreateScreen(),
                  ),
                  GoRoute(
                    path: ':id',
                    name: 'leadDetail',
                    parentNavigatorKey: _rootNavigatorKey,
                    builder: (context, state) {
                      final id = state.pathParameters['id']!;
                      return LeadDetailScreen(leadId: id);
                    },
                    routes: [
                      GoRoute(
                        path: 'edit',
                        name: 'leadEdit',
                        parentNavigatorKey: _rootNavigatorKey,
                        builder: (context, state) {
                          final id = state.pathParameters['id']!;
                          return LeadFormScreen(leadId: id);
                        },
                      ),
                    ],
                  ),
                ],
              ),
            ],
          ),

          // Deals Branch
          StatefulShellBranch(
            routes: [
              GoRoute(
                path: AppRoutes.deals,
                name: 'deals',
                builder: (context, state) => const DealsListScreen(),
                routes: [
                  GoRoute(
                    path: 'create',
                    name: 'dealCreate',
                    parentNavigatorKey: _rootNavigatorKey,
                    builder: (context, state) => DealFormScreen(
                      accountId: state.uri.queryParameters['accountId'],
                    ),
                  ),
                  GoRoute(
                    path: ':id',
                    name: 'dealDetail',
                    parentNavigatorKey: _rootNavigatorKey,
                    builder: (context, state) {
                      final id = state.pathParameters['id']!;
                      return DealDetailScreen(dealId: id);
                    },
                    routes: [
                      GoRoute(
                        path: 'edit',
                        name: 'dealEdit',
                        parentNavigatorKey: _rootNavigatorKey,
                        builder: (context, state) {
                          final id = state.pathParameters['id']!;
                          return DealFormScreen(dealId: id);
                        },
                      ),
                    ],
                  ),
                ],
              ),
            ],
          ),

          // Tickets Branch
          StatefulShellBranch(
            routes: [
              GoRoute(
                path: AppRoutes.tickets,
                name: 'tickets',
                builder: (context, state) => const TicketsListScreen(),
                routes: [
                  GoRoute(
                    path: 'create',
                    name: 'ticketCreate',
                    parentNavigatorKey: _rootNavigatorKey,
                    builder: (context, state) => const TicketCreateScreen(),
                  ),
                  GoRoute(
                    path: ':id',
                    name: 'ticketDetail',
                    parentNavigatorKey: _rootNavigatorKey,
                    builder: (context, state) {
                      final id = state.pathParameters['id']!;
                      return TicketDetailScreen(ticketId: id);
                    },
                    routes: [
                      GoRoute(
                        path: 'edit',
                        name: 'ticketEdit',
                        parentNavigatorKey: _rootNavigatorKey,
                        builder: (context, state) {
                          final id = state.pathParameters['id']!;
                          return TicketFormScreen(ticketId: id);
                        },
                      ),
                    ],
                  ),
                ],
              ),
            ],
          ),

          // Tasks Branch
          StatefulShellBranch(
            routes: [
              GoRoute(
                path: AppRoutes.tasks,
                name: 'tasks',
                // Keyed on `view` for the same reason as the leads branch.
                builder: (context, state) {
                  final view = state.uri.queryParameters['view'];
                  return TasksListScreen(
                    key: ValueKey('tasks-$view'),
                    initialView: view,
                  );
                },
                routes: [
                  GoRoute(
                    path: 'create',
                    name: 'taskCreate',
                    parentNavigatorKey: _rootNavigatorKey,
                    // `?due=YYYY-MM-DD` carries the day the user tapped in the
                    // calendar, which the form used to discard.
                    builder: (context, state) => TaskFormScreen(
                      initialDueDate: DateTime.tryParse(
                        state.uri.queryParameters['due'] ?? '',
                      ),
                    ),
                  ),
                  // Before `:id`, which would otherwise swallow "board" and
                  // ask the tasks API for a task with that id.
                  GoRoute(
                    path: 'board',
                    name: 'taskBoard',
                    parentNavigatorKey: _rootNavigatorKey,
                    builder: (context, state) => const BoardScreen(),
                  ),
                  GoRoute(
                    path: ':id',
                    name: 'taskDetail',
                    parentNavigatorKey: _rootNavigatorKey,
                    builder: (context, state) {
                      final id = state.pathParameters['id']!;
                      return TaskDetailScreen(taskId: id);
                    },
                    routes: [
                      GoRoute(
                        path: 'edit',
                        name: 'taskEdit',
                        parentNavigatorKey: _rootNavigatorKey,
                        builder: (context, state) {
                          final id = state.pathParameters['id']!;
                          return TaskFormScreen(taskId: id);
                        },
                      ),
                    ],
                  ),
                ],
              ),
            ],
          ),

          // More/Settings Branch
          StatefulShellBranch(
            routes: [
              GoRoute(
                path: AppRoutes.more,
                name: 'more',
                builder: (context, state) => const MoreScreen(),
              ),
            ],
          ),
        ],
      ),
    ],

    // Error page
    errorBuilder: (context, state) => Scaffold(
      body: Center(
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            const Icon(LucideIcons.circleAlert, size: 64, color: Colors.grey),
            const SizedBox(height: 16),
            Text(
              'Page not found',
              style: Theme.of(context).textTheme.headlineSmall,
            ),
            const SizedBox(height: 8),
            Text(
              state.uri.toString(),
              style: Theme.of(
                context,
              ).textTheme.bodyMedium?.copyWith(color: Colors.grey),
            ),
            const SizedBox(height: 24),
            ElevatedButton(
              onPressed: () => context.go(AppRoutes.dashboard),
              child: const Text('Go to Dashboard'),
            ),
          ],
        ),
      ),
    ),
  );
});
