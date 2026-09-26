/**
 * Keeping a stored target that the picker list cannot offer.
 *
 * Every people picker on the settings pages is built from
 * `getOrgPeopleAndTeams`, and `GetTeamsAndUsersView` filters profiles to
 * `is_active=True, org=request.profile.org`. None of the four `cases` settings
 * resources filter `is_active` on the write side, so a profile that was a
 * legal target when it was chosen stays the stored target after it is
 * deactivated. The list the form renders from can no longer represent it.
 *
 * A `<select>` whose bound value matches no option reports
 * `selectedIndex === -1`, and a select with no selected option contributes NO
 * entry to `FormData`. The action then reads that absence as `''` and the
 * body builder turns `''` into `null`. So an admin who opens an edit form to
 * change something unrelated, and saves, silently clears a target they never
 * touched. On an escalation policy that means Urgent breaches stop escalating
 * to anybody, with no message anywhere.
 *
 * The fix is to give the select something to match: render the stored value as
 * its own option, labelled so the admin can see why it is there. That is what
 * these two helpers find. They live in `$lib/v2/` rather than `$lib/server/v2/`
 * because a `.svelte` file cannot import from `$lib/server/`, and here they can
 * still be unit tested, unlike the markup that consumes them.
 *
 * "Not in the list" is the test, not "is_active is false": the pages that need
 * this do not receive `is_active` on a stored target, and absence from a list
 * that only ever contains active in-org profiles means the same thing. The one
 * case where it means something else is a failed picker fetch, which
 * `getOrgPeopleAndTeams` reports as empty lists; the form is unusable then
 * anyway, and keeping the stored value is still the right outcome.
 */

/**
 * The stored targets the options list cannot offer.
 *
 * @param {{ id: string }[]} options what the picker can currently show
 * @param {any[]} stored what the record actually points at
 * @returns {any[]} the entries of `stored` with no matching option, in order
 */
export function missingOptions(options, stored) {
  const known = new Set((options ?? []).map((o) => o?.id).filter(Boolean));
  return (stored ?? []).filter((s) => s && s.id && !known.has(s.id));
}

/**
 * The single stored target an options list cannot offer, or null.
 *
 * @param {{ id: string }[]} options
 * @param {any} stored a single target object, or null when nothing is set
 * @returns {any} the target when it is unrepresentable, null otherwise
 */
export function missingOption(options, stored) {
  return missingOptions(options, stored ? [stored] : [])[0] ?? null;
}

/**
 * How such an option reads. Not a bare name: the point is that the admin can
 * tell the difference between a person they can pick and a person who is only
 * there because the record already points at them.
 *
 * @param {string | null | undefined} name
 * @returns {string}
 */
export function inactiveOptionLabel(name) {
  return `${name || 'Unnamed'} (no longer active)`;
}

/**
 * Whether two id lists hold the same ids, ignoring order and repeats.
 *
 * For forms whose many-to-many picker cannot show everything the record
 * holds (an inactive person, an archived tag, a list that failed to load).
 * The page posts the stored ids that DO have a checkbox as `<name>_original`;
 * if the ticked ids equal them, nobody touched the picker, and the action
 * leaves the field out of the request so the server keeps what it has. Where
 * the server re-adds only active rows, resubmitting the ids cannot keep them.
 *
 * @param {string[]} a
 * @param {string[]} b
 * @returns {boolean}
 */
export function sameIds(a, b) {
  const left = new Set(a);
  const right = new Set(b);
  return left.size === right.size && [...left].every((id) => right.has(id));
}
