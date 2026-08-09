import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

import '../../data/models/auth_response.dart';
import '../../providers/auth_provider.dart';
import '../../routes/app_router.dart';

class OrgSelectionScreen extends ConsumerStatefulWidget {
  const OrgSelectionScreen({super.key});

  @override
  ConsumerState<OrgSelectionScreen> createState() => _OrgSelectionScreenState();
}

class _OrgSelectionScreenState extends ConsumerState<OrgSelectionScreen> {
  bool _isSelecting = false;

  Future<void> _selectOrganization(Organization org) async {
    if (_isSelecting) return;

    setState(() {
      _isSelecting = true;
    });

    try {
      final success = await ref
          .read(authProvider.notifier)
          .switchOrganization(org);

      if (success && mounted) {
        context.go(AppRoutes.dashboard);
      } else if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text('Failed to select organization'),
            backgroundColor: Colors.red,
          ),
        );
      }
    } finally {
      if (mounted) {
        setState(() {
          _isSelecting = false;
        });
      }
    }
  }

  Future<void> _signOut() async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Sign Out'),
        content: const Text('Are you sure you want to sign out?'),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop(false),
            child: const Text('Cancel'),
          ),
          TextButton(
            onPressed: () => Navigator.of(context).pop(true),
            child: const Text('Sign Out'),
          ),
        ],
      ),
    );

    if (confirmed == true && mounted) {
      await ref.read(authProvider.notifier).signOut();
      if (mounted) {
        context.go(AppRoutes.login);
      }
    }
  }

  Widget _buildOrgCard(Organization org) {
    // `Material` rather than a plain `Container`: a ListTile paints its ink
    // splash on the nearest Material ancestor, so a coloured box between the
    // two swallows the tap ripple. Flutter asserts on exactly this in debug
    // builds, which is how it was found.
    return Container(
      margin: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
      child: Material(
        color: Colors.white,
        borderRadius: BorderRadius.circular(8),
        child: ListTile(
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(8),
            side: BorderSide(color: Colors.grey.shade200),
          ),
          contentPadding: const EdgeInsets.all(16),
          leading: CircleAvatar(
            backgroundColor: Theme.of(context).primaryColor,
            child: Text(
              org.initials,
              style: const TextStyle(
                color: Colors.white,
                fontWeight: FontWeight.bold,
              ),
            ),
          ),
          title: Text(
            org.name,
            style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w600),
          ),
          subtitle: org.role != null
              ? Text(
                  org.role!,
                  style: TextStyle(fontSize: 14, color: Colors.grey[600]),
                )
              : null,
          trailing: _isSelecting
              ? const SizedBox(
                  width: 24,
                  height: 24,
                  child: CircularProgressIndicator(strokeWidth: 2),
                )
              : const Icon(LucideIcons.chevronRight, size: 16),
          onTap: _isSelecting ? null : () => _selectOrganization(org),
        ),
      ),
    );
  }

  Widget _buildEmptyState() {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(LucideIcons.building2, size: 64, color: Colors.grey[400]),
            const SizedBox(height: 16),
            Text(
              'No Organizations',
              style: TextStyle(
                fontSize: 20,
                fontWeight: FontWeight.w600,
                color: Colors.grey[700],
              ),
            ),
            const SizedBox(height: 8),
            Text(
              'You are not a member of any organization yet. Ask an administrator '
              'to invite you, or start one of your own.',
              textAlign: TextAlign.center,
              style: TextStyle(fontSize: 14, color: Colors.grey[600]),
            ),
            const SizedBox(height: 20),
            // This used to be the sentence above and nothing else: it named the
            // action and left the user on a screen whose only working control
            // was Sign Out.
            FilledButton.icon(
              onPressed: () => context.push(AppRoutes.orgCreate),
              icon: const Icon(LucideIcons.plus),
              label: const Text('Create an organization'),
            ),
          ],
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final authState = ref.watch(authProvider);
    final user = authState.user;
    final organizations = authState.organizations ?? [];

    return Scaffold(
      backgroundColor: Colors.grey[50],
      appBar: AppBar(
        title: const Text('Select Organization'),
        backgroundColor: Colors.white,
        foregroundColor: Colors.black,
        elevation: 0,
        automaticallyImplyLeading: false,
        actions: [
          IconButton(
            icon: const Icon(LucideIcons.logOut),
            onPressed: _signOut,
            tooltip: 'Sign Out',
          ),
        ],
      ),
      body: authState.isLoading
          ? const Center(child: CircularProgressIndicator())
          : Column(
              children: [
                // User info section
                Container(
                  width: double.infinity,
                  color: Colors.white,
                  padding: const EdgeInsets.all(16),
                  child: Column(
                    children: [
                      CircleAvatar(
                        radius: 40,
                        backgroundColor: Theme.of(context).primaryColor,
                        backgroundImage: user?.profilePic != null
                            ? NetworkImage(user!.profilePic!)
                            : null,
                        child: user?.profilePic == null
                            ? Text(
                                user?.initials ?? 'U',
                                style: const TextStyle(
                                  fontSize: 24,
                                  color: Colors.white,
                                  fontWeight: FontWeight.bold,
                                ),
                              )
                            : null,
                      ),
                      const SizedBox(height: 12),
                      Text(
                        user?.displayName ?? 'Unknown User',
                        style: const TextStyle(
                          fontSize: 20,
                          fontWeight: FontWeight.w600,
                        ),
                      ),
                      Text(
                        user?.email ?? '',
                        style: TextStyle(fontSize: 14, color: Colors.grey[600]),
                      ),
                    ],
                  ),
                ),
                const SizedBox(height: 16),

                // Section header
                if (organizations.isNotEmpty)
                  Padding(
                    padding: const EdgeInsets.symmetric(horizontal: 16),
                    child: Align(
                      alignment: Alignment.centerLeft,
                      child: Text(
                        'Choose your organization to continue:',
                        style: TextStyle(
                          fontSize: 14,
                          fontWeight: FontWeight.w500,
                          color: Colors.grey[700],
                        ),
                      ),
                    ),
                  ),
                const SizedBox(height: 8),

                // Organizations list
                Expanded(
                  child: organizations.isEmpty
                      ? _buildEmptyState()
                      : ListView.builder(
                          itemCount: organizations.length,
                          itemBuilder: (context, index) {
                            return _buildOrgCard(organizations[index]);
                          },
                        ),
                ),

                // Belonging to one organization does not mean you cannot start
                // another; the web has always allowed it from the same screen.
                if (organizations.isNotEmpty)
                  Padding(
                    padding: const EdgeInsets.fromLTRB(16, 4, 16, 16),
                    child: TextButton.icon(
                      onPressed: _isSelecting
                          ? null
                          : () => context.push(AppRoutes.orgCreate),
                      icon: const Icon(LucideIcons.plus, size: 18),
                      label: const Text('Create an organization'),
                    ),
                  ),
              ],
            ),
    );
  }
}
