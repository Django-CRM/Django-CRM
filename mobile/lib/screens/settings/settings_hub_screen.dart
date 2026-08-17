import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

import '../../core/theme/theme.dart';
import '../../routes/app_router.dart';
import '../../widgets/common/common.dart';

/// The launcher for the org settings cluster, mirroring `/settings` on the web.
///
/// **Not admin-gated, on purpose.** The reads underneath it are open to any
/// member: `CustomFieldDefinitionListCreateView.get` carries only
/// `IsAuthenticated, HasOrgContext`, and the admin check sits on POST, PUT and
/// DELETE. The web's sidebar says the same in as many words ("the hub is
/// readable by any member"). Gating the read here would hide from a member the
/// definitions that shape the forms they fill in every day, and would be this
/// app disagreeing with the API about who may look.
///
/// Each page hides its own write controls from a non-admin, which is UX rather
/// than a guard: the server answers 403 either way.
///
/// Every settings page the web has is here. Two of the reads underneath are
/// admin-only server-side and say so on arrival rather than here: the reopen
/// policy and the org-wide token list.
class SettingsHubScreen extends StatelessWidget {
  const SettingsHubScreen({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.surfaceDim,
      appBar: AppBar(
        title: const Text('Organization settings'),
        backgroundColor: AppColors.surface,
        elevation: 0,
        scrolledUnderElevation: 1,
      ),
      body: SingleChildScrollView(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const _SectionHeader('Records'),
            MenuRow(
              icon: LucideIcons.listPlus,
              label: 'Custom fields',
              description: 'Extra fields on leads, deals, tickets and the rest',
              onTap: () => context.push(AppRoutes.settingsCustomFields),
            ),
            MenuRow(
              icon: LucideIcons.tags,
              label: 'Tags',
              description: 'The labels shared across every record type',
              onTap: () => context.push(AppRoutes.settingsTags),
            ),
            const _SectionHeader('Tickets'),
            MenuRow(
              icon: LucideIcons.gitBranch,
              label: 'Routing',
              description: 'Who a new ticket is assigned to',
              onTap: () => context.push(AppRoutes.settingsRouting),
            ),
            MenuRow(
              icon: LucideIcons.bellOff,
              label: 'Escalation',
              description: 'What happens when a ticket misses its SLA target',
              onTap: () => context.push(AppRoutes.settingsEscalation),
            ),
            MenuRow(
              icon: LucideIcons.clock,
              label: 'Business hours',
              description: 'The week every response target is measured against',
              onTap: () => context.push(AppRoutes.settingsBusinessHours),
            ),
            MenuRow(
              icon: LucideIcons.rotateCcw,
              label: 'Reopen policy',
              description: 'Whether a reply brings a closed ticket back',
              onTap: () => context.push(AppRoutes.settingsReopen),
            ),
            MenuRow(
              icon: LucideIcons.messageSquareQuote,
              label: 'Saved replies',
              description: 'Canned answers to insert into a ticket reply',
              onTap: () => context.push(AppRoutes.settingsMacros),
            ),
            MenuRow(
              icon: LucideIcons.mail,
              label: 'Inbound email',
              description: 'The addresses that turn mail into tickets',
              onTap: () => context.push(AppRoutes.settingsInboundEmail),
            ),
            MenuRow(
              icon: LucideIcons.circleCheckBig,
              label: 'Approval rules',
              description: 'What gates a ticket close, and who can clear it',
              onTap: () => context.push(AppRoutes.settingsTicketApprovals),
            ),
            const _SectionHeader('Organization'),
            MenuRow(
              icon: LucideIcons.building2,
              label: 'Organization',
              description: 'Company details printed on documents, and defaults',
              onTap: () => context.push(AppRoutes.settingsOrganization),
            ),
            MenuRow(
              icon: LucideIcons.keyRound,
              label: 'API tokens',
              description: 'Programmatic access, and what to revoke',
              onTap: () => context.push(AppRoutes.settingsApiTokens),
            ),
            // Filed with the organization cluster rather than with records,
            // beside API tokens. What a published form grants is the ability
            // for a stranger with no account to write into this org, which is
            // an access question; that it arrives as a lead is the smaller
            // half.
            MenuRow(
              icon: LucideIcons.clipboardList,
              label: 'Web forms',
              description:
                  'Forms you embed on your own site, and the leads '
                  'they bring in',
              onTap: () => context.push(AppRoutes.settingsWebForms),
            ),
            const SizedBox(height: 48),
          ],
        ),
      ),
    );
  }
}

class _SectionHeader extends StatelessWidget {
  const _SectionHeader(this.title);

  final String title;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 24, 16, 8),
      child: Text(
        title.toUpperCase(),
        style: AppTypography.overline.copyWith(
          color: AppColors.textSecondary,
          letterSpacing: 1.2,
        ),
      ),
    );
  }
}
