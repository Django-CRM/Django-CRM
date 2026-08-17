import { describe, it, expect } from 'vitest';
import { readableError } from './form-errors.js';

/**
 * `apiRequest` attaches the parsed response body to the error it throws
 * (`failure.body`), as well as flattening it into a message. These build the
 * same pair so the tests exercise the real input shape rather than a guess.
 *
 * @param {any} body
 * @param {string} message
 */
const thrown = (body, message) => Object.assign(new Error(message), { body, status: 400 });

describe('readableError', () => {
  it('reads a string `errors` off the response body', () => {
    // The `{error: true, errors: "<sentence>"}` envelope, used in 300-odd
    // places in the backend. `apiRequest` has no branch for it (its checks
    // look for `errors` as an OBJECT, and for `error` as a STRING), so it
    // falls through to the generic entry-flattener and produces
    // "errors: Add an email field...". That prefix is the JSON key leaking
    // into a sentence a person reads.
    const err = thrown(
      { error: true, errors: 'Add an email field before publishing.' },
      'errors: Add an email field before publishing.'
    );
    expect(readableError(err, 'fallback')).toBe('Add an email field before publishing.');
  });

  it('keeps the field name when `errors` is a per-field map', () => {
    // Unchanged: a validation failure needs to say which field failed, and
    // `apiRequest` already flattens this correctly.
    const err = thrown(
      { error: true, errors: { name: ['This field may not be blank.'] } },
      'name: This field may not be blank.'
    );
    expect(readableError(err, 'fallback')).toBe('name: This field may not be blank.');
  });

  it('reads a DRF `detail`', () => {
    const err = thrown({ detail: 'Not found.' }, 'Not found.');
    expect(readableError(err, 'fallback')).toBe('Not found.');
  });

  it('parses field errors out of a JSON message when there is no body', () => {
    const err = new Error('Request failed {"errors": {"name": ["Too long."]}}');
    expect(readableError(err, 'fallback')).toBe('name: Too long.');
  });

  it('unwraps non_field_errors rather than printing the key', () => {
    const err = new Error('{"errors": {"non_field_errors": ["Pick one or the other."]}}');
    expect(readableError(err, 'fallback')).toBe('Pick one or the other.');
  });

  it('falls back when the response explained nothing', () => {
    expect(readableError(new Error(''), 'Could not save.')).toBe('Could not save.');
    expect(readableError(null, 'Could not save.')).toBe('Could not save.');
  });

  it('returns the message when it is prose rather than JSON', () => {
    expect(readableError(new Error('fetch failed'), 'Could not save.')).toBe('fetch failed');
  });

  it('ignores an empty string body rather than returning it', () => {
    // A blank `errors` would otherwise put an empty message on screen, which
    // reads as "it worked" beside a failed save.
    const err = thrown({ error: true, errors: '   ' }, 'errors:    ');
    expect(readableError(err, 'Could not save.')).toBe('Could not save.');
  });
});
