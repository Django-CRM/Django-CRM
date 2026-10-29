/** A comment author's display text: a connected app's name, else the email as before. */
export function authorText(
  details: { name?: string | null; email?: string | null } | null | undefined
): string {
  return details?.name || details?.email || '';
}
