import { mergeRoute } from '$lib/server/v2/duplicates.js';

/** Side by side with `?with=<id>`, then merge. See `mergeRoute`. */
const route = mergeRoute('contacts');

/** @type {import('./$types').PageServerLoad} */
export const load = route.load;

/** @type {import('./$types').Actions} */
export const actions = route.actions;
