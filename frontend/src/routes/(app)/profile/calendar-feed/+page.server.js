import { fail } from '@sveltejs/kit';
import {
  getCalendarFeed,
  issueCalendarFeed,
  disableCalendarFeed
} from '$lib/server/v2/calendar-feed.js';
import { readableError } from '$lib/server/v2/form-errors.js';

/**
 * Your own task calendar feed. No role check: every member may subscribe to
 * their own tasks, and the API answers only for the caller.
 *
 * @type {import('./$types').PageServerLoad}
 */
export async function load({ cookies }) {
  return { feed: await getCalendarFeed({ cookies }) };
}

/** @type {import('./$types').Actions} */
export const actions = {
  /** Turn the feed on, or replace its URL. The new URL is returned once. */
  issue: async ({ cookies }) => {
    try {
      return { url: await issueCalendarFeed({ cookies }) };
    } catch (/** @type {any} */ err) {
      return fail(400, { error: readableError(err, 'Could not create the calendar feed.') });
    }
  },

  /** Turn the feed off. The URL stops working at once. */
  disable: async ({ cookies }) => {
    try {
      await disableCalendarFeed({ cookies });
    } catch (/** @type {any} */ err) {
      return fail(400, { error: readableError(err, 'Could not turn the calendar feed off.') });
    }
    return { disabled: true };
  }
};
