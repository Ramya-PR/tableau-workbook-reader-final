import React from 'react';
import { cellText } from '../util.js';

/**
 * Presentational table. Columns: { key, label, title?, value(row), render?(row), mono?, wrap?, sortable? }.
 * Sorting is controlled by the parent via `sort` ({ key, dir }) and `onSort(key)`.
 */
export default function Grid({ columns, rows, sort, onSort, caption }) {
  return (
    <div className="grid-scroll" role="region" aria-label={caption} tabIndex={0}>
      <table className="grid">
        <thead>
          <tr>
            {columns.map((column) => {
              const sorted = sort && sort.key === column.key ? sort.dir : null;
              const sortable = onSort && column.sortable !== false;
              return (
                <th
                  key={column.key}
                  scope="col"
                  title={column.title}
                  aria-sort={sorted === 'asc' ? 'ascending' : sorted === 'desc' ? 'descending' : undefined}
                >
                  {sortable ? (
                    <button type="button" className="sort-button" onClick={() => onSort(column.key)}>
                      {column.label}
                      <span className={`sort-mark ${sorted ?? ''}`} aria-hidden="true" />
                    </button>
                  ) : (
                    column.label
                  )}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={index}>
              {columns.map((column) => {
                const content = column.render ? column.render(row) : cellText(column.value(row));
                const classes = [column.mono ? 'mono' : '', column.wrap ? 'wrap' : ''].filter(Boolean).join(' ');
                return (
                  <td key={column.key} className={classes || undefined}>
                    {content}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
