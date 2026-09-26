import { defineConfig } from 'vitest/config';
import path from 'node:path';

/**
 * THE CEILING OF THIS HARNESS
 *
 * This is a bare Vite config, not the app's own `vite.config.js`, so none of
 * SvelteKit's virtual modules exist here by default. Two aliases are wired in
 * below because `$lib/server/v2/leads.js:26` and `$lib/api-helpers.js:8` both
 * import `$env/dynamic/public` at module scope: it is a Vite virtual module
 * generated at build/dev time, not a real file, so a plain `node`/vitest run
 * cannot resolve it without a stand-in. `$lib` is aliased because
 * `path.resolve('./src/lib')` is the only way to reach it outside SvelteKit's
 * own resolution.
 *
 * That is as far as this config goes. It resolves `$lib`,
 * `$env/dynamic/public` and `$env/dynamic/private` (an empty stub, for
 * `$lib/server/relay.js`) and nothing else: `$app/forms`, `$app/navigation`
 * and `$env/static/*` are all still unresolvable here, and there is no Svelte plugin, so `.svelte` files cannot be compiled
 * at all. `include: ['src/**\/*.test.js']` will happily pick up a test placed
 * anywhere under `src/`, including next to a route module or a component, but
 * importing either from a test run through this config fails with an opaque
 * "Failed to resolve import" rather than a clear "not supported here".
 *
 * This harness therefore covers server modules only (`$lib/server/v2/*.js`
 * and similar, functions over `apiRequest` with no SvelteKit runtime
 * dependency). Testing a route module (`+page.server.js`, which pulls in
 * `$app/forms` via its actions) or a `.svelte` component means moving
 * `test: {}` into the app's own `vite.config.js`, where the `sveltekit()`
 * plugin already provides `$lib`, `$app/*`, `$env/*` and Svelte compilation
 * for free. That is a real tradeoff, not a strict improvement: it also pulls
 * `sentrySvelteKit()` and `tailwindcss()` into every test run, which this
 * standalone config exists specifically to avoid.
 *
 * `path.resolve('./src/lib')` below resolves against `process.cwd()`, not
 * against this file's location, so `pnpm test` (and `vitest` directly) must
 * be run from `frontend/`. Running it from the repo root or from `src/`
 * silently resolves `$lib` to the wrong place.
 */
export default defineConfig({
  test: {
    // Server modules only. They are plain functions over `apiRequest`, so they
    // need no DOM, and a node environment keeps the run fast.
    environment: 'node',
    include: ['src/**/*.test.js'],
    maxWorkers: '50%'
  },
  resolve: {
    alias: {
      $lib: path.resolve('./src/lib'),
      '$env/dynamic/public': path.resolve('./test/stubs/env-dynamic-public.js'),
      '$env/dynamic/private': path.resolve('./test/stubs/env-dynamic-private.js')
    }
  }
});
