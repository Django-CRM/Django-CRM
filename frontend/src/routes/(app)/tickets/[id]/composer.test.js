import { describe, it, expect } from 'vitest';
import { insertAtCaret } from './composer.js';

describe('insertAtCaret', () => {
  it('fills an empty box', () => {
    expect(insertAtCaret('', 0, 0, 'Hi Liz')).toEqual({ text: 'Hi Liz', caret: 6 });
  });

  it('inserts at the caret', () => {
    expect(insertAtCaret('Hello . Bye', 6, 6, 'there')).toEqual({
      text: 'Hello there. Bye',
      caret: 11
    });
  });

  it('replaces the selection', () => {
    expect(insertAtCaret('Hello XXX', 6, 9, 'Liz')).toEqual({ text: 'Hello Liz', caret: 9 });
  });

  it('appends when the browser reported no caret', () => {
    expect(insertAtCaret('Hello', null, null, ' Liz')).toEqual({ text: 'Hello Liz', caret: 9 });
  });

  it('never reads outside the text', () => {
    expect(insertAtCaret('ab', 9, 2, '!')).toEqual({ text: 'ab!', caret: 3 });
    expect(insertAtCaret('ab', -3, 1, '!')).toEqual({ text: '!b', caret: 1 });
  });
});
