import { describe, expect, it, vi, afterEach } from 'vitest';
import { csvCell, downloadImportErrors, importErrorsCsv } from './csv.js';

describe('csvCell', () => {
  it.each([
    ['plain', 'plain'],
    ['Acme = best', 'Acme = best'],
    ['a, b', '"a, b"'],
    ['say "hi"', '"say ""hi"""'],
    ['two\nlines', '"two\nlines"'],
    ['=1+1', "'=1+1"],
    ['+44 20', "'+44 20"],
    ['-2', "'-2"],
    ['@SUM(A1)', "'@SUM(A1)"],
    ['\t=cmd', "'\t=cmd"],
    [' =HYPERLINK("x")', `"' =HYPERLINK(""x"")"`],
    [3, '3'],
    [null, ''],
    [undefined, '']
  ])('%j becomes %j', (raw, cell) => {
    expect(csvCell(raw)).toBe(cell);
  });
});

describe('importErrorsCsv', () => {
  it('matches the mobile file: row,field,message, one error per line', () => {
    expect(
      importErrorsCsv([
        { row: 2, field: 'email', message: 'Not an email' },
        { row: 3, field: 'name', message: 'Say "hi", then\nleave' },
        { row: 4, field: 'phone', message: '=HYPERLINK("x")' },
        { row: 5, field: 'city', message: ' -1' }
      ])
    ).toBe(
      'row,field,message\n' +
        '2,email,Not an email\n' +
        '3,name,"Say ""hi"", then\nleave"\n' +
        `4,phone,"'=HYPERLINK(""x"")"\n` +
        "5,city,' -1"
    );
  });
});

describe('downloadImportErrors', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('names the file after the module and skips an empty list', () => {
    const click = vi.fn();
    /** @type {any} */
    const anchor = { click };
    vi.stubGlobal('document', { createElement: () => anchor });
    vi.stubGlobal('URL', { createObjectURL: () => 'blob:x', revokeObjectURL: vi.fn() });

    downloadImportErrors([], 'leads');
    expect(click).not.toHaveBeenCalled();

    downloadImportErrors([{ row: 1, field: 'email', message: 'x' }], 'leads');
    expect(anchor.download).toBe('leads-import-errors.csv');
    expect(click).toHaveBeenCalledOnce();
  });
});
