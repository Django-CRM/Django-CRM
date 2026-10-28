import { missingOptions } from '#lib/v2/pickers.js';

export const REMINDER_CHOICES = [5, 15, 30, 60];

export function reminderLabel(minutes: number | null | undefined): string {
  return minutes ? `${minutes} minutes` : 'Off';
}

/**
 * The stored value as an option when the choices lack it, so a select never
 * submits its first entry in place of a value the API once accepted.
 */
export function reminderExtra(minutes: number | null | undefined): number[] {
  const stored = minutes ? [{ id: String(minutes) }] : [];
  return missingOptions(
    REMINDER_CHOICES.map((m) => ({ id: String(m) })),
    stored
  ).map((o) => Number(o.id));
}

export const DOCS_LEAD_ALERTS = 'https://django-crm.readthedocs.io/en/latest/features/lead-alerts/';
export const DOCS_WEBHOOKS = 'https://django-crm.readthedocs.io/en/latest/integrations/webhooks/';

export const WEBHOOK_FORMATS: { value: string; label: string; short: string }[] = [
  {
    value: 'json',
    label: 'Signed JSON (Zapier, n8n, your own code)',
    short: 'Signed JSON message'
  },
  { value: 'slack', label: 'Slack message (incoming webhook URL)', short: 'Slack message' },
  { value: 'teams', label: 'Teams message (Workflows webhook URL)', short: 'Teams message' }
];

export function webhookFormatName(format: string): string {
  return WEBHOOK_FORMATS.find((f) => f.value === format)?.short ?? format;
}
