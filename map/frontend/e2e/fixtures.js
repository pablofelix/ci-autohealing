/**
 * Shared mock data and helpers for all Playwright E2E tests.
 *
 * All API responses match the backend's actual contract — see map/backend/routes.py.
 * Positions are chosen so all nodes fit within a 1280x720 viewport.
 */

// ── Graph ─────────────────────────────────────────────────────────────────────

export const MOCK_NODES = [
  {
    id: 'app-rhoai-v3-5',
    type: 'default',
    data: {
      label: 'rhoai-v3-5',
      nodeType: 'Application',
      description: 'RHOAI v3.5 release application',
      hasGaps: false,
      gaps: [],
    },
    position: { x: 350, y: 20 },
  },
  {
    id: 'comp-odh-dashboard-v3-5',
    type: 'default',
    data: {
      label: 'odh-dashboard-v3-5',
      nodeType: 'Component',
      hasGaps: false,
      gaps: [],
    },
    position: { x: 50, y: 180 },
  },
  {
    id: 'comp-odh-vllm-cpu-v3-5',
    type: 'default',
    data: {
      label: 'odh-vllm-cpu-v3-5',
      nodeType: 'Component',
      hasGaps: true,
      gaps: [{ type: 'missing_image', severity: 'warning', message: 'No container image configured' }],
    },
    position: { x: 350, y: 180 },
  },
  {
    id: 'comp-odh-notebook-v3-5',
    type: 'default',
    data: {
      label: 'odh-notebook-v3-5',
      nodeType: 'Component',
      hasGaps: false,
      gaps: [],
    },
    position: { x: 650, y: 180 },
  },
  {
    id: 'repo-odh-dashboard',
    type: 'default',
    data: {
      label: 'odh-dashboard',
      nodeType: 'Repository',
      description: 'ODH Dashboard GitHub repository',
      hasGaps: false,
      gaps: [],
    },
    position: { x: 50, y: 350 },
  },
  {
    id: 'pipeline-container-build',
    type: 'default',
    data: {
      label: 'container-build',
      nodeType: 'Pipeline',
      hasGaps: false,
      gaps: [],
    },
    position: { x: 350, y: 350 },
  },
  {
    id: 'ec-registry-rhoai-prod',
    type: 'default',
    data: {
      label: 'ec-registry-rhoai-prod',
      nodeType: 'ECPolicy',
      hasGaps: true,
      gaps: [{ type: 'missing_exception', severity: 'error', message: 'Expired policy exception' }],
    },
    position: { x: 650, y: 350 },
  },
];

export const MOCK_EDGES = [
  { id: 'e1', source: 'app-rhoai-v3-5', target: 'comp-odh-dashboard-v3-5', label: 'HAS_COMPONENT' },
  { id: 'e2', source: 'app-rhoai-v3-5', target: 'comp-odh-vllm-cpu-v3-5', label: 'HAS_COMPONENT' },
  { id: 'e3', source: 'app-rhoai-v3-5', target: 'comp-odh-notebook-v3-5', label: 'HAS_COMPONENT' },
  { id: 'e4', source: 'comp-odh-dashboard-v3-5', target: 'repo-odh-dashboard', label: 'SOURCES_FROM' },
  { id: 'e5', source: 'comp-odh-dashboard-v3-5', target: 'pipeline-container-build', label: 'BUILT_BY' },
  { id: 'e6', source: 'pipeline-container-build', target: 'ec-registry-rhoai-prod', label: 'VALIDATED_BY' },
];

export const MOCK_GRAPH = { nodes: MOCK_NODES, edges: MOCK_EDGES };

// ── Node detail ───────────────────────────────────────────────────────────────

export const MOCK_NODE_DETAILS = {
  'comp-odh-dashboard-v3-5': {
    id: 'comp-odh-dashboard-v3-5',
    type: 'Component',
    props: {
      name: 'odh-dashboard-v3-5',
      url: 'https://github.com/opendatahub-io/odh-dashboard',
      description: 'ODH Dashboard component for RHOAI',
      release_branch: 'rhoai-3.5',
    },
    neighbors: [
      { id: 'app-rhoai-v3-5', type: 'Application', name: 'rhoai-v3-5', relationship: 'HAS_COMPONENT', direction: 'incoming' },
      { id: 'repo-odh-dashboard', type: 'Repository', name: 'odh-dashboard', relationship: 'SOURCES_FROM', direction: 'outgoing' },
      { id: 'pipeline-container-build', type: 'Pipeline', name: 'container-build', relationship: 'BUILT_BY', direction: 'outgoing' },
    ],
    gaps: [],
  },
  'comp-odh-vllm-cpu-v3-5': {
    id: 'comp-odh-vllm-cpu-v3-5',
    type: 'Component',
    props: {
      name: 'odh-vllm-cpu-v3-5',
      url: 'https://github.com/vllm-project/vllm',
    },
    neighbors: [
      { id: 'app-rhoai-v3-5', type: 'Application', name: 'rhoai-v3-5', relationship: 'HAS_COMPONENT', direction: 'incoming' },
    ],
    gaps: [{ type: 'missing_image', severity: 'warning', message: 'No container image configured' }],
  },
  'repo-odh-dashboard': {
    id: 'repo-odh-dashboard',
    type: 'Repository',
    props: {
      name: 'odh-dashboard',
      url: 'https://github.com/opendatahub-io/odh-dashboard',
      description: 'ODH Dashboard repository',
    },
    neighbors: [
      { id: 'comp-odh-dashboard-v3-5', type: 'Component', name: 'odh-dashboard-v3-5', relationship: 'SOURCES_FROM', direction: 'incoming' },
    ],
    gaps: [],
  },
  'pipeline-container-build': {
    id: 'pipeline-container-build',
    type: 'Pipeline',
    props: {
      name: 'container-build',
      url: 'https://github.com/konflux-ci/build-definitions/tree/main/pipelines/container-build',
    },
    neighbors: [
      { id: 'comp-odh-dashboard-v3-5', type: 'Component', name: 'odh-dashboard-v3-5', relationship: 'BUILT_BY', direction: 'incoming' },
      { id: 'ec-registry-rhoai-prod', type: 'ECPolicy', name: 'ec-registry-rhoai-prod', relationship: 'VALIDATED_BY', direction: 'outgoing' },
    ],
    gaps: [],
  },
  'ec-registry-rhoai-prod': {
    id: 'ec-registry-rhoai-prod',
    type: 'ECPolicy',
    props: {
      name: 'ec-registry-rhoai-prod',
      url: 'https://console.redhat.com/EnterpriseContractPolicy/ec-registry-rhoai-prod',
    },
    neighbors: [
      { id: 'pipeline-container-build', type: 'Pipeline', name: 'container-build', relationship: 'VALIDATED_BY', direction: 'incoming' },
    ],
    gaps: [{ type: 'missing_exception', severity: 'error', message: 'Expired policy exception' }],
  },
};

// ── Search ────────────────────────────────────────────────────────────────────

export const MOCK_SEARCH_RESULTS = {
  dashboard: {
    results: [
      { id: 'comp-odh-dashboard-v3-5', name: 'odh-dashboard-v3-5', type: 'Component' },
      { id: 'repo-odh-dashboard', name: 'odh-dashboard', type: 'Repository' },
    ],
    count: 2,
    query: 'dashboard',
  },
  nonexistent: { results: [], count: 0, query: 'xyznonexistent999' },
};

// ── Impact analysis ───────────────────────────────────────────────────────────

export const MOCK_IMPACT = {
  'comp-odh-dashboard-v3-5': {
    source: 'comp-odh-dashboard-v3-5',
    direction: 'downstream',
    affected: [
      { id: 'repo-odh-dashboard', type: 'Repository', name: 'odh-dashboard', depth: 1 },
      { id: 'pipeline-container-build', type: 'Pipeline', name: 'container-build', depth: 1 },
      { id: 'ec-registry-rhoai-prod', type: 'ECPolicy', name: 'ec-registry-rhoai-prod', depth: 2 },
    ],
    depth: 2,
  },
};

// ── Stats ─────────────────────────────────────────────────────────────────────

export const MOCK_STATS = {
  nodes: [
    { type: 'Application', count: 1 },
    { type: 'Component', count: 3 },
    { type: 'Repository', count: 1 },
    { type: 'Pipeline', count: 1 },
    { type: 'ECPolicy', count: 1 },
  ],
  edges: [
    { type: 'HAS_COMPONENT', count: 3 },
    { type: 'SOURCES_FROM', count: 1 },
    { type: 'BUILT_BY', count: 1 },
    { type: 'VALIDATED_BY', count: 1 },
  ],
};

// ── Gaps ──────────────────────────────────────────────────────────────────────

export const MOCK_GAPS = {
  gaps: [
    { node_id: 'comp-odh-vllm-cpu-v3-5', type: 'missing_image', severity: 'warning', message: 'No container image configured' },
    { node_id: 'ec-registry-rhoai-prod', type: 'missing_exception', severity: 'error', message: 'Expired policy exception' },
  ],
  count: 2,
  by_type: { missing_image: 1, missing_exception: 1 },
};

// ── Live status ───────────────────────────────────────────────────────────────

export const MOCK_LIVE_STATUS = {
  application: 'rhoai-v3-5',
  nodes: [
    { node_id: 'app-rhoai-v3-5', node_type: 'Application', status: 'degraded', health_score: 50, border_color: '#f59e0b', detail: 'Release: AT_RISK' },
    { node_id: 'comp-odh-dashboard-v3-5', node_type: 'Component', status: 'healthy', health_score: 90, border_color: '#10b981' },
    { node_id: 'comp-odh-vllm-cpu-v3-5', node_type: 'Component', status: 'failing', health_score: 30, border_color: '#ef4444', detail: 'build_error' },
  ],
  activity: [
    { timestamp: '2026-07-10T10:00:00Z', component: 'odh-vllm-cpu-v3-5', event_type: 'build_failure', severity: 'error', message: 'Build failed: dependency_issue' },
    { timestamp: '2026-07-10T09:30:00Z', component: 'odh-dashboard-v3-5', event_type: 'conforma_violation', severity: 'warning', message: 'verify-conforma-lp-rhoai' },
    { timestamp: '2026-07-10T09:00:00Z', component: 'odh-notebook-v3-5', event_type: 'fix_pr', severity: 'pr_merged', message: 'PR #42 merged and verified' },
  ],
  onboarding: [
    {
      node_id: 'comp-odh-dashboard-v3-5', score: 100, overall: 'complete', badge_color: '#10b981',
      checks: {
        repository: { status: 'PASS', detail: 'OK' }, branch: { status: 'PASS', detail: 'OK' },
        container_image: { status: 'PASS', detail: 'OK' }, pac: { status: 'PASS', detail: 'OK' },
        builds: { status: 'PASS', detail: 'OK' }, last_built: { status: 'PASS', detail: 'OK' },
        nudges: { status: 'PASS', detail: 'OK' },
      },
      failing: [], warnings: [], jira_key: '',
    },
    {
      node_id: 'comp-odh-vllm-cpu-v3-5', score: 75, overall: 'partial', badge_color: '#f59e0b',
      checks: {
        repository: { status: 'PASS', detail: 'OK' }, branch: { status: 'PASS', detail: 'OK' },
        container_image: { status: 'PASS', detail: 'OK' },
        pac: { status: 'WARN', detail: 'Missing PaC', fix: 'Create PaC Repository CR' },
        builds: { status: 'PASS', detail: 'OK' }, last_built: { status: 'PASS', detail: 'OK' },
        nudges: { status: 'INFO', detail: 'No nudges' },
      },
      failing: [], warnings: ['pac'], jira_key: 'RHOAI-12345',
    },
    {
      node_id: 'comp-odh-notebook-v3-5', score: 35, overall: 'incomplete', badge_color: '#ef4444',
      checks: {
        repository: { status: 'PASS', detail: 'OK' },
        branch: { status: 'FAIL', detail: 'No branch', fix: 'Set spec.source.git.revision' },
        container_image: { status: 'FAIL', detail: 'No image', fix: 'Set spec.containerImage' },
        pac: { status: 'SKIP', detail: 'N/A' },
        builds: { status: 'FAIL', detail: 'No builds', fix: 'Trigger build' },
        last_built: { status: 'WARN', detail: 'Never built' },
        nudges: { status: 'INFO', detail: 'No nudges' },
      },
      failing: ['branch', 'container_image', 'builds'], warnings: ['last_built'], jira_key: '',
    },
  ],
  ic_available: true,
  last_updated: '2026-07-10T12:00:00Z',
};

// ── Chat ──────────────────────────────────────────────────────────────────────

export const MOCK_CHAT_RESPONSE = {
  response: 'The odh-dashboard component is built by the container-build pipeline and validated by the ec-registry-rhoai-prod policy.',
  model: 'claude-sonnet-4-5-20250929',
};

// ── Path ──────────────────────────────────────────────────────────────────────

export const MOCK_PATH = {
  nodes: [
    { id: 'comp-odh-dashboard-v3-5', type: 'Component', name: 'odh-dashboard-v3-5' },
    { id: 'pipeline-container-build', type: 'Pipeline', name: 'container-build' },
    { id: 'ec-registry-rhoai-prod', type: 'ECPolicy', name: 'ec-registry-rhoai-prod' },
  ],
  edges: [
    { source: 'comp-odh-dashboard-v3-5', target: 'pipeline-container-build', label: 'BUILT_BY' },
    { source: 'pipeline-container-build', target: 'ec-registry-rhoai-prod', label: 'VALIDATED_BY' },
  ],
};

// ── Route mocking ─────────────────────────────────────────────────────────────

/**
 * Register all mock API routes on a Playwright page.
 * Call before page.goto('/').
 */
export async function mockAllAPIs(page, overrides = {}) {
  await page.route('**/api/map/graph', (route) =>
    route.fulfill({ json: overrides.graph ?? MOCK_GRAPH }),
  );

  await page.route('**/api/map/node/*', (route) => {
    const nodeId = route.request().url().split('/api/map/node/')[1]?.split('?')[0];
    const decoded = decodeURIComponent(nodeId || '');
    const detail = MOCK_NODE_DETAILS[decoded] ?? {
      id: decoded, type: 'Unknown', props: { name: decoded }, neighbors: [], gaps: [],
    };
    return route.fulfill({ json: overrides.nodeDetail ?? detail });
  });

  await page.route('**/api/map/search*', (route) => {
    const url = new URL(route.request().url());
    const q = url.searchParams.get('q') || '';
    const typeFilter = url.searchParams.get('type');

    let results = [];
    for (const node of MOCK_NODES) {
      const name = (node.data.label || node.id).toLowerCase();
      if (name.includes(q.toLowerCase())) {
        results.push({ id: node.id, name: node.data.label, type: node.data.nodeType });
      }
    }
    if (typeFilter) {
      results = results.filter((r) => r.type === typeFilter);
    }
    return route.fulfill({ json: { results, count: results.length, query: q } });
  });

  await page.route('**/api/map/impact/*', (route) => {
    const nodeId = route.request().url().split('/api/map/impact/')[1]?.split('?')[0];
    const decoded = decodeURIComponent(nodeId || '');
    const impact = MOCK_IMPACT[decoded];
    if (impact) {
      return route.fulfill({ json: impact });
    }
    return route.fulfill({ status: 404, json: { detail: `Node '${decoded}' not found` } });
  });

  await page.route('**/api/map/stats', (route) =>
    route.fulfill({ json: overrides.stats ?? MOCK_STATS }),
  );

  await page.route('**/api/map/gaps', (route) =>
    route.fulfill({ json: overrides.gaps ?? MOCK_GAPS }),
  );

  await page.route('**/api/map/live-status*', async (route) => {
    await new Promise((r) => setTimeout(r, 500));
    await route.fulfill({ json: overrides.liveStatus ?? MOCK_LIVE_STATUS });
  });

  await page.route('**/api/map/health', (route) =>
    route.fulfill({ json: { status: 'ok', neo4j: 'connected', total_nodes: 7 } }),
  );

  await page.route('**/api/map/chat', async (route) => {
    if (overrides.chatError) {
      return route.fulfill({ status: 500, json: { detail: 'Chat unavailable' } });
    }
    await new Promise((r) => setTimeout(r, 200));
    await route.fulfill({ json: overrides.chatResponse ?? MOCK_CHAT_RESPONSE });
  });

  await page.route('**/api/map/path/**', (route) => {
    if (overrides.pathNotFound) {
      return route.fulfill({ status: 404, json: { detail: 'No path found' } });
    }
    return route.fulfill({ json: overrides.path ?? MOCK_PATH });
  });
}

/**
 * Wait for the graph + live status to fully render (badges appear).
 */
export async function waitForMapReady(page, { expectBadges = true } = {}) {
  const { expect } = await import('@playwright/test');
  await page.waitForSelector('[data-testid="rf__wrapper"]', { timeout: 10_000 });
  if (expectBadges) {
    const badges = page.locator('span[title*="Onboarding:"]');
    await expect(badges).toHaveCount(3, { timeout: 15_000 });
  }
}
