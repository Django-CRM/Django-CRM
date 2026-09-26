import 'package:bottle_crm/data/models/ticket.dart';
import 'package:bottle_crm/providers/tickets_provider.dart';
import 'package:flutter_test/flutter_test.dart';

/// A tree node as `/api/cases/{id}/tree/` returns one.
Map<String, dynamic> treeJson({
  required String id,
  String? name,
  String status = 'New',
  bool isActive = true,
  bool truncated = false,
  List<Map<String, dynamic>> children = const [],
}) => {
  'id': id,
  'name': name ?? id,
  'status': status,
  'is_active': isActive,
  'truncated': truncated,
  'children': children,
};

/// The shape that broke the web's unwired version of this feature: the ticket
/// being closed is itself a child, so walking from the root would sweep in its
/// parent and its sibling.
///
///   root (open)
///   |- sibling (open)
///   `- focus (open)             <- the one being closed
///      |- kidOpen (open)
///      `- kidClosed (Closed)
///         `- grandchild (open)
final TicketTreeNode tree = TicketTreeNode.fromJson(
  treeJson(
    id: 'root',
    children: [
      treeJson(id: 'sibling'),
      treeJson(
        id: 'focus',
        children: [
          treeJson(id: 'kidOpen'),
          treeJson(
            id: 'kidClosed',
            status: 'Closed',
            children: [treeJson(id: 'grandchild')],
          ),
        ],
      ),
    ],
  ),
  focusId: 'focus',
);

void main() {
  group('find', () {
    test('reaches a node at any depth', () {
      expect(tree.find('grandchild')?.name, 'grandchild');
    });

    test('is null for an id the tree does not carry', () {
      expect(tree.find('nope'), isNull);
      expect(tree.find(''), isNull);
    });
  });

  group('openDescendantsOf', () {
    test("is the ticket's own subtree, never its relatives", () {
      // `CaseTreeView` returns the top of the whole tree, so walking from the
      // root would list the parent and the sibling as about to be closed.
      // Neither is touched by `_open_descendants`.
      final ids = tree.openDescendantsOf('focus').map((n) => n.id);
      expect(ids, isNot(contains('root')));
      expect(ids, isNot(contains('sibling')));
    });

    test('recurses through a closed child to reach an open grandchild', () {
      expect(tree.openDescendantsOf('focus').map((n) => n.id), [
        'kidOpen',
        'grandchild',
      ]);
    });

    test('leaves closed tickets out, because closing them changes nothing', () {
      expect(
        tree.openDescendantsOf('focus').map((n) => n.id),
        isNot(contains('kidClosed')),
      );
    });

    test('leaves an inactive ticket out, matching the backend', () {
      // An inactive row is a merged duplicate; `_open_descendants` skips it.
      final merged = TicketTreeNode.fromJson(
        treeJson(
          id: 'p',
          children: [
            treeJson(id: 'merged', isActive: false),
            treeJson(id: 'real'),
          ],
        ),
      );
      expect(merged.openDescendantsOf('p').map((n) => n.id), ['real']);
    });

    test('lists a ticket the viewer cannot open, without a name', () {
      // `/tree/` redacts it to an id, a status and `restricted`. It still
      // counts, because the close refuses a cascade that would take it.
      final redacted = TicketTreeNode.fromJson(
        treeJson(
          id: 'p',
          children: [
            {
              'id': 'hidden',
              'name': null,
              'restricted': true,
              'status': 'New',
              'is_active': true,
              'children': const [],
            },
          ],
        ),
      );
      final open = redacted.openDescendantsOf('p');
      expect(open.map((n) => n.id), ['hidden']);
      expect(open.single.restricted, isTrue);
      expect(open.single.name, TicketTreeNode.restrictedName);
      expect(redacted.restricted, isFalse);
    });

    test('is empty for a leaf, and for a ticket not in the tree', () {
      expect(tree.openDescendantsOf('kidOpen'), isEmpty);
      expect(tree.openDescendantsOf('nope'), isEmpty);
    });

    test('is not the same number as child_count', () {
      // The whole reason the prompt stopped quoting `child_count`: focus has
      // two direct children and closing it closes two tickets, but they are
      // not the same two.
      final direct = tree.find('focus')!.children.map((n) => n.id).toList();
      expect(direct, ['kidOpen', 'kidClosed']);
      expect(tree.openDescendantsOf('focus').map((n) => n.id), [
        'kidOpen',
        'grandchild',
      ]);
    });
  });

  group('subtreeTruncatedFor', () {
    test('is true when the depth cap was hit inside the subtree', () {
      final deep = TicketTreeNode.fromJson(
        treeJson(
          id: 'p',
          children: [treeJson(id: 'deep', truncated: true)],
        ),
      );
      expect(deep.subtreeTruncatedFor('p'), isTrue);
    });

    test('ignores a cap reached outside the subtree being closed', () {
      final elsewhere = TicketTreeNode.fromJson(
        treeJson(
          id: 'root',
          children: [
            treeJson(id: 'other', truncated: true),
            treeJson(id: 'focus'),
          ],
        ),
      );
      expect(elsewhere.subtreeTruncatedFor('focus'), isFalse);
    });

    test('is false for an ordinary tree', () {
      expect(tree.subtreeTruncatedFor('focus'), isFalse);
    });
  });

  group('cascadeSummary', () {
    test('says nothing else changes when nothing linked is open', () {
      expect(cascadeSummary(count: 0), contains('changes nothing else'));
    });

    test('counts and agrees with itself on number', () {
      expect(
        cascadeSummary(count: 1),
        contains('1 linked ticket is still open'),
      );
      expect(
        cascadeSummary(count: 3),
        contains('3 linked tickets are still open'),
      );
    });

    test('admits the list is a floor when the tree was cut short', () {
      // The close has no depth cap even though the tree endpoint does.
      expect(
        cascadeSummary(count: 2, truncated: true),
        contains('may be more'),
      );
      expect(cascadeSummary(count: 2), isNot(contains('may be more')));
    });

    test('matches what the web says, word for word', () {
      // Two clients, one sentence. A person who reads it on the phone and then
      // on the web should not have to work out whether they mean the same.
      expect(
        cascadeSummary(count: 2),
        '2 linked tickets are still open and will be closed with it.',
      );
    });
  });
}
