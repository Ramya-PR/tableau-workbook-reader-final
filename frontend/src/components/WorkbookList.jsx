import React from 'react';

export default function WorkbookList({ items, busy, onOpen }) {
  if (items.length === 0) return <p className="muted small">No workbooks matched.</p>;
  return (
    <ul className="workbook-list">
      {items.map((item) => (
        <li key={item.id}>
          <div className="workbook-name">
            <span>{item.name}</span>
            {(item.project || item.updated_at) && (
              <span className="muted small">
                {[item.project, item.updated_at ? item.updated_at.slice(0, 10) : null].filter(Boolean).join(', ')}
              </span>
            )}
          </div>
          <button type="button" className="button quiet" disabled={busy} onClick={() => onOpen(item)}>
            Parse
          </button>
        </li>
      ))}
    </ul>
  );
}
