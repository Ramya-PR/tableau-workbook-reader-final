import React, { useState } from 'react';
import DataTable from './DataTable.jsx';
import DataPreview from './DataPreview.jsx';
import {
  KIND_LABELS,
  KIND_ORDER,
  dashboardColumns,
  datasourceColumns,
  fieldColumns,
  LEVEL_LABELS,
  migrationColumns,
  usageColumns,
  worksheetColumns,
} from '../columns.jsx';

const SOURCE_NAMES = { public: 'Tableau Public', server: 'Tableau Server or Cloud', upload: 'an uploaded file' };

export default function Results({ result, limits, onClear }) {
  const { metadata, data_files: files, warnings } = result;
  const [tab, setTab] = useState('fields');

  const tabs = [
    { id: 'fields', label: 'Fields', count: metadata.fields.length },
    { id: 'worksheets', label: 'Worksheets', count: metadata.worksheets.length },
    { id: 'usage', label: 'Fields by worksheet', count: metadata.worksheet_fields.length },
    { id: 'dashboards', label: 'Dashboards', count: metadata.dashboards.length },
    { id: 'datasources', label: 'Datasources', count: metadata.datasources.length },
    { id: 'migration', label: 'Migration', count: metadata.migration.items.length },
    { id: 'data', label: 'Data preview', count: files.length },
  ];

  const build = metadata.workbook.source_build ? metadata.workbook.source_build.split(' ')[0] : null;
  const stem = result.filename.replace(/\.(twbx?|TWBX?)$/, '');

  return (
    <article className="results">
      <header className="results-head">
        <div>
          <h2>{result.filename}</h2>
          <dl className="facts">
            <div>
              <dt>From</dt>
              <dd>{SOURCE_NAMES[result.source] ?? result.source}</dd>
            </div>
            <div>
              <dt>Format</dt>
              <dd>{result.file_kind === 'twbx' ? 'Packaged workbook (.twbx)' : 'Workbook XML (.twb)'}</dd>
            </div>
            {build && (
              <div>
                <dt>Saved with Tableau</dt>
                <dd>{build}</dd>
              </div>
            )}
            {metadata.workbook.published_to && (
              <div>
                <dt>Published at</dt>
                <dd>{metadata.workbook.published_to}</dd>
              </div>
            )}
          </dl>
        </div>
        <button type="button" className="button quiet" onClick={onClear}>
          Clear and delete the file
        </button>
      </header>

      {warnings.length > 0 && (
        <ul className="warnings" aria-label="Things to know about this workbook">
          {warnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      )}

      <div className="tabs" role="tablist" aria-label="Workbook contents">
        {tabs.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            id={`results-tab-${t.id}`}
            aria-selected={tab === t.id}
            aria-controls="results-panel"
            className="tab"
            onClick={() => setTab(t.id)}
          >
            {t.label} <span className="count">{t.count}</span>
          </button>
        ))}
      </div>

      <div role="tabpanel" id="results-panel" aria-labelledby={`results-tab-${tab}`} className="panel">
        {tab === 'fields' && (
          <DataTable
            rows={metadata.fields}
            columns={fieldColumns}
            exportName={`${stem}-fields`}
            facet={{ key: 'kind', labels: KIND_LABELS, order: KIND_ORDER }}
            empty="No fields were found in this workbook."
          />
        )}
        {tab === 'worksheets' && (
          <DataTable
            rows={metadata.worksheets}
            columns={worksheetColumns}
            exportName={`${stem}-worksheets`}
            empty="This workbook has no worksheets."
          />
        )}
        {tab === 'usage' && (
          <DataTable
            rows={metadata.worksheet_fields}
            columns={usageColumns}
            exportName={`${stem}-fields-by-worksheet`}
            facet={{ key: 'kind', labels: KIND_LABELS, order: KIND_ORDER }}
            empty="No worksheet uses any fields."
          />
        )}
        {tab === 'dashboards' && (
          <DataTable
            rows={metadata.dashboards}
            columns={dashboardColumns}
            exportName={`${stem}-dashboards`}
            empty="This workbook has no dashboards."
          />
        )}
        {tab === 'datasources' && (
          <DataTable
            rows={metadata.datasources}
            columns={datasourceColumns}
            exportName={`${stem}-datasources`}
            empty="This workbook has no datasources."
          />
        )}
        {tab === 'migration' && <MigrationPanel migration={metadata.migration} stem={stem} />}
        {tab === 'data' && (
          <DataPreview workbookId={result.workbook_id} files={files} fileKind={result.file_kind} limits={limits} />
        )}
      </div>
      <p className="footnote">An asterisk after a role means the workbook doesn&rsquo;t store it, so it was inferred from the data type.</p>
    </article>
  );
}

function MigrationPanel({ migration, stem }) {
  const { overall, counts, items } = migration;
  return (
    <>
      <div className="migration-summary">
        <p>
          <strong>Estimated migration effort: {LEVEL_LABELS[overall]}</strong>
        </p>
        <p className="muted small">
          {counts.high} high, {counts.medium} medium and {counts.low} low items. This is a rule-based estimate to help plan the review,
          not an automatic conversion. Overall: high with 3 or more high items, medium with 1 or 2 high items or 5 or more medium items, otherwise low. It looks for constructs that need rewriting in Power BI or Looker.
        </p>
      </div>
      <DataTable
        rows={items}
        columns={migrationColumns}
        exportName={`${stem}-migration`}
        facet={{ key: 'level', labels: LEVEL_LABELS, order: ['high', 'medium', 'low'] }}
        empty="Nothing in this workbook needs special attention."
      />
    </>
  );
}
