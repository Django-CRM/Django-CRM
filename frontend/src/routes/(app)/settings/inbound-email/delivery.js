/**
 * What an inbound address actually does with mail, as opposed to what its
 * on/off switch says.
 *
 * `is_active` is one of three gates a delivery has to clear, and the page used
 * to draw it as the only one: an address was either "Creating tickets" or
 * "Creating nothing". Two states were drawn as the first and behave as the
 * second, both silently, because inbound mail has no user watching it fail:
 *
 * 1. **An unimplemented provider.** `InboundMailbox.PROVIDER_CHOICES` offers
 *    four, and `InboundMailboxWebhookView.post` answers 501 for every one that
 *    is not `ses`. A Mailgun address marked active accepts mail and opens
 *    nothing, forever, and the form offered the four as equals.
 * 2. **An unpinned SES address.** The webhook needs both a valid SNS signature
 *    and a `topic_arn` that matches this mailbox, because a signature alone
 *    only proves the message came from *some* topic in *some* AWS account. A
 *    mailbox gets its pin one of two ways: an admin enters the Topic ARN, or a
 *    signature-verified SubscriptionConfirmation arrives from a topic in an AWS
 *    account the server allows (`INBOUND_SNS_ACCOUNT_IDS`). Until one of those
 *    happens it rejects everything.
 *
 * The states are ordered as the webhook itself checks them, so `deliveryState`
 * answers "which gate does a delivery fail first", and an address that clears
 * all three is the only one called live. Nothing here is a security control:
 * every gate is enforced in `cases/inbound_views.py`, and this only decides
 * what the page says about it.
 *
 * `mobile/lib/data/models/mailbox.dart` carries the same rules.
 */

/** The providers the webhook actually handles. Everything else 501s. */
export const SUPPORTED_PROVIDERS = ['ses'];

/**
 * The first gate mail to this address would fail, or `'live'`.
 *
 * @param {{ is_active?: boolean, provider?: string, has_topic_arn?: boolean }} mailbox
 * @returns {'off' | 'unsupported' | 'unconfirmed' | 'live'}
 */
export function deliveryState(mailbox) {
  if (!mailbox?.is_active) return 'off';
  if (!SUPPORTED_PROVIDERS.includes(mailbox.provider)) return 'unsupported';
  if (!mailbox.has_topic_arn) return 'unconfirmed';
  return 'live';
}

const LABEL = {
  off: 'Creating nothing',
  unsupported: 'Provider not wired up',
  unconfirmed: 'Not connected yet',
  live: 'Creating tickets'
};

const TONE = {
  off: 'clay',
  unsupported: 'rust',
  unconfirmed: 'clay',
  live: 'moss'
};

/** @param {string} state */
export function deliveryLabel(state) {
  return LABEL[state] ?? LABEL.off;
}

/** @param {string} state */
export function deliveryTone(state) {
  return TONE[state] ?? TONE.off;
}

/**
 * Why nothing is arriving, in the words the page uses, or null when it is.
 *
 * `providerLabel` is passed in rather than imported so this module stays free
 * of the enum map the page already holds.
 *
 * @param {string} state
 * @param {string} providerLabel
 */
export function deliveryExplanation(state, providerLabel) {
  if (state === 'off') {
    return 'Mail still arrives here and no ticket is opened. Nothing bounces, so whoever wrote gets silence rather than an error.';
  }
  if (state === 'unsupported') {
    return `${providerLabel} deliveries are not implemented. The webhook refuses them, so mail to this address becomes nothing whatever else is set here. Only AWS SES is wired up.`;
  }
  if (state === 'unconfirmed') {
    return 'Waiting for its SNS topic. Every message is refused until this address has a Topic ARN: an admin can enter it here, or it is set when AWS confirms a subscription from an AWS account this server allows.';
  }
  return null;
}

/**
 * The addresses that would actually open a ticket right now.
 *
 * Derived from the rows rather than read off the server's `totals.active`,
 * which counts `is_active` and nothing else. `GET /cases/mailboxes/` returns
 * every mailbox in the org unfiltered and unpaginated, so this counts the same
 * set the total does, and answers the question the header asks.
 *
 * @param {any[]} mailboxes
 */
export function deliveringCount(mailboxes) {
  return (mailboxes ?? []).filter((m) => deliveryState(m) === 'live').length;
}

/**
 * The rows worth a banner: on, and creating nothing.
 *
 * An off address is left out on purpose. Somebody chose that, and it is the
 * one state of the three the row already reads correctly.
 *
 * @param {any[]} mailboxes
 */
export function silentMailboxes(mailboxes) {
  return (mailboxes ?? []).filter((m) => {
    const state = deliveryState(m);
    return state === 'unsupported' || state === 'unconfirmed';
  });
}

/**
 * The provider select's label for one choice.
 *
 * The unimplemented three stay selectable: the column accepts them, an admin
 * may be recording an address before the integration exists, and hiding them
 * would silently rewrite a mailbox already set to one. They say so instead.
 *
 * @param {string} value
 * @param {string} label
 */
export function providerChoiceLabel(value, label) {
  return SUPPORTED_PROVIDERS.includes(value) ? label : `${label} (not wired up yet)`;
}
