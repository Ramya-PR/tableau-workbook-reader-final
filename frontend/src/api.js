// Thin client for the FastAPI backend. All errors surface as ApiError with a
// stable `code` (the UI switches on it) and a message that is safe to show.

export class ApiError extends Error {
  constructor(code, message, status) {
    super(message);
    this.name = 'ApiError';
    this.code = code;
    this.status = status;
  }
}

async function request(path, options) {
  let response;
  try {
    response = await fetch(path, options);
  } catch {
    throw new ApiError(
      'BACKEND_UNREACHABLE',
      'Cannot reach the backend. Start it with: uvicorn app.main:create_app --factory --port 8000',
      0,
    );
  }

  if (!response.ok) {
    let detail = null;
    try {
      detail = (await response.json()).detail;
    } catch {
      /* not JSON */
    }
    if (detail && typeof detail === 'object' && !Array.isArray(detail) && detail.code) {
      throw new ApiError(detail.code, detail.message, response.status);
    }
    if (Array.isArray(detail) && detail.length) {
      // FastAPI request-validation error
      const first = detail[0];
      throw new ApiError('INVALID_INPUT', `${(first.loc || []).slice(1).join('.') || 'Request'}: ${first.msg}`, response.status);
    }
    throw new ApiError('HTTP_ERROR', `The server answered with HTTP ${response.status}.`, response.status);
  }
  return response.json();
}

const qs = (params) => {
  const search = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') search.set(key, String(value));
  });
  const text = search.toString();
  return text ? `?${text}` : '';
};

export const api = {
  capabilities: () => request('/api/capabilities'),

  listWorkbooks: (source, { q, profile } = {}) =>
    request(`/api/sources/${encodeURIComponent(source)}/workbooks${qs({ q, profile })}`),

  parse: (source, reference) =>
    request('/api/workbooks/parse', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ source, reference }),
    }),

  upload: (file) => {
    const body = new FormData();
    body.append('file', file);
    return request('/api/workbooks/upload', { method: 'POST', body });
  },

  previewTables: (workbookId, file) =>
    request(`/api/workbooks/${workbookId}/preview/tables${qs({ file })}`),

  previewRows: (workbookId, { file, table, offset, limit }) =>
    request(`/api/workbooks/${workbookId}/preview/rows${qs({ file, table, offset, limit })}`),

  // Best effort; `keepalive` lets it finish while the page is closing.
  remove: (workbookId) =>
    fetch(`/api/workbooks/${workbookId}`, { method: 'DELETE', keepalive: true }).catch(() => {}),
};
