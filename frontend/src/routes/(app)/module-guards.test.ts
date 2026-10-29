/**
 * Every folder of the app is either core or in the route map, and every page
 * of a module sits under a layout that runs the module guard.
 */
import { describe, it, expect } from 'vitest';
import { existsSync, readdirSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const { requirementFor } = await import('#lib/v2/modules.js');
const { moduleGuard } = await import('#lib/server/v2/modules.js');

const APP = path.dirname(fileURLToPath(import.meta.url));

const CORE = [
  'accounts',
  'contacts',
  'help',
  'notifications',
  'objects',
  'profile',
  'settings',
  'team',
  'welcome'
];
const CORE_SETTINGS = [
  'api-tokens',
  'apps',
  'audit-log',
  'automation',
  'billing',
  'connected-apps',
  'custom-fields',
  'lead-sources',
  'modules',
  'objects',
  'organization',
  'tags',
  'web-forms',
  'webhooks'
];

const folders = (dir: string) =>
  readdirSync(dir, { withFileTypes: true })
    .filter((entry) => entry.isDirectory())
    .map((entry) => entry.name);

function pageFolders(dir: string): string[] {
  const here = ['+page.svelte', '+page.server.js', '+page.server.ts'].some((f) =>
    existsSync(path.join(dir, f))
  );
  const below = folders(dir).flatMap((name) => pageFolders(path.join(dir, name)));
  return here ? [dir, ...below] : below;
}

const routeOf = (dir: string) => '/' + path.relative(APP, dir).split(path.sep).join('/');

/**
 * Whether a `+layout.server` file exporting the guard sits at `dir` or above it,
 * below the `(app)` shell. Either extension: the routes are moving to TypeScript.
 */
async function guarded(dir: string) {
  for (let at = dir; at !== APP; at = path.dirname(at)) {
    for (const name of ['+layout.server.ts', '+layout.server.js']) {
      const file = path.join(at, name);
      if (existsSync(file) && (await import(file)).load === moduleGuard) return true;
    }
  }
  return false;
}

describe('module guards', () => {
  it('classifies every top-level folder and every settings folder', () => {
    const entries = [
      ...folders(APP).map((name) => ({ route: `/${name}`, core: CORE.includes(name) })),
      ...folders(path.join(APP, 'settings')).map((name) => ({
        route: `/settings/${name}`,
        core: CORE_SETTINGS.includes(name)
      }))
    ];
    // Core and module at once is as wrong as neither.
    const misfiled = entries
      .filter(({ route, core }) => core === (requirementFor(route) !== null))
      .map(({ route }) => route);
    expect(misfiled).toEqual([]);
  });

  it('guards every folder that holds a module page', async () => {
    const pages = pageFolders(APP).filter((dir) => requirementFor(routeOf(dir)) !== null);
    expect(pages.length).toBeGreaterThan(50);
    const unguarded = [];
    for (const dir of pages) if (!(await guarded(dir))) unguarded.push(routeOf(dir));
    expect(unguarded).toEqual([]);
  });
});
