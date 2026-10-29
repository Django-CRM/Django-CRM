/**
 * Connected apps on screen, browser-safe. The navigation entry shows for an
 * admin where custom objects are offered (`data.customObjects` is not null).
 */
export const CONNECTED_APPS_HREF = '/settings/connected-apps';

const RESOURCE_WORDS: Record<string, string> = {
  opportunities: 'Deals',
  cases: 'Tickets',
  objects: 'Custom records',
  hooks: 'Subscriptions'
};

const capital = (word: string) => word.charAt(0).toUpperCase() + word.slice(1);

/** `leads:read` as "Leads: read"; a string with no action is shown as it is. */
export function scopeLabel(scope: string): string {
  const [resource, action] = scope.split(':');
  const noun = RESOURCE_WORDS[resource] ?? capital(resource.replace(/_/g, ' '));
  return action ? `${noun}: ${action}` : noun;
}

/** What an app named `name` is called on screen. */
export function kindLabel(kind: { kind: string; name: string | null }): string {
  return kind.name ?? 'Another app';
}

export const MAX_APP_NAME = 60;

export const KEY_NOTICE = 'Copy this key now. It will not be shown again.';

export const replaceSentence = (name: string) =>
  `Replace the key for ${name}? The old key stops working at once.`;

export const disconnectSentence = (name: string) =>
  `Disconnect ${name}? Its key stops working and its subscriptions are deleted. Records it created stay.`;

/** "Never" for an app that has made no request; otherwise `when` of the date. */
export const lastUsedLabel = (at: string | null, when: (at: string) => string) =>
  at ? when(at) : 'Never';
