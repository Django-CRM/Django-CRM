import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

import '../../core/permissions.dart';
import '../../core/theme/theme.dart';
import '../../data/models/solution.dart';
import '../../providers/auth_provider.dart';
import '../../providers/lookup_provider.dart';
import '../../providers/solutions_provider.dart';
import '../../widgets/common/common.dart';

/// View / edit a single solution. New solutions use `solutionId == null`.
class SolutionDetailScreen extends ConsumerStatefulWidget {
  final String? solutionId;
  const SolutionDetailScreen({super.key, this.solutionId});

  bool get isCreate => solutionId == null;

  @override
  ConsumerState<SolutionDetailScreen> createState() =>
      _SolutionDetailScreenState();
}

class _SolutionDetailScreenState extends ConsumerState<SolutionDetailScreen> {
  final _titleController = TextEditingController();
  final _descController = TextEditingController();
  SolutionStatus _status = SolutionStatus.draft;
  bool _isPublished = false;
  List<String> _tagIds = [];

  bool _isLoading = false;
  bool _isFetching = false;
  Solution? _existing;

  @override
  void initState() {
    super.initState();
    if (!widget.isCreate) _load();
  }

  @override
  void dispose() {
    _titleController.dispose();
    _descController.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    setState(() => _isFetching = true);
    final s = await ref
        .read(solutionsProvider.notifier)
        .getById(widget.solutionId!);
    if (!mounted) return;
    setState(() {
      _isFetching = false;
      if (s != null) {
        _existing = s;
        _titleController.text = s.title;
        _descController.text = s.description;
        _status = s.status;
        _isPublished = s.isPublished;
        _tagIds = List<String>.from(s.tagIds);
      }
    });
  }

  Future<void> _save() async {
    if (_titleController.text.trim().isEmpty) {
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(const SnackBar(content: Text('Title is required')));
      return;
    }
    setState(() => _isLoading = true);
    final payload = {
      'title': _titleController.text.trim(),
      'description': _descController.text.trim(),
      'status': _status.value,
      // Always sent on an update. Every active tag is on screen as a chip, so
      // an unselected one is a deliberate "not this", and the API reads an
      // explicit empty list as "clear" while an absent key means "leave alone".
      'tags': _tagIds,
    };
    final notifier = ref.read(solutionsProvider.notifier);
    final res = widget.isCreate
        ? await notifier.create(
            Solution(
              id: '',
              title: payload['title']! as String,
              description: payload['description']! as String,
              status: _status,
              tagIds: _tagIds,
            ),
          )
        : await notifier.update(widget.solutionId!, payload);
    if (!mounted) return;
    setState(() => _isLoading = false);
    if (res.success) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(
            widget.isCreate ? 'Solution created' : 'Solution updated',
          ),
          behavior: SnackBarBehavior.floating,
        ),
      );
      context.pop(true);
    } else {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(res.message ?? 'Failed to save'),
          backgroundColor: AppColors.danger600,
          behavior: SnackBarBehavior.floating,
        ),
      );
    }
  }

  Future<void> _togglePublish() async {
    if (_existing == null) return;
    setState(() => _isLoading = true);
    final notifier = ref.read(solutionsProvider.notifier);
    final res = _isPublished
        ? await notifier.unpublish(_existing!.id)
        : await notifier.publish(_existing!.id);
    if (!mounted) return;
    setState(() {
      _isLoading = false;
      if (res.success) _isPublished = !_isPublished;
    });
    if (!res.success) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(res.message ?? 'Failed'),
          backgroundColor: AppColors.danger600,
          behavior: SnackBarBehavior.floating,
        ),
      );
    } else {
      await _load();
    }
  }

  Future<void> _delete() async {
    if (_existing == null) return;
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Delete solution?'),
        content: Text('Permanently delete "${_existing!.title}"?'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('Cancel'),
          ),
          TextButton(
            style: TextButton.styleFrom(foregroundColor: AppColors.danger600),
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('Delete'),
          ),
        ],
      ),
    );
    if (ok != true) return;
    setState(() => _isLoading = true);
    final res = await ref
        .read(solutionsProvider.notifier)
        .delete(_existing!.id);
    if (!mounted) return;
    setState(() => _isLoading = false);
    if (res.success) {
      context.pop(true);
    } else {
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(SnackBar(content: Text(res.message ?? 'Delete failed')));
    }
  }

  @override
  Widget build(BuildContext context) {
    // `cases/kb_access.py` draws three different lines on this one screen.
    // Reading is open to the org, which is why the article renders for
    // everybody. Editing and deleting belong to admins and the author. Only an
    // admin may approve or publish, and being the author deliberately does not
    // grant it. The screen used to offer all three to everybody.
    final isAdmin = ref.watch(isOrgAdminProvider);
    // Already active-only: the lookup calls /tags/ without
    // `include_archived`, and `TagsListView` defaults to active. That matters
    // because `_apply_tags` refuses an archived tag, so a chip for one would
    // silently do nothing on save. The web form filters explicitly because
    // its `getTags` asks for archived ones for the settings page.
    final tags = ref.watch(tagsProvider);
    final canWrite =
        widget.isCreate ||
        isAdminOrOwner(
          isAdmin: isAdmin,
          currentUserKey: ref.watch(currentUserProvider)?.id,
          ownerKey: _existing?.createdById,
        );

    return Scaffold(
      backgroundColor: AppColors.surface,
      appBar: AppBar(
        title: Text(widget.isCreate ? 'New solution' : 'Solution'),
        backgroundColor: AppColors.surface,
        elevation: 0,
        leading: IconButton(
          icon: const Icon(LucideIcons.chevronLeft),
          onPressed: () => context.pop(),
        ),
        actions: [
          if (!widget.isCreate && _existing != null && canWrite)
            IconButton(
              tooltip: 'Delete',
              icon: const Icon(LucideIcons.trash2),
              onPressed: _isLoading ? null : _delete,
            ),
        ],
      ),
      body: _isFetching
          ? const Center(child: CircularProgressIndicator())
          : SingleChildScrollView(
              padding: const EdgeInsets.all(20),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  FloatingLabelInput(
                    label: 'Title',
                    controller: _titleController,
                    prefixIcon: LucideIcons.bookOpen,
                  ),
                  const SizedBox(height: 16),
                  TextAreaField(
                    label: 'Description',
                    controller: _descController,
                    maxLines: 10,
                  ),
                  const SizedBox(height: 16),
                  Text(
                    'STATUS',
                    style: AppTypography.overline.copyWith(
                      color: AppColors.textSecondary,
                    ),
                  ),
                  const SizedBox(height: 8),
                  Wrap(
                    spacing: 8,
                    children: SolutionStatus.values
                        .map(
                          (s) => ChoiceChip(
                            label: Text(s.label),
                            selected: _status == s,
                            onSelected: (_) => setState(() => _status = s),
                          ),
                        )
                        .toList(),
                  ),
                  if (tags.isNotEmpty) ...[
                    const SizedBox(height: 16),
                    Text(
                      'TAGS',
                      style: AppTypography.overline.copyWith(
                        color: AppColors.textSecondary,
                      ),
                    ),
                    const SizedBox(height: 4),
                    // Chips rather than the picker sheet the ticket form uses:
                    // a knowledge base has a handful of tags and they all fit,
                    // so the vocabulary is visible instead of hidden behind a
                    // tap. Same shape as the web form.
                    Text(
                      'Internal filing. Customers never see these names, but '
                      'articles sharing one are shown to each other in the portal.',
                      style: AppTypography.caption.copyWith(
                        color: AppColors.textSecondary,
                      ),
                    ),
                    const SizedBox(height: 8),
                    Wrap(
                      spacing: 8,
                      runSpacing: 8,
                      children: tags
                          .map(
                            (t) => FilterChip(
                              label: Text(t.name),
                              selected: _tagIds.contains(t.id),
                              onSelected: canWrite
                                  ? (on) => setState(() {
                                      if (on) {
                                        _tagIds = [..._tagIds, t.id];
                                      } else {
                                        _tagIds = _tagIds
                                            .where((id) => id != t.id)
                                            .toList();
                                      }
                                    })
                                  : null,
                            ),
                          )
                          .toList(),
                    ),
                  ],
                  if (!widget.isCreate && _existing != null && canWrite) ...[
                    const SizedBox(height: 16),
                    Container(
                      padding: const EdgeInsets.all(12),
                      decoration: BoxDecoration(
                        color: _isPublished
                            ? AppColors.primary50
                            : AppColors.gray50,
                        borderRadius: AppLayout.borderRadiusMd,
                        border: Border.all(
                          color: _isPublished
                              ? AppColors.primary200
                              : AppColors.border,
                        ),
                      ),
                      child: Row(
                        children: [
                          Icon(
                            _isPublished
                                ? LucideIcons.globe
                                : LucideIcons.eyeOff,
                            color: _isPublished
                                ? AppColors.primary600
                                : AppColors.textSecondary,
                          ),
                          const SizedBox(width: 12),
                          Expanded(
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Text(
                                  _isPublished ? 'Published' : 'Not published',
                                  style: AppTypography.label,
                                ),
                                Text(
                                  // An approved, published article is readable
                                  // by signed-in portal contacts in this org
                                  // and is suggested to them while they file a
                                  // ticket, per
                                  // PortalBaseView._published_articles. The
                                  // agent suggester is the lesser half of what
                                  // this switch does, so the customer comes
                                  // first in the sentence.
                                  _isPublished
                                      ? 'Live to customers, and suggested to agents.'
                                      : 'Only approved solutions can be published.',
                                  style: AppTypography.caption.copyWith(
                                    color: AppColors.textSecondary,
                                  ),
                                ),
                              ],
                            ),
                          ),
                          TextButton(
                            // The global theme sets `minimumSize:
                            // Size.fromHeight(..)`, which is `Size(∞, h)`. In
                            // a Column that clamps to the parent width; in a
                            // Row it stays infinite, the layout throws, and
                            // the whole body paints nothing. That is what made
                            // this screen open blank with only a delete icon.
                            style: TextButton.styleFrom(
                              minimumSize: const Size(
                                0,
                                AppLayout.buttonHeightMedium,
                              ),
                            ),
                            // Admin-only, per `assert_solution_release_access`,
                            // and still only from an approved article per the
                            // serializer. Both rules, not just the second.
                            onPressed:
                                _isLoading ||
                                    !isAdmin ||
                                    (!_isPublished &&
                                        _status != SolutionStatus.approved)
                                ? null
                                : _togglePublish,
                            child: Text(_isPublished ? 'Unpublish' : 'Publish'),
                          ),
                        ],
                      ),
                    ),
                  ],
                  const SizedBox(height: 24),
                  if (canWrite)
                    PrimaryButton(
                      label: widget.isCreate
                          ? 'Create solution'
                          : 'Save changes',
                      onPressed: _isLoading ? null : _save,
                      isLoading: _isLoading,
                    ),
                ],
              ),
            ),
    );
  }
}
