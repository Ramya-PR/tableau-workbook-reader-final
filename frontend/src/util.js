// Pure helpers (unit-tested in util.test.js).

/** Plain-text form of any cell value, used for filtering, sorting and CSV export. */
export function cellText(value) {
  if (value === null || value === undefined) return '';
  if (Array.isArray(value)) return value.join(', ');
  if (typeof value === 'boolean') return value ? 'yes' : '';
  if (typeof value === 'object') {
    return Object.entries(value)
      .map(([key, count]) => `${key}: ${count}`)
      .join(', ');
  }
  return String(value);
}

/** Ascending comparison: empty values last, numbers numerically, text naturally. */
export function compareValues(a, b) {
  const aEmpty = a === null || a === undefined || a === '';
  const bEmpty = b === null || b === undefined || b === '';
  if (aEmpty || bEmpty) return aEmpty === bEmpty ? 0 : aEmpty ? 1 : -1;
  if (typeof a === 'number' && typeof b === 'number') return a - b;
  return cellText(a).localeCompare(cellText(b), undefined, { numeric: true, sensitivity: 'base' });
}

/** Counts of each distinct value of `key`, in first-seen order. */
export function countBy(rows, key) {
  const counts = new Map();
  rows.forEach((row) => counts.set(row[key], (counts.get(row[key]) ?? 0) + 1));
  return counts;
}

/**
 * Stop spreadsheet apps from running cell text as a formula (CSV injection).
 * Plain negative numbers such as "-5" or "-.5" are left alone.
 */
export function guardCsvCell(text) {
  if (/^[=+@\t\r]/.test(text) || /^-(?![0-9.])/.test(text)) return `'${text}`;
  return text;
}

export function csvEscape(text) {
  return /[",\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

/** columns: [{ label, value(row), exportValue?(row) }] -> CSV text */
export function toCsv(columns, rows) {
  const header = columns.map((c) => csvEscape(guardCsvCell(c.label))).join(',');
  const body = rows.map((row) =>
    columns
      .map((c) => {
        const raw = c.exportValue ? c.exportValue(row) : c.value(row);
        return csvEscape(guardCsvCell(cellText(raw)));
      })
      .join(','),
  );
  return [header, ...body].join('\r\n');
}

export function downloadCsv(filename, csv) {
  // BOM so Excel reads UTF-8 correctly
  const blob = new Blob(['﻿', csv], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function formatBytes(bytes) {
  if (!Number.isFinite(bytes)) return '';
  const units = ['B', 'KB', 'MB', 'GB'];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value >= 10 || unit === 0 ? Math.round(value) : value.toFixed(1)} ${units[unit]}`;
}

export function plural(count, singular, pluralForm = `${singular}s`) {
  return `${count} ${count === 1 ? singular : pluralForm}`;
}
