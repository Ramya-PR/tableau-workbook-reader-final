import React, { useCallback, useEffect, useRef, useState } from 'react';
import { api } from './api.js';
import SourcePanel from './components/SourcePanel.jsx';
import Results from './components/Results.jsx';

const FALLBACK_CODES = new Set(['DOWNLOAD_DISABLED', 'DOWNLOAD_UNAVAILABLE', 'LIST_FAILED', 'REDIRECT_BLOCKED']);

export default function App() {
  const [caps, setCaps] = useState(null);
  const [source, setSource] = useState('public');
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const currentId = useRef(null);

  useEffect(() => {
    api.capabilities().then(setCaps).catch(setError);
  }, []);

  // Delete the server-side copy when the tab closes.
  useEffect(() => {
    const onUnload = () => currentId.current && api.remove(currentId.current);
    window.addEventListener('beforeunload', onUnload);
    return () => window.removeEventListener('beforeunload', onUnload);
  }, []);

  const discard = useCallback(() => {
    if (currentId.current) api.remove(currentId.current);
    currentId.current = null;
    setResult(null);
  }, []);

  const run = useCallback(
    async (task) => {
      setBusy(true);
      setError(null);
      discard();
      try {
        const next = await task();
        currentId.current = next.workbook_id;
        setResult(next);
      } catch (e) {
        setError(e);
      } finally {
        setBusy(false);
      }
    },
    [discard],
  );

  const onParse = (src, reference) => run(() => api.parse(src, reference));
  const onUpload = (file) => run(() => api.upload(file));

  return (
    <div className="shell">
      <aside className="rail">
        <h1>Workbook reader</h1>
        <p className="tagline">See what is inside a Tableau workbook: its fields, formulas, sheets, dashboards and data.</p>
        <SourcePanel
          caps={caps}
          source={source}
          onSourceChange={(s) => {
            setSource(s);
            setError(null);
          }}
          busy={busy}
          onParse={onParse}
          onUpload={onUpload}
          onError={setError}
        />
        {caps && (
          <p className="muted small retention">
            Workbooks are kept on the backend for {caps.limits.session_ttl_minutes} minutes at most, then deleted.
          </p>
        )}
      </aside>

      <main className="main" aria-busy={busy}>
        {error && <Banner error={error} onUseUpload={() => { setSource('upload'); setError(null); }} />}
        {busy && <p className="status" role="status">Reading the workbook&hellip;</p>}
        {!busy && result && caps && <Results key={result.workbook_id} result={result} limits={caps.limits} onClear={discard} />}
        {!busy && !result && !error && (
          <div className="empty-state">
            <h2>No workbook open</h2>
            <p>Choose a workbook on the left. Upload a .twbx to see both the metadata and the data inside it.</p>
          </div>
        )}
      </main>
    </div>
  );
}

function Banner({ error, onUseUpload }) {
  return (
    <div className="banner" role="alert">
      <p>{error.message ?? 'Something went wrong.'}</p>
      {FALLBACK_CODES.has(error.code) && (
        <button type="button" className="button" onClick={onUseUpload}>
          Use Upload instead
        </button>
      )}
    </div>
  );
}
