import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

import '../../core/theme/theme.dart';
import '../../data/models/custom_object.dart';
import '../../data/models/object_board.dart';
import '../../providers/custom_objects_provider.dart';
import '../../routes/app_router.dart';
import '../common/common.dart';
import '../misc/kanban_column.dart';
import 'object_record_card.dart';

/// The records of an object laid out by one dropdown field. A card is picked
/// up with a long press (only a card the viewer can edit) and dropped on another column to change that one
/// value. Moving needs [canMove] (writes on for the org); the server checks
/// the record's own rights and says so when it refuses.
class ObjectBoardView extends ConsumerWidget {
  const ObjectBoardView({
    super.key,
    required this.objectKey,
    required this.definition,
    required this.canMove,
  });

  final String objectKey;
  final ObjectDefinition definition;
  final bool canMove;

  Color _tint(BoardColumnKind kind, int index) {
    if (kind != BoardColumnKind.option) return AppColors.gray400;
    const palette = [
      AppColors.primary600,
      AppColors.purple600,
      AppColors.warning600,
      AppColors.success600,
    ];
    return palette[index % palette.length];
  }

  Future<void> _drop(
    BuildContext context,
    WidgetRef ref,
    ObjectRecord record,
    ObjectBoardColumn target,
  ) async {
    final error = await ref
        .read(objectBoardProvider(objectKey).notifier)
        .move(record, target);
    if (error == null || !context.mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(error),
        behavior: SnackBarBehavior.floating,
        backgroundColor: AppColors.danger600,
      ),
    );
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final async = ref.watch(objectBoardProvider(objectKey));
    final board = async.value;
    if (board == null) {
      if (async.hasError) {
        return Center(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              const Text('Failed to load the board'),
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 16),
                child: Text(
                  async.error.toString().replaceFirst('Exception: ', ''),
                  textAlign: TextAlign.center,
                ),
              ),
              TextButton(
                onPressed: () =>
                    ref.read(objectBoardProvider(objectKey).notifier).refresh(),
                child: const Text('Retry'),
              ),
            ],
          ),
        );
      }
      return const Center(child: CircularProgressIndicator());
    }
    if (board.columns.isEmpty) {
      return const EmptyState(
        icon: LucideIcons.columns3,
        title: 'This board has no columns',
      );
    }
    final required = definition.boardField?.isRequired ?? false;
    final fields = [
      for (final f in definition.customFields)
        if (f.key != board.fieldKey) f,
    ];
    final width = MediaQuery.of(context).size.width * 0.85;

    return RefreshIndicator(
      onRefresh: () =>
          ref.read(objectBoardProvider(objectKey).notifier).refresh(),
      child: CustomScrollView(
        physics: const AlwaysScrollableScrollPhysics(),
        slivers: [
          SliverFillRemaining(
            child: SingleChildScrollView(
              scrollDirection: Axis.horizontal,
              padding: const EdgeInsets.fromLTRB(8, 8, 8, 16),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  for (var i = 0; i < board.columns.length; i++)
                    _column(
                      context,
                      ref,
                      board,
                      board.columns[i],
                      i,
                      width: width,
                      fields: fields,
                      fieldRequired: required,
                    ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _column(
    BuildContext context,
    WidgetRef ref,
    ObjectBoard board,
    ObjectBoardColumn column,
    int index, {
    required double width,
    required List<ObjectField> fields,
    required bool fieldRequired,
  }) {
    return BoardColumn<ObjectRecord>(
      title: column.label,
      color: _tint(column.kind, index),
      count: column.count,
      cards: column.records,
      width: width,
      emptyLabel: 'No ${definition.summary.labelPlural.toLowerCase()}',
      footnote: column.kind == BoardColumnKind.other
          ? 'Values that are no longer options'
          : null,
      draggable: (record) => canMove && record.canEdit,
      accepts: (record) =>
          canMove &&
          board.canDrop(record, column, fieldRequired: fieldRequired),
      onDrop: (record) => _drop(context, ref, record, column),
      cardBuilder: (record, {bool dragging = false}) => ObjectRecordCard(
        record: record,
        fields: fields,
        onTap: () => context.push(AppRoutes.objectRecord(objectKey, record.id)),
      ),
    );
  }
}
