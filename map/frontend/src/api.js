const BASE = '/api/map';

async function fetchJson(path) {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

export const api = {
  graph: () => fetchJson('/graph'),
  nodeDetail: (id) => fetchJson(`/node/${encodeURIComponent(id)}`),
  search: (q, type) => {
    const params = new URLSearchParams({ q });
    if (type) params.set('type', type);
    return fetchJson(`/search?${params}`);
  },
  path: (from, to) => fetchJson(`/path/${encodeURIComponent(from)}/${encodeURIComponent(to)}`),
  stats: () => fetchJson('/stats'),
  gaps: () => fetchJson('/gaps'),
  health: () => fetchJson('/health'),
  liveStatus: (app = 'rhoai-v3-5') => fetchJson(`/live-status?application=${encodeURIComponent(app)}`),
  impact: (nodeId, { maxDepth = 5, direction = 'downstream' } = {}) =>
    fetchJson(`/impact/${encodeURIComponent(nodeId)}?max_depth=${maxDepth}&direction=${direction}`),
  drift: () => fetchJson('/drift'),
  concepts: () => fetchJson('/concepts'),
  chat: (message, nodeId = null) => {
    const body = { message };
    if (nodeId) body.node_id = nodeId;
    return fetch(`${BASE}/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then((res) => {
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      return res.json();
    });
  },
  changes: ({ since, entityId, changeType, limit } = {}) => {
    const params = new URLSearchParams();
    if (since) params.set('since', since);
    if (entityId) params.set('entity_id', entityId);
    if (changeType) params.set('change_type', changeType);
    if (limit) params.set('limit', limit);
    const qs = params.toString();
    return fetchJson(`/changes${qs ? `?${qs}` : ''}`);
  },
};
