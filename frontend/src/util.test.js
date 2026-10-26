import { describe, expect, it } from 'vitest';
import { cellText, compareValues, countBy, csvEscape, formatBytes, guardCsvCell, plural, toCsv } from './util.js';

describe('cellText', () => {
  it('renders every kind of value', () => {
    expect(cellText(null)).toBe('');
    expect(cellText(undefined)).toBe('');
    expect(cellText(0)).toBe('0');
    expect(cellText(['a', 'b'])).toBe('a, b');
    expect(cellText(true)).toBe('yes');
    expect(cellText(false)).toBe('');
    expect(cellText({ filter: 1, text: 2 })).toBe('filter: 1, text: 2');
  });
});

describe('compareValues', () => {
  it('sorts numbers numerically, text naturally, and empties last', () => {
    expect(['b10', 'b2', 'a1'].sort(compareValues)).toEqual(['a1', 'b2', 'b10']);
    expect([10, 2, 33].sort(compareValues)).toEqual([2, 10, 33]);
    expect(['x', null, 'a', '', undefined].sort(compareValues)).toEqual(['a', 'x', null, '', undefined]);
  });
  it('ignores case', () => {
    expect(compareValues('apple', 'Banana')).toBeLessThan(0);
  });
});

describe('countBy', () => {
  it('counts in first-seen order', () => {
    const counts = countBy([{ k: 'b' }, { k: 'a' }, { k: 'b' }], 'k');
    expect([...counts.entries()]).toEqual([
      ['b', 2],
      ['a', 1],
    ]);
  });
});

describe('CSV export', () => {
  it('defuses spreadsheet formulas but keeps negative numbers', () => {
    expect(guardCsvCell('=SUM(A1)')).toBe("'=SUM(A1)");
    expect(guardCsvCell('+1+1')).toBe("'+1+1");
    expect(guardCsvCell('@cmd')).toBe("'@cmd");
    expect(guardCsvCell('-cmd|x')).toBe("'-cmd|x");
    expect(guardCsvCell('-5')).toBe('-5');
    expect(guardCsvCell('-.5')).toBe('-.5');
    expect(guardCsvCell('SUM([Sales])')).toBe('SUM([Sales])');
  });

  it('quotes commas, quotes and newlines', () => {
    expect(csvEscape('plain')).toBe('plain');
    expect(csvEscape('a,b')).toBe('"a,b"');
    expect(csvEscape('say "hi"')).toBe('"say ""hi"""');
    expect(csvEscape('line1\nline2')).toBe('"line1\nline2"');
  });

  it('builds a CSV, preferring exportValue over the display value', () => {
    const columns = [
      { label: 'Name', value: (r) => r.name },
      { label: 'SQL', value: (r) => r.sql.slice(0, 5), exportValue: (r) => r.sql },
      { label: 'Tags', value: (r) => r.tags },
    ];
    const csv = toCsv(columns, [{ name: 'A, Inc', sql: 'SELECT 1, 2', tags: ['x', 'y'] }]);
    expect(csv).toBe('Name,SQL,Tags\r\n"A, Inc","SELECT 1, 2","x, y"');
  });

  it('guards formulas in header and body', () => {
    const csv = toCsv([{ label: '=bad', value: (r) => r.v }], [{ v: '=1+1' }]);
    expect(csv).toBe("'=bad\r\n'=1+1");
  });
});

describe('formatting', () => {
  it('formats bytes and plurals', () => {
    expect(formatBytes(0)).toBe('0 B');
    expect(formatBytes(1536)).toBe('1.5 KB');
    expect(formatBytes(10 * 1024 * 1024)).toBe('10 MB');
    expect(plural(1, 'sheet')).toBe('1 sheet');
    expect(plural(2, 'sheet')).toBe('2 sheets');
    expect(plural(0, 'query', 'queries')).toBe('0 queries');
  });
});
