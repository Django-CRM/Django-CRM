/// Parts of a record's detail page shared by the deal and project screens: an
/// assignee from a profile map, a note with its options, and the edit dialog.
library;

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

import '../../core/theme/theme.dart';
import '../../data/api/schema.g.dart' as api;
import 'avatar.dart';

/// One assignee, from a profile map (`{id, user_details: {name, email,
/// profile_pic}}`).
class AssigneeRow extends StatelessWidget {
  final Map<String, dynamic> profile;
  const AssigneeRow({super.key, required this.profile});

  @override
  Widget build(BuildContext context) {
    final details = api.Profile.fromJson(profile).userDetails;
    String name = 'Unknown';
    String? email;
    String? pic;
    if (details != null) {
      final n = details.name?.trim();
      email = details.email?.trim();
      pic = details.profilePic?.trim();
      if (n != null && n.isNotEmpty) {
        name = n;
      } else if (email != null && email.isNotEmpty) {
        name = email.split('@').first;
      }
    }
    return Row(
      children: [
        UserAvatar(name: name, imageUrl: pic, size: AvatarSize.md),
        const SizedBox(width: 12),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                name,
                style: AppTypography.label,
                overflow: TextOverflow.ellipsis,
              ),
              if (email != null && email.isNotEmpty && email != name)
                Text(
                  email,
                  style: AppTypography.caption.copyWith(
                    color: AppColors.textSecondary,
                  ),
                  overflow: TextOverflow.ellipsis,
                ),
            ],
          ),
        ),
      ],
    );
  }
}

class EditNoteDialog extends StatefulWidget {
  final String initialText;
  final int? maxLength;
  const EditNoteDialog({super.key, required this.initialText, this.maxLength});

  @override
  State<EditNoteDialog> createState() => _EditNoteDialogState();
}

class _EditNoteDialogState extends State<EditNoteDialog> {
  late final TextEditingController _controller;

  @override
  void initState() {
    super.initState();
    _controller = TextEditingController(text: widget.initialText);
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: const Text('Edit Note'),
      content: TextField(
        controller: _controller,
        autofocus: true,
        maxLines: null,
        minLines: 3,
        inputFormatters: [
          if (widget.maxLength != null)
            LengthLimitingTextInputFormatter(widget.maxLength),
        ],
        decoration: const InputDecoration(
          hintText: 'Update your note...',
          border: OutlineInputBorder(),
        ),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(context),
          child: const Text('Cancel'),
        ),
        TextButton(
          onPressed: () => Navigator.pop(context, _controller.text.trim()),
          child: const Text('Save'),
        ),
      ],
    );
  }
}

class NoteCard extends StatelessWidget {
  final String text;
  final String author;
  final DateTime timestamp;
  final VoidCallback? onEdit;
  final VoidCallback? onDelete;

  const NoteCard({
    super.key,
    required this.text,
    required this.author,
    required this.timestamp,
    this.onEdit,
    this.onDelete,
  });

  void _showOptions(BuildContext context) {
    if (onEdit == null && onDelete == null) return;
    showModalBottomSheet(
      context: context,
      backgroundColor: AppColors.surface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (context) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Container(
              margin: const EdgeInsets.only(top: 12),
              width: 40,
              height: 4,
              decoration: BoxDecoration(
                color: AppColors.gray300,
                borderRadius: BorderRadius.circular(2),
              ),
            ),
            if (onEdit != null)
              ListTile(
                leading: Icon(LucideIcons.pencil, color: AppColors.primary600),
                title: const Text('Edit Note'),
                onTap: () {
                  Navigator.pop(context);
                  onEdit!();
                },
              ),
            if (onDelete != null)
              ListTile(
                leading: Icon(LucideIcons.trash2, color: AppColors.danger600),
                title: Text(
                  'Delete Note',
                  style: TextStyle(color: AppColors.danger600),
                ),
                onTap: () {
                  Navigator.pop(context);
                  _confirmDelete(context);
                },
              ),
            const SizedBox(height: 8),
          ],
        ),
      ),
    );
  }

  Future<void> _confirmDelete(BuildContext context) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Delete Note?'),
        content: const Text('This action cannot be undone.'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Cancel'),
          ),
          TextButton(
            onPressed: () => Navigator.pop(context, true),
            child: Text('Delete', style: TextStyle(color: AppColors.danger600)),
          ),
        ],
      ),
    );
    if (confirmed == true) onDelete?.call();
  }

  @override
  Widget build(BuildContext context) {
    final hasActions = onEdit != null || onDelete != null;
    return GestureDetector(
      onLongPress: hasActions ? () => _showOptions(context) : null,
      child: Container(
        margin: const EdgeInsets.only(bottom: 12),
        padding: const EdgeInsets.all(16),
        decoration: BoxDecoration(
          color: AppColors.gray50,
          borderRadius: AppLayout.borderRadiusLg,
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Expanded(
                  child: Text(
                    text,
                    style: AppTypography.body.copyWith(
                      color: AppColors.textPrimary,
                      height: 1.5,
                    ),
                  ),
                ),
                if (hasActions)
                  SizedBox(
                    width: 32,
                    height: 32,
                    child: IconButton(
                      padding: EdgeInsets.zero,
                      iconSize: 18,
                      visualDensity: VisualDensity.compact,
                      icon: Icon(
                        LucideIcons.moreVertical,
                        color: AppColors.textTertiary,
                      ),
                      tooltip: 'Note options',
                      onPressed: () => _showOptions(context),
                    ),
                  ),
              ],
            ),
            const SizedBox(height: 12),
            Row(
              children: [
                UserAvatar(name: author, size: AvatarSize.xs),
                const SizedBox(width: 8),
                // Flexible: a long author (an email) ellipsizes rather than
                // pushing the time off the card.
                Flexible(
                  child: Text(
                    author,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: AppTypography.caption.copyWith(
                      fontWeight: FontWeight.w500,
                    ),
                  ),
                ),
                const SizedBox(width: 8),
                Text(
                  _relTime(timestamp),
                  style: AppTypography.caption.copyWith(
                    color: AppColors.textTertiary,
                  ),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  String _relTime(DateTime dt) {
    final d = DateTime.now().difference(dt);
    if (d.inDays > 30) return '${(d.inDays / 30).floor()}mo ago';
    if (d.inDays > 0) return '${d.inDays}d ago';
    if (d.inHours > 0) return '${d.inHours}h ago';
    if (d.inMinutes > 0) return '${d.inMinutes}m ago';
    return 'just now';
  }
}
