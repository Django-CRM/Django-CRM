import { describe, it, expect } from 'vitest';
import {
  findNode,
  openDescendants,
  subtreeTruncated,
  cascadeSummary,
  closeResultMessage,
  cascadedCount,
  RESTRICTED_NAME
} from './close.js';

/** @param {any} over */
function node(over = {}) {
  return {
    id: 'n',
    name: 'A ticket',
    status: 'New',
    is_active: true,
    children: [],
    ...over
  };
}

/**
 * A tree where the ticket being closed is itself a child, which is the shape
 * that broke the old dialog:
 *
 *   root (open)
 *   ├── sibling (open)
 *   └── focus (open)          <- the one being closed
 *       ├── kidOpen (open)
 *       └── kidClosed (Closed)
 *           └── grandchild (open)
 */
const TREE = node({
  id: 'root',
  name: 'Root',
  children: [
    node({ id: 'sibling', name: 'Sibling' }),
    node({
      id: 'focus',
      name: 'Focus',
      children: [
        node({ id: 'kidOpen', name: 'Kid open' }),
        node({
          id: 'kidClosed',
          name: 'Kid closed',
          status: 'Closed',
          children: [node({ id: 'grandchild', name: 'Grandchild' })]
        })
      ]
    })
  ]
});

describe('findNode', () => {
  it('finds the ticket being closed anywhere in the tree', () => {
    expect(findNode(TREE, 'grandchild')?.name).toBe('Grandchild');
  });

  it('is null for an id the tree does not carry', () => {
    expect(findNode(TREE, 'nope')).toBeNull();
    expect(findNode(null, 'focus')).toBeNull();
  });
});

describe('openDescendants', () => {
  it("is the focus ticket's own subtree, never its relatives", () => {
    // The bug in the version this replaces: it walked from `root` and
    // collected every open node but the focus, so a ticket that is itself a
    // child listed its parent and siblings as about to be closed.
    const ids = openDescendants(TREE, 'focus').map((d) => d.id);
    expect(ids).not.toContain('root');
    expect(ids).not.toContain('sibling');
  });

  it('recurses through a closed child to reach an open grandchild', () => {
    // `_open_descendants` skips a closed node but still walks into it, so the
    // grandchild does close. Listing only direct children would understate it.
    expect(openDescendants(TREE, 'focus').map((d) => d.id)).toEqual(['kidOpen', 'grandchild']);
  });

  it('leaves closed tickets out, because closing them changes nothing', () => {
    expect(openDescendants(TREE, 'focus').map((d) => d.id)).not.toContain('kidClosed');
  });

  it('leaves an inactive ticket out, matching the backend', () => {
    // An inactive row is a merged duplicate; the backend's `is_active` check
    // skips it.
    const tree = node({
      id: 'p',
      children: [node({ id: 'merged', is_active: false }), node({ id: 'real' })]
    });
    expect(openDescendants(tree, 'p').map((d) => d.id)).toEqual(['real']);
  });

  it('lists a ticket the viewer cannot open, without a name', () => {
    // `/tree/` redacts it to an id, a status and `restricted`. It still
    // counts, because the close refuses a cascade that would take it.
    const tree = node({
      id: 'p',
      children: [
        { id: 'hidden', name: null, restricted: true, status: 'New', is_active: true, children: [] }
      ]
    });
    expect(openDescendants(tree, 'p')).toEqual([
      { id: 'hidden', name: RESTRICTED_NAME, status: 'New', restricted: true }
    ]);
  });

  it('is empty for a leaf, and for a ticket not in the tree', () => {
    expect(openDescendants(TREE, 'kidOpen')).toEqual([]);
    expect(openDescendants(TREE, 'nope')).toEqual([]);
  });
});

describe('subtreeTruncated', () => {
  it('is true when the API stopped at its depth cap inside the subtree', () => {
    const tree = node({
      id: 'p',
      children: [node({ id: 'deep', truncated: true })]
    });
    expect(subtreeTruncated(tree, 'p')).toBe(true);
  });

  it('ignores a cap reached outside the subtree being closed', () => {
    const tree = node({
      id: 'root',
      children: [node({ id: 'elsewhere', truncated: true }), node({ id: 'focus' })]
    });
    expect(subtreeTruncated(tree, 'focus')).toBe(false);
  });

  it('is false for an ordinary tree', () => {
    expect(subtreeTruncated(TREE, 'focus')).toBe(false);
  });
});

describe('cascadeSummary', () => {
  it('says nothing else changes when nothing linked is open', () => {
    expect(cascadeSummary({ count: 0 })).toContain('changes nothing else');
  });

  it('counts and agrees with itself on number', () => {
    expect(cascadeSummary({ count: 1 })).toContain('1 linked ticket is still open');
    expect(cascadeSummary({ count: 3 })).toContain('3 linked tickets are still open');
  });

  it('admits the list is a floor when the tree was cut short', () => {
    // The close has no depth cap even though the tree endpoint does, so more
    // can close than the confirm step is able to name.
    expect(cascadeSummary({ count: 2, truncated: true })).toContain('may be more');
    expect(cascadeSummary({ count: 2 })).not.toContain('may be more');
  });
});

describe('closeResultMessage', () => {
  it('reports what the server closed, not what was asked for', () => {
    expect(closeResultMessage({ cascade: true, cascaded: 2 })).toBe(
      'Ticket closed, and 2 linked tickets with it.'
    );
    expect(closeResultMessage({ cascade: true, cascaded: 1 })).toBe(
      'Ticket closed, and 1 linked ticket with it.'
    );
  });

  it('says nothing else changed when the cascade closed nothing', () => {
    expect(closeResultMessage({ cascade: true, cascaded: 0 })).toContain('Nothing linked was open');
  });

  it('claims no cascade when the box was unticked', () => {
    expect(closeResultMessage({ cascade: false, cascaded: 0 })).toBe('Ticket closed.');
  });
});

describe('cascadedCount', () => {
  it('counts the ids the endpoint returned', () => {
    expect(cascadedCount({ cascaded_case_ids: ['a', 'b'] })).toBe(2);
  });

  it('a body without the key means none, not unknown', () => {
    expect(cascadedCount({})).toBe(0);
    expect(cascadedCount(null)).toBe(0);
  });
});
