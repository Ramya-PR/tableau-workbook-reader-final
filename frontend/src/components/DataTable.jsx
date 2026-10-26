import React, { useMemo, useState } from 'react';
import Grid from './Grid.jsx';
import { cellText, compareValues, countBy, downloadCsv, plural, toCsv } from '../util.js';

/**
 * Fully client-side table: text filter, optional facet buttons, sorting,
 * pagination and CSV export. Metadata is small enough to hold in the browser;
 * row data (DataPreview) is paginated by the server instead.
 *
 * facet: { key, labels: { value: 'Label' }, order?: [values] }
 */
export default function DataTable({ rows, columns, exportName, facet, empty, pageSize = 50 }) {
  const [query, setQuery] = useState('');
  const [facetValue, setFacetValue] = useState(null);
  const [sort, setSort] = useState(null);
  const [page, setPage] = useState(0);

  const facetCounts = useMemo(() => (facet ? countBy(rows, facet.key) : null), [rows, facet]);

  const filtered = useMemo(() => {
    let out = rows;
    if (facet && facetValue !== null) out = out.filter((row) => row[facet.key] === facetValue);
    const needle = query.trim().toLowerCase();
    if (needle) {
      out = out.filter((row) => columns.some((c) => cellText(c.value(row)).toLowerCase().includes(needle)));
    }
    return out;
  }, [rows, columns, facet, facetValue, query]);

  const sorted = useMemo(() => {
    if (!sort) return filtered;
    const column = columns.find((c) => c.key === sort.key);
    if (!column) return filtered;
    const direction = sort.dir === 'asc' ? 1 : -1;
    return [...filtered].sort((a, b) => direction * compareValues(column.value(a), column.value(b)));
  }, [filtered, columns, sort]);

  const lastPage = Math.max(0, Math.ceil(sorted.length / pageSize) - 1);
  const currentPage = Math.min(page, lastPage);
  const visible = sorted.slice(currentPage * pageSize, (currentPage + 1) * pageSize);

  const onSort = (key) => {
    setPage(0);
    setSort((current) => {
      if (!current || current.key !== key) return { key, dir: 'asc' };
      return current.dir === 'asc' ? { key, dir: 'desc' } : null;
    });
  };

  const facetOrder = facet ? facet.order ?? [...facetCounts.keys()] : [];

  return (
    <div className="table-block">
      <div className="table-tools">
        {facet && facetCounts.size > 0 && (
          <div className="facets" role="group" aria-label="Filter by type">
            <button
              type="button"
              className="facet"
              aria-pressed={facetValue === null}
              onClick={() => {
                setFacetValue(null);
                setPage(0);
              }}
            >
              All <span className="count">{rows.length}</span>
            </button>
            {facetOrder
              .filter((value) => facetCounts.has(value))
              .map((value) => (
                <button
                  key={value}
                  type="button"
                  className={`facet kind-${value}`}
                  aria-pressed={facetValue === value}
                  onClick={() => {
                    setFacetValue(facetValue === value ? null : value);
                    setPage(0);
                  }}
                >
                  {facet.labels?.[value] ?? value} <span className="count">{facetCounts.get(value)}</span>
                </button>
              ))}
          </div>
        )}
        <label className="search">
          <span className="visually-hidden">Filter rows</span>
          <input
            type="search"
            placeholder="Filter rows"
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
              setPage(0);
            }}
          />
        </label>
        <button
          type="button"
          className="button quiet"
          disabled={sorted.length === 0}
          onClick={() => downloadCsv(`${exportName}.csv`, toCsv(columns, sorted))}
        >
          Export CSV
        </button>
      </div>

      {rows.length === 0 ? (
        <p className="empty">{empty}</p>
      ) : sorted.length === 0 ? (
        <p className="empty">Nothing matches this filter.</p>
      ) : (
        <Grid columns={columns} rows={visible} sort={sort} onSort={onSort} caption={exportName} />
      )}

      {sorted.length > pageSize && (
        <div className="pager">
          <span>
            Rows {currentPage * pageSize + 1} to {Math.min((currentPage + 1) * pageSize, sorted.length)} of {sorted.length}
          </span>
          <button type="button" className="button quiet" disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>
            Previous
          </button>
          <button type="button" className="button quiet" disabled={currentPage >= lastPage} onClick={() => setPage(currentPage + 1)}>
            Next
          </button>
        </div>
      )}
      {sorted.length <= pageSize && sorted.length > 0 && (
        <p className="pager-note">{plural(sorted.length, 'row')}</p>
      )}
    </div>
  );
}
