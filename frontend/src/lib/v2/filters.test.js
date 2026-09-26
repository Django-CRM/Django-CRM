import { describe, it, expect } from 'vitest';
import {
  FILTERS,
  fieldKeys,
  withoutParam,
  withParams,
  activeChips,
  activePresetKey
} from '$lib/v2/filters.js';

/**
 * Each page's module exports the allow-list its list function forwards. As
 * descriptors are added by later tasks this map grows with them, and the loop
 * below covers whatever is present.
 */
const MODULES = {
  tickets: () => import('$lib/server/v2/tickets.js'),
  leads: () => import('$lib/server/v2/leads.js'),
  contacts: () => import('$lib/server/v2/contacts.js'),
  pipeline: () => import('$lib/server/v2/deals.js'),
  tasks: () => import('$lib/server/v2/tasks.js'),
  accounts: () => import('$lib/server/v2/accounts.js'),
  invoices: () => import('$lib/server/v2/invoices.js'),
  estimates: () => import('$lib/server/v2/estimates.js'),
  solutions: () => import('$lib/server/v2/solutions.js'),
  documents: () => import('$lib/server/v2/documents.js'),
  recurring: () => import('$lib/server/v2/recurring.js')
};

describe('every descriptor agrees with its module allow-list', () => {
  for (const pageKey of Object.keys(FILTERS)) {
    it(`${pageKey}: every field key is forwarded by the module`, async () => {
      // A descriptor with no MODULES entry must fail here with a sentence,
      // not a bare TypeError on `MODULES[pageKey] is not a function`. Adding a
      // descriptor without registering its module is the expected mistake.
      expect(MODULES[pageKey], `add "${pageKey}" to MODULES in this file`).toBeDefined();
      const mod = await MODULES[pageKey]();
      expect(mod.FILTER_FIELDS, `${pageKey}.js must export FILTER_FIELDS`).toBeDefined();
      for (const key of fieldKeys(FILTERS[pageKey])) {
        expect(mod.FILTER_FIELDS, `${pageKey} field "${key}"`).toContain(key);
      }
    });

    it(`${pageKey}: exactly one preset is the default`, () => {
      const empty = FILTERS[pageKey].presets.filter((p) => Object.keys(p.params).length === 0);
      expect(empty.length).toBe(1);
    });

    it(`${pageKey}: every preset param is forwarded or is a page-local view param`, async () => {
      const mod = await MODULES[pageKey]();
      // Params a page's own load interprets rather than forwarding to the API.
      // `open` and `rotten` are pipeline's version of `all`: `?open=true` and
      // `?rotten=true` (opportunity_views.py:183,186) are read directly by
      // pipeline's load, the same way tickets and tasks read `all` themselves,
      // so neither belongs on deals.js's FILTER_FIELDS allow-list.
      // Params a page's own `load` interprets rather than forwarding to the
      // API. `archived` is documents' opt-in to seeing archived rows, the
      // same shape as contacts' `inactive`.
      const VIEW_PARAMS = ['all', 'inactive', 'archived', 'visibility', 'view', 'open', 'rotten'];
      for (const preset of FILTERS[pageKey].presets) {
        for (const key of Object.keys(preset.params)) {
          if (VIEW_PARAMS.includes(key)) continue;
          expect(mod.FILTER_FIELDS, `${pageKey} preset "${preset.key}" param "${key}"`).toContain(
            key
          );
        }
      }
    });
  }
});

describe('leads: only statuses the list endpoint can actually show', () => {
  // backend/leads/views/lead_views.py:92 excludes status="converted" from the
  // base queryset unconditionally, before any query param is read, and :166
  // splits what remains into `open_leads` and a `close_leads` block that
  // `listLeads` (lib/server/v2/leads.js) never reads. So the leads list can
  // only ever render "assigned", "in process" or "recycled". Offering
  // "Converted" or "Closed" as a filter option would submit a value the page
  // can never return a row for, which reads as "you have none" rather than
  // "this page cannot show them".
  it('offers neither "converted" nor "closed" as a status option', () => {
    const statusField = FILTERS.leads.fields.find((f) => f.key === 'status');
    expect(statusField.options).not.toContain('converted');
    expect(statusField.options).not.toContain('closed');
  });

  it('writes no preset param the list endpoint does not read', () => {
    // `is_converted` is read only by the PATCH/conversion action, never by
    // the list GET, so no preset may promise a view built on it.
    for (const preset of FILTERS.leads.presets) {
      expect(Object.keys(preset.params)).not.toContain('is_converted');
    }
  });
});

describe('documents: exactly one filter field', () => {
  // `DocumentListView.get` (backend/common/views/document_views.py:71-79) reads
  // only `title`, `status` and `shared_to`. `Document` has no `tags` relation
  // at all, and `shared_to` is passed straight to `json.loads`, so an ordinary
  // `?shared_to=<uuid>` throws `JSONDecodeError` and answers 500. If a future
  // change adds a field here, re-check the backend view before doing it; do
  // not "helpfully" restore `tags` or `created_by`.
  it('offers only "status"', () => {
    expect(FILTERS.documents.fields.map((f) => f.key)).toEqual(['status']);
  });
});

describe('documents: archived rows stay out of the default view', () => {
  // Archiving a document is how you get it out of the way. If the empty-params
  // preset were the "all" one, a bare /documents would show archived rows back
  // alongside the live ones and undo the only thing archiving does. So `active`
  // is the default and `all` opts in through `archived=1`, which
  // `documents/+page.server.js` reads. Same shape as contacts' `inactive`.
  it('makes "active" the empty-params default', () => {
    const active = FILTERS.documents.presets.find((p) => p.key === 'active');
    expect(Object.keys(active.params)).toEqual([]);
  });

  it('makes seeing archived rows an explicit opt-in', () => {
    const all = FILTERS.documents.presets.find((p) => p.key === 'all');
    expect(all.params).toEqual({ archived: '1' });
  });

  it('never lets a preset send status=active, which the load owns', () => {
    // The default narrowing lives in the load, not the descriptor, so that an
    // explicit Status choice can override it. A preset writing status=active
    // would put a second, competing source of truth in the URL.
    for (const preset of FILTERS.documents.presets) {
      expect(preset.params.status).toBeUndefined();
    }
  });
});

describe('URL helpers', () => {
  const url = new URL('http://x/tickets?priority=High&all=1');

  it('withoutParam drops one key and keeps the rest', () => {
    expect(withoutParam(url, 'priority')).toBe('/tickets?all=1');
  });

  it('withoutParam returns a bare path when nothing is left', () => {
    expect(withoutParam(new URL('http://x/tickets?a=1'), 'a')).toBe('/tickets');
  });

  it('withParams treats an empty value as a removal', () => {
    expect(withParams(url, { priority: '' })).toBe('/tickets?all=1');
  });
});

describe('activePresetKey', () => {
  it('returns the default preset key for a bare URL', () => {
    expect(activePresetKey('tickets', new URL('http://x/tickets'))).toBe('open');
  });

  it('matches a non-@me preset on its literal param value', () => {
    expect(activePresetKey('tickets', new URL('http://x/tickets?sla_breached=true'))).toBe(
      'breaching'
    );
  });

  it('matches the "mine" preset when assigned_to equals the resolved viewer id', () => {
    expect(activePresetKey('tickets', new URL('http://x/tickets?assigned_to=p1'), 'p1')).toBe(
      'mine'
    );
  });

  it('does not match "mine" when assigned_to names someone other than the viewer', () => {
    // Regression test: the old matcher accepted "@me" against ANY assigned_to
    // value, so filtering by a colleague's id was mislabelled as "Mine".
    expect(activePresetKey('tickets', new URL('http://x/tickets?assigned_to=p2'), 'p1')).toBe(
      'open'
    );
  });

  it('does not match "mine" when the viewer id is unresolved', () => {
    expect(activePresetKey('tickets', new URL('http://x/tickets?assigned_to=p1'), null)).toBe(
      'open'
    );
  });
});

describe('activeChips', () => {
  it('renders a chip only for params that are actually set', () => {
    const chips = activeChips('tickets', new URL('http://x/tickets?priority=High'));
    expect(chips).toHaveLength(1);
    expect(chips[0]).toMatchObject({ key: 'priority', label: 'Priority', value: 'High' });
  });

  it('resolves a person id to a name and still removes by id', () => {
    const chips = activeChips('tickets', new URL('http://x/tickets?assigned_to=p1'), {
      people: [{ id: 'p1', name: 'Ada' }]
    });
    expect(chips[0].value).toBe('Ada');
    expect(chips[0].href).toBe('/tickets');
  });

  it('falls back to the raw id when the lookup misses', () => {
    const chips = activeChips('tickets', new URL('http://x/tickets?assigned_to=p9'), {
      people: []
    });
    expect(chips[0].value).toBe('p9');
  });
});

describe('pipeline presets', () => {
  // The pipeline base queryset does not exclude closed stages
  // (opportunity_views.py), so the empty-params preset is "All deals", not
  // "Open". A bare URL must resolve to that preset, not to "open", or the
  // preset menu would highlight a view the query is not actually running.
  it('returns "all" for a bare URL, not "open"', () => {
    expect(activePresetKey('pipeline', new URL('http://x/pipeline'))).toBe('all');
  });

  it('matches "open" only when ?open=true is actually set', () => {
    expect(activePresetKey('pipeline', new URL('http://x/pipeline?open=true'))).toBe('open');
  });

  it('matches "stalled" on ?rotten=true', () => {
    expect(activePresetKey('pipeline', new URL('http://x/pipeline?rotten=true'))).toBe('stalled');
  });

  it('matches "mine" against the resolved viewer id only', () => {
    expect(activePresetKey('pipeline', new URL('http://x/pipeline?assigned_to=p1'), 'p1')).toBe(
      'mine'
    );
    expect(activePresetKey('pipeline', new URL('http://x/pipeline?assigned_to=p1'), 'p2')).toBe(
      'all'
    );
  });
});

describe('tasks presets', () => {
  it('returns "open" for a bare URL', () => {
    expect(activePresetKey('tasks', new URL('http://x/tasks'))).toBe('open');
  });

  it('matches "all" on ?all=1, the same spelling tickets uses', () => {
    expect(activePresetKey('tasks', new URL('http://x/tasks?all=1'))).toBe('all');
  });

  it('matches "mine" against the resolved viewer id only', () => {
    expect(activePresetKey('tasks', new URL('http://x/tasks?assigned_to=p1'), 'p1')).toBe('mine');
  });
});

describe('number-range chips (pipeline "Value")', () => {
  it('renders a half-open floor as "over X"', () => {
    const chips = activeChips('pipeline', new URL('http://x/pipeline?amount__gte=5000'));
    expect(chips).toHaveLength(1);
    expect(chips[0]).toMatchObject({ key: 'amount', label: 'Value', value: 'over 5000' });
  });

  it('renders a half-open ceiling as "under X"', () => {
    const chips = activeChips('pipeline', new URL('http://x/pipeline?amount__lte=10000'));
    expect(chips[0].value).toBe('under 10000');
  });

  it('renders a closed range as "X to Y"', () => {
    const chips = activeChips(
      'pipeline',
      new URL('http://x/pipeline?amount__gte=5000&amount__lte=10000')
    );
    expect(chips[0].value).toBe('5000 to 10000');
  });

  it('removes both bounds through one href', () => {
    const chips = activeChips(
      'pipeline',
      new URL('http://x/pipeline?amount__gte=5000&amount__lte=10000&stage=PROSPECTING')
    );
    const valueChip = chips.find((c) => c.key === 'amount');
    expect(valueChip.href).toBe('/pipeline?stage=PROSPECTING');
  });
});

describe('deal stage chip', () => {
  it('names the stage from the lookup the page passes, since stages come from the API', () => {
    const chips = activeChips('pipeline', new URL('http://x/pipeline?stage=SIGNED'), {
      stages: [{ id: 'SIGNED', name: 'Signed' }]
    });
    expect(chips).toEqual([{ key: 'stage', label: 'Stage', value: 'Signed', href: '/pipeline' }]);
  });

  it('falls back to the code when the stage is not in the lookup', () => {
    const chips = activeChips('pipeline', new URL('http://x/pipeline?stage=GONE'));
    expect(chips[0].value).toBe('GONE');
  });
});
