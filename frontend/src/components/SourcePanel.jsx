import React, { useRef, useState } from 'react';
import { api } from '../api.js';
import WorkbookList from './WorkbookList.jsx';

const SOURCES = [
  { id: 'public', label: 'Tableau Public' },
  { id: 'server', label: 'Server or Cloud' },
  { id: 'upload', label: 'Upload a file' },
];

export default function SourcePanel({ caps, source, onSourceChange, busy, onParse, onUpload, onError }) {
  const status = (id) => caps?.sources?.[id];

  return (
    <section aria-label="Choose a workbook">
      <div className="source-tabs" role="tablist" aria-label="Workbook source">
        {SOURCES.map((s) => (
          <button
            key={s.id}
            type="button"
            role="tab"
            id={`tab-${s.id}`}
            aria-selected={source === s.id}
            aria-controls={`pane-${s.id}`}
            className="source-tab"
            onClick={() => onSourceChange(s.id)}
          >
            {s.label}
            {status(s.id) && status(s.id).verified === false && <span className="tag">untested</span>}
          </button>
        ))}
      </div>

      <div role="tabpanel" id={`pane-${source}`} aria-labelledby={`tab-${source}`} className="source-pane">
        {source === 'public' && (
          <PublicPane status={status('public')} busy={busy} onParse={onParse} onError={onError} />
        )}
        {source === 'server' && (
          <ServerPane status={status('server')} busy={busy} onParse={onParse} onError={onError} />
        )}
        {source === 'upload' && <UploadPane busy={busy} onUpload={onUpload} limits={caps?.limits} />}
      </div>
    </section>
  );
}

function useListing(source, onError) {
  const [items, setItems] = useState(null);
  const [loading, setLoading] = useState(false);
  const load = async (params) => {
    setLoading(true);
    onError(null);
    try {
      const result = await api.listWorkbooks(source, params);
      setItems(result.workbooks);
    } catch (e) {
      setItems(null);
      onError(e);
    } finally {
      setLoading(false);
    }
  };
  return { items, loading, load };
}

function PublicPane({ status, busy, onParse, onError }) {
  const [reference, setReference] = useState('');
  const [profile, setProfile] = useState(status?.default_profile ?? '');
  const listing = useListing('public', onError);

  return (
    <>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (reference.trim()) onParse('public', reference.trim());
        }}
      >
        <label className="field">
          Workbook URL or name
          <input
            type="text"
            value={reference}
            onChange={(e) => setReference(e.target.value)}
            placeholder="https://public.tableau.com/views/WorkbookName/SheetName"
            spellCheck={false}
            autoComplete="off"
          />
        </label>
        <button type="submit" className="button primary" disabled={busy || !reference.trim()}>
          Parse workbook
        </button>
      </form>

      <hr className="rule" />

      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (profile.trim()) listing.load({ profile: profile.trim() });
        }}
      >
        <label className="field">
          Or list the workbooks on a profile
          <input
            type="text"
            value={profile}
            onChange={(e) => setProfile(e.target.value)}
            placeholder="your.profile.name"
            spellCheck={false}
            autoComplete="off"
          />
        </label>
        <button type="submit" className="button" disabled={busy || listing.loading || !profile.trim()}>
          {listing.loading ? 'Finding workbooks…' : 'Find workbooks'}
        </button>
      </form>
      {listing.items && (
        <WorkbookList items={listing.items} busy={busy} onOpen={(item) => onParse('public', item.id)} />
      )}

      {status?.note && <p className="note">{status.note}</p>}
    </>
  );
}

function ServerPane({ status, busy, onParse, onError }) {
  const [query, setQuery] = useState('');
  const listing = useListing('server', onError);

  if (!status?.available) {
    return (
      <div className="note">
        <p>Tableau Server and Cloud isn&rsquo;t set up yet.</p>
        {status?.reason && <p className="muted">{status.reason}</p>}
        <p className="muted">You can still parse workbooks from Tableau Public or by uploading a file.</p>
      </div>
    );
  }

  return (
    <>
      <p className="muted small">
        Signed in to {status.server}, site {status.site}.
      </p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          listing.load({ q: query.trim() });
        }}
      >
        <label className="field">
          Search by workbook name
          <input type="text" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Leave empty to list all" />
        </label>
        <button type="submit" className="button primary" disabled={busy || listing.loading}>
          {listing.loading ? 'Listing workbooks…' : 'List workbooks'}
        </button>
      </form>
      {listing.items && (
        <WorkbookList items={listing.items} busy={busy} onOpen={(item) => onParse('server', item.id)} />
      )}
      {status.note && <p className="note">{status.note}</p>}
    </>
  );
}

function UploadPane({ busy, onUpload, limits }) {
  const inputRef = useRef(null);
  const [dragging, setDragging] = useState(false);

  const pick = (file) => {
    if (file) onUpload(file);
  };

  return (
    <>
      <div
        className={`dropzone${dragging ? ' dragging' : ''}`}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          pick(e.dataTransfer.files?.[0]);
        }}
      >
        <p>Drop a .twbx or .twb file here</p>
        <button type="button" className="button primary" disabled={busy} onClick={() => inputRef.current?.click()}>
          Choose a file
        </button>
        <input
          ref={inputRef}
          type="file"
          accept=".twb,.twbx"
          className="visually-hidden"
          tabIndex={-1}
          onChange={(e) => {
            pick(e.target.files?.[0]);
            e.target.value = '';
          }}
        />
      </div>
      <p className="muted small">
        A packaged workbook (.twbx) includes the data extract, so you can preview rows.
        {limits ? ` Files up to ${limits.max_file_mb} MB.` : ''}
      </p>
    </>
  );
}
