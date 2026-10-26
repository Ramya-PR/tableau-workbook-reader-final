import React, { useEffect, useMemo, useState } from 'react';
import { api } from '../api.js';
import Grid from './Grid.jsx';
import { downloadCsv, formatBytes, toCsv } from '../util.js';

const PAGE_SIZES = [25, 50, 100, 250, 500, 1000];

function describeError(error) {
  return error?.message ?? 'Something went wrong while loading rows.';
}

export default function DataPreview({ workbookId, files, fileKind, limits }) {
  const previewable = files.filter((f) => f.previewable);
  const blocked = files.filter((f) => !f.previewable);

  const pageSizes = useMemo(() => {
    const sizes = PAGE_SIZES.filter((n) => n <= limits.max_preview_rows);
    if (!sizes.includes(limits.default_preview_rows)) sizes.push(limits.default_preview_rows);
    return sizes.sort((a, b) => a - b);
  }, [limits]);

  const [file, setFile] = useState(previewable[0]?.path ?? null);
  const [tables, setTables] = useState(null);
  const [table, setTable] = useState(null);
  const [pageSize, setPageSize] = useState(limits.default_preview_rows);
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  // Load the table list whenever the chosen data file changes.
  useEffect(() => {
    if (!file) return undefined;
    let cancelled = false;
    setTables(null);
    setTable(null);
    setData(null);
    setError(null);
    setOffset(0);
    setLoading(true);
    api
      .previewTables(workbookId, file)
      .then((result) => {
        if (cancelled) return;
        setTables(result.tables);
        setTable(result.tables[0]?.name ?? null);
        if (result.tables.length === 0) setLoading(false);
      })
      .catch((e) => {
        if (cancelled) return;
        setError(describeError(e));
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [workbookId, file]);

  // Load one page of rows.
  useEffect(() => {
    if (!file || table === null) return undefined;
    let cancelled = false;
    setLoading(true);
    setError(null);
    api
      .previewRows(workbookId, { file, table, offset, limit: pageSize })
      .then((result) => {
        if (cancelled) return;
        setData(result);
        setLoading(false);
      })
      .catch((e) => {
        if (cancelled) return;
        setError(describeError(e));
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [workbookId, file, table, offset, pageSize]);

  const current = tables?.find((t) => t.name === table);
  const total = current?.row_count ?? 0;

  const columns = useMemo(
    () =>
      (data?.columns ?? []).map((column, index) => ({
        key: `c${index}`,
        label: column.name,
        title: column.type,
        sortable: false,
        value: (row) => row[index],
        render: (row) =>
          row[index] === null || row[index] === undefined ? <span className="null">null</span> : String(row[index]),
      })),
    [data],
  );

  if (previewable.length === 0) {
    return (
      <div className="empty-block">
        <h3>No row data to preview</h3>
        {fileKind === 'twb' ? (
          <p>
            A .twb file contains only the workbook definition, never the data. Upload or download the packaged
            workbook (.twbx) to see rows.
          </p>
        ) : files.length === 0 ? (
          <p>
            This workbook has no bundled data files. Its data sources are most likely live connections, so the data
            stays in the source system.
          </p>
        ) : (
          <p>The data files in this workbook can&rsquo;t be previewed yet:</p>
        )}
        <FileList files={blocked} />
      </div>
    );
  }

  return (
    <div className="preview">
      <div className="preview-controls">
        <label>
          Data file
          <select value={file ?? ''} onChange={(e) => setFile(e.target.value)}>
            {previewable.map((f) => (
              <option key={f.path} value={f.path}>
                {f.path} ({formatBytes(f.size_bytes)})
              </option>
            ))}
          </select>
        </label>
        {tables && tables.length > 1 && (
          <label>
            Table
            <select
              value={table ?? ''}
              onChange={(e) => {
                setTable(e.target.value);
                setOffset(0);
              }}
            >
              {tables.map((t) => (
                <option key={t.name} value={t.name}>
                  {t.name} ({t.row_count.toLocaleString()} rows)
                </option>
              ))}
            </select>
          </label>
        )}
        <label>
          Rows per page
          <select
            value={pageSize}
            onChange={(e) => {
              setPageSize(Number(e.target.value));
              setOffset(0);
            }}
          >
            {pageSizes.map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          className="button quiet"
          disabled={!data || data.rows.length === 0}
          onClick={() => downloadCsv('data-preview-page.csv', toCsv(columns, data.rows))}
        >
          Export this page
        </button>
      </div>

      {error && (
        <p className="inline-error" role="alert">
          {error}
        </p>
      )}

      {data && columns.length > 0 && (
        <>
          <Grid columns={columns} rows={data.rows} caption="Data preview" />
          <div className="pager">
            <span>
              {data.rows.length === 0
                ? 'No rows in this range.'
                : `Rows ${(offset + 1).toLocaleString()} to ${(offset + data.rows.length).toLocaleString()} of ${total.toLocaleString()}`}
            </span>
            <button type="button" className="button quiet" disabled={loading || offset === 0} onClick={() => setOffset(Math.max(0, offset - pageSize))}>
              Previous
            </button>
            <button
              type="button"
              className="button quiet"
              disabled={loading || offset + pageSize >= total}
              onClick={() => setOffset(offset + pageSize)}
            >
              Next
            </button>
          </div>
        </>
      )}
      {loading && !data && <p className="empty">Loading rows&hellip;</p>}
      {tables && tables.length === 0 && <p className="empty">This file contains no tables.</p>}

      {blocked.length > 0 && (
        <details className="more-files">
          <summary>{blocked.length} other data {blocked.length === 1 ? 'file' : 'files'} can&rsquo;t be previewed</summary>
          <FileList files={blocked} />
        </details>
      )}
    </div>
  );
}

function FileList({ files }) {
  if (files.length === 0) return null;
  return (
    <ul className="file-list">
      {files.map((f) => (
        <li key={f.path}>
          <span className="mono">{f.path}</span> <span className="muted">({formatBytes(f.size_bytes)})</span>
          {f.reason && <div className="muted">{f.reason}</div>}
        </li>
      ))}
    </ul>
  );
}
