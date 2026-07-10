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
};
