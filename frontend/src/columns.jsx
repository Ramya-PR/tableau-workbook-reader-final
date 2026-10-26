import React from 'react';
import { cellText } from './util.js';

export const KIND_LABELS = {
  calculated: 'Calculated',
  parameter: 'Parameter',
  set: 'Set',
  group: 'Group',
  bin: 'Bin',
  physical: 'Source column',
};
export const KIND_ORDER = ['calculated', 'parameter', 'set', 'group', 'bin', 'physical'];

const MODE_LABELS = {
  extract: 'Extract',
  live: 'Live connection',
  file: 'File',
  published: 'Published datasource',
  parameters: 'Parameters',
  unknown: 'Unknown',
};

function KindBadge({ kind }) {
  return <span className={`badge kind-${kind}`}>{KIND_LABELS[kind] ?? kind}</span>;
}

export const LEVEL_LABELS = { high: 'High', medium: 'Medium', low: 'Low' };
export const FLAG_LABELS = {
  lod: 'LOD expression', table_calc: 'Table calculation', external_script: 'External script (R/Python)', rawsql: 'Raw SQL',
  row_level_security: 'User-based security', spatial: 'Spatial', attr: 'ATTR()', regex: 'Regular expression',
  date_functions: 'Date functions', aggregation: 'Aggregation', simple_calculation: 'Calculated field', parameter: 'Parameter',
  set: 'Set', group: 'Group', bin: 'Bin', custom_sql: 'Custom SQL', published_datasource: 'Published datasource',
  extract: 'Extract', joins: 'Joins or unions', quick_table_calc: 'Quick table calculation',
};
const flagText = (ids) => (ids ?? []).map((id) => FLAG_LABELS[id] ?? id).join(', ');

function LevelBadge({ level }) {
  return level ? <span className={`badge level-${level}`}>{LEVEL_LABELS[level]}</span> : null;
}

const col = (key, label, value, extra = {}) => ({ key, label, value, ...extra });

export const fieldColumns = [
  col('datasource', 'Datasource', (r) => r.datasource),
  col('name', 'Field', (r) => r.name),
  col('kind', 'Type', (r) => KIND_LABELS[r.kind] ?? r.kind, { render: (r) => <KindBadge kind={r.kind} /> }),
  col('datatype', 'Data type', (r) => r.datatype),
  col('role', 'Role', (r) => r.role, {
    render: (r) =>
      r.role ? (
        <span title={r.role_inferred ? 'Not stored in the workbook; inferred from the data type' : undefined}>
          {r.role}
          {r.role_inferred ? '*' : ''}
        </span>
      ) : null,
  }),
  col('table', 'Source table', (r) => r.table),
  col('hidden', 'Hidden', (r) => r.hidden),
  col('formula', 'Formula or default value', (r) => r.formula_resolved ?? (r.value != null ? `default: ${r.value}` : ''), {
    mono: true,
    wrap: true,
    exportValue: (r) => r.formula_resolved ?? (r.value != null ? `default: ${r.value}` : ''),
  }),
  col('worksheets', 'Used in worksheets', (r) => r.worksheets, { wrap: true }),
  col('migration_level', 'Migration effort', (r) => r.migration_level ?? '', { render: (r) => <LevelBadge level={r.migration_level} /> }),
  col('migration_flags', 'Migration notes', (r) => flagText(r.migration_flags), { wrap: true }),
];

export const migrationColumns = [
  col('level', 'Effort', (r) => LEVEL_LABELS[r.level], { render: (r) => <LevelBadge level={r.level} /> }),
  col('scope', 'Item', (r) => (r.scope === 'field' ? 'Field' : r.scope === 'datasource' ? 'Datasource' : 'Worksheet field')),
  col('datasource', 'Datasource', (r) => r.datasource),
  col('name', 'Name', (r) => r.name),
  col('flags', 'What needs attention', (r) => flagText(r.flags), { wrap: true }),
  col('reasons', 'Why', (r) => r.reasons.join(' '), { wrap: true }),
];

export const worksheetColumns = [
  col('name', 'Worksheet', (r) => r.name),
  col('datasources', 'Datasources', (r) => r.datasources, { wrap: true }),
  col('field_count', 'Fields used', (r) => r.field_count),
  col('calculated_fields_used', 'Calculated fields used', (r) => r.calculated_fields_used),
  col('parameters_used', 'Parameters used', (r) => r.parameters_used),
  col('marks', 'Mark types', (r) => r.marks),
  col('rows', 'Rows shelf', (r) => r.rows, { mono: true, wrap: true }),
  col('cols', 'Columns shelf', (r) => r.cols, { mono: true, wrap: true }),
  col('filters', 'Filters', (r) => r.filters, { wrap: true }),
  col('dashboards', 'On dashboards', (r) => r.dashboards, { wrap: true }),
];

export const usageColumns = [
  col('worksheet', 'Worksheet', (r) => r.worksheet),
  col('datasource', 'Datasource', (r) => r.datasource),
  col('field', 'Field', (r) => r.field),
  col('kind', 'Type', (r) => KIND_LABELS[r.kind] ?? r.kind, { render: (r) => <KindBadge kind={r.kind} /> }),
  col('datatype', 'Data type', (r) => r.datatype),
  col('role', 'Role', (r) => r.role),
  col('derivations', 'Aggregation or date part', (r) => r.derivations),
  col('quick_table_calcs', 'Quick table calculation', (r) => r.quick_table_calcs),
  col('formula', 'Formula', (r) => r.formula, { mono: true, wrap: true }),
];

export const dashboardColumns = [
  col('name', 'Dashboard', (r) => r.name),
  col('worksheet_count', 'Worksheets', (r) => r.worksheet_count),
  col('worksheets', 'Worksheet names', (r) => r.worksheets, { wrap: true }),
  col('objects', 'Other objects', (r) => r.objects, { wrap: true }),
];

const sqlSummary = (r) =>
  r.custom_sql.length === 0
    ? ''
    : `${r.custom_sql.length} ${r.custom_sql.length === 1 ? 'query' : 'queries'}: ${r.custom_sql[0].replace(/\s+/g, ' ').slice(0, 120)}${r.custom_sql[0].length > 120 ? '…' : ''}`;

export const datasourceColumns = [
  col('caption', 'Datasource', (r) => r.caption),
  col('mode', 'Connection type', (r) => MODE_LABELS[r.mode] ?? r.mode),
  col('connection_classes', 'Connector', (r) => r.connection_classes),
  col('server', 'Server', (r) => r.server),
  col('dbname', 'Database', (r) => r.dbname),
  col('filename', 'File', (r) => r.filename, { wrap: true }),
  col('tables', 'Tables', (r) => r.tables, { wrap: true }),
  col('custom_sql', 'Custom SQL', sqlSummary, {
    mono: true,
    wrap: true,
    exportValue: (r) => r.custom_sql.join('\n---\n'),
  }),
  col('joins', 'Joins', (r) => r.joins),
  col('unions', 'Unions', (r) => r.unions),
  col('join_details', 'Join details', (r) => r.join_details, { wrap: true }),
  col('has_extract', 'Has extract', (r) => r.has_extract),
  col('data_file', 'Bundled data file', (r) => r.data_file, { wrap: true }),
  col('published_to', 'Published at', (r) => r.published_to, { wrap: true }),
  col('field_count', 'Fields', (r) => r.field_count),
  col('calculated_field_count', 'Calculated fields', (r) => r.calculated_field_count),
];

// cellText is re-exported so the table can filter on exactly what it displays.
export { cellText };
