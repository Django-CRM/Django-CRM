import { describe, it, expect } from 'vitest';
import { treeRows, subtreeIds, parentCandidates, linkRefusal } from './tree.js';
import { RESTRICTED_TICKET_NAME } from '$lib/v2/enums.js';

/** @param {any} over */
function node(over = {}) {
  return { id: 'n', name: 'A ticket', status: 'New', is_active: true, children: [], ...over };
}

/**
 * What `/tree/` returns for a ticket that is itself a child: the TOP of the
 * tree, with a hidden sibling and a hidden node above a readable grandchild.
 *
 *   root
 *   ├── hiddenSibling (restricted)
 *   └── focus               <- this page
 *       └── hiddenChild (restricted)
 *           └── grandchild
 */
const TREE = node({
  id: 'root',
  name: 'Outage',
  children: [
    node({ id: 'hiddenSibling', name: null, restricted: true, status: 'Pending' }),
    node({
      id: 'focus',
      name: 'This one',
      children: [
        node({
          id: 'hiddenChild',
          name: null,
          restricted: true,
          children: [node({ id: 'grandchild', name: 'Mine', truncated: true })]
        })
      ]
    })
  ]
});

describe('treeRows', () => {
  it('flattens the whole tree top down, with depths', () => {
    expect(treeRows(TREE, 'focus').map((r) => [r.id, r.depth])).toEqual([
      ['root', 0],
      ['hiddenSibling', 1],
      ['focus', 1],
      ['hiddenChild', 2],
      ['grandchild', 3]
    ]);
  });

  it('marks the ticket this page is about, and only it', () => {
    const rows = treeRows(TREE, 'focus');
    expect(rows.filter((r) => r.focus).map((r) => r.id)).toEqual(['focus']);
  });

  it('names a ticket the viewer cannot open with the shared phrase, keeping its status', () => {
    const hidden = treeRows(TREE, 'focus').find((r) => r.id === 'hiddenSibling');
    expect(hidden).toMatchObject({
      name: RESTRICTED_TICKET_NAME,
      restricted: true,
      status: 'Pending'
    });
  });

  it('keeps a readable ticket under a hidden one at its real depth', () => {
    const grandchild = treeRows(TREE, 'focus').find((r) => r.id === 'grandchild');
    expect(grandchild).toMatchObject({ name: 'Mine', restricted: false, depth: 3 });
  });

  it('carries the depth cap so the page can say more is below', () => {
    const rows = treeRows(TREE, 'focus');
    expect(rows.filter((r) => r.truncated).map((r) => r.id)).toEqual(['grandchild']);
  });

  it('is empty without a tree', () => {
    expect(treeRows(null, 'focus')).toEqual([]);
  });
});

describe('subtreeIds', () => {
  it('is the ticket and everything under it, never its parent or siblings', () => {
    expect([...subtreeIds(TREE, 'focus')].sort()).toEqual(['focus', 'grandchild', 'hiddenChild']);
  });

  it('is just the ticket when there is no tree', () => {
    expect([...subtreeIds(null, 'focus')]).toEqual(['focus']);
  });
});

describe('parentCandidates', () => {
  const rows = [
    { id: 'focus', status: 'New' },
    { id: 'grandchild', status: 'New' },
    { id: 'current', status: 'Assigned' },
    { id: 'merged', status: 'Duplicate' },
    { id: 'ok', status: 'Pending' },
    { id: 'closed', status: 'Closed' }
  ];

  it('drops the ticket, its subtree, its current parent and merged tickets', () => {
    const picked = parentCandidates(rows, {
      exclude: subtreeIds(TREE, 'focus'),
      parentId: 'current'
    });
    expect(picked.map((r) => r.id)).toEqual(['ok', 'closed']);
  });

  it('keeps everything else when the ticket has no parent', () => {
    const picked = parentCandidates(rows, { exclude: new Set(['focus']) });
    expect(picked.map((r) => r.id)).toEqual(['grandchild', 'current', 'ok', 'closed']);
  });
});

describe('linkRefusal', () => {
  it("reads the endpoint's parent_id sentence without the key", () => {
    const err = { body: { parent_id: 'Linking would create a cycle.' } };
    expect(linkRefusal(err, 'fallback')).toBe('Linking would create a cycle.');
  });

  it('reads a detail sentence, which is how a 403 arrives', () => {
    const err = { body: { detail: 'You do not have Permission to perform this action' } };
    expect(linkRefusal(err, 'fallback')).toBe('You do not have Permission to perform this action');
  });

  it('accepts a list-shaped message', () => {
    const err = { body: { parent_id: ['Case tree is limited to 3 levels.'] } };
    expect(linkRefusal(err, 'fallback')).toBe('Case tree is limited to 3 levels.');
  });

  it('falls back when the response said nothing usable', () => {
    expect(linkRefusal({ body: {} }, 'Could not link this ticket.')).toBe(
      'Could not link this ticket.'
    );
    expect(linkRefusal(undefined, 'Could not link this ticket.')).toBe(
      'Could not link this ticket.'
    );
  });
});
