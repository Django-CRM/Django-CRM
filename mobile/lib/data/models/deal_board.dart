import 'deal.dart';
import 'deal_pipeline.dart';

/// The deal board: `GET /opportunities/kanban/?pipeline=<id>`, the endpoint
/// the web board reads, with the deal list's filters.
///
/// One column per stage of the pipeline, keyed by the stage `code` the deals
/// store. The screen still takes each column's label, colour and order from
/// the pipeline itself; this only says which deals sit in it.
class DealBoard {
  const DealBoard({this.columns = const []});

  final List<DealBoardColumn> columns;

  factory DealBoard.fromJson(Map<String, dynamic> json) {
    final raw = json['columns'];
    return DealBoard(
      columns: raw is List
          ? raw
                .whereType<Map<String, dynamic>>()
                .map(DealBoardColumn.fromJson)
                .toList()
          : const [],
    );
  }

  /// The column for [code], or an empty one when the board has none (a stage
  /// added since the board was read).
  DealBoardColumn column(String code) => columns.firstWhere(
    (c) => c.code == code,
    orElse: () => DealBoardColumn(code: code),
  );

  /// Whether the server says this viewer may move [dealId]. False for a deal
  /// the board does not hold.
  bool canMove(String dealId) => columns.any(
    (c) => c.cards.any((card) => card.deal.id == dealId && card.canMove),
  );

  /// The board after [moved] (the deal as it is now, in its new stage) left
  /// whichever column held it. Counts follow the card.
  DealBoard withMove(Deal moved) {
    DealBoardCard? card;
    for (final column in columns) {
      for (final c in column.cards) {
        if (c.deal.id == moved.id) card = c;
      }
    }
    if (card == null) return this;
    final placed = DealBoardCard(deal: moved, canMove: card.canMove);
    return DealBoard(
      columns: [
        for (final column in columns)
          if (column.cards.any((c) => c.deal.id == moved.id))
            column.copyWith(
              itemCount: column.itemCount - 1,
              cards: column.cards.where((c) => c.deal.id != moved.id).toList(),
            )
          else if (column.code == moved.stage)
            column.copyWith(
              itemCount: column.itemCount + 1,
              cards: [...column.cards, placed],
            )
          else
            column,
      ],
    );
  }
}

/// One stage's deals. The API sends at most 100 cards a column, so
/// [itemCount] can be larger than `cards.length`; see [isTruncated].
class DealBoardColumn {
  const DealBoardColumn({
    required this.code,
    this.kind = dealStageOpen,
    this.itemCount = 0,
    this.cards = const [],
  });

  final String code;

  /// `open`, `won` or `lost`.
  final String kind;

  /// Every deal in the stage the caller may see and the filters let through.
  final int itemCount;
  final List<DealBoardCard> cards;

  bool get isTruncated => itemCount > cards.length;
  bool get isClosed => kind == dealStageWon || kind == dealStageLost;

  factory DealBoardColumn.fromJson(Map<String, dynamic> json) {
    final raw = json['items'];
    final cards = raw is List
        ? raw
              .whereType<Map<String, dynamic>>()
              .map(DealBoardCard.fromJson)
              .toList()
        : <DealBoardCard>[];
    return DealBoardColumn(
      code: (json['id'] ?? '').toString(),
      kind: json['kind'] as String? ?? dealStageOpen,
      itemCount: json['item_count'] as int? ?? cards.length,
      cards: cards,
    );
  }

  DealBoardColumn copyWith({int? itemCount, List<DealBoardCard>? cards}) =>
      DealBoardColumn(
        code: code,
        kind: kind,
        itemCount: itemCount ?? this.itemCount,
        cards: cards ?? this.cards,
      );
}

/// One card: the deal, and whether this viewer may move it.
class DealBoardCard {
  const DealBoardCard({required this.deal, this.canMove = false});

  final Deal deal;

  /// The server's `can_move`. Only an explicit `true` counts, so a card from
  /// a payload without the field is not draggable rather than offering a
  /// move the server may refuse.
  final bool canMove;

  factory DealBoardCard.fromJson(Map<String, dynamic> json) => DealBoardCard(
    deal: Deal.fromJson(json),
    canMove: json['can_move'] == true,
  );
}
