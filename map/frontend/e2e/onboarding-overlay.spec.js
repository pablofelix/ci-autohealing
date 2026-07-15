// @ts-check
import { test, expect } from '@playwright/test';

/**
 * Mock data matching the backend's LiveStatusService output shape.
 * Covers all three onboarding states: complete, partial, incomplete.
 */
const MOCK_LIVE_STATUS = {
  application: 'rhoai-v3-5',
  nodes: [
    {
      node_id: 'comp-odh-dashboard-v3-5',
      node_type: 'Component',
      status: 'healthy',
      health_score: 90,
      border_color: '#10b981',
    },
    {
      node_id: 'comp-odh-vllm-cpu-v3-5',
      node_type: 'Component',
      status: 'failing',
      health_score: 30,
      border_color: '#ef4444',
    },
  ],
  activity: [],
  onboarding: [
    {
      node_id: 'comp-odh-dashboard-v3-5',
      score: 100,
      overall: 'complete',
      badge_color: '#10b981',
      checks: {
        repository: { status: 'PASS', detail: 'https://github.com/opendatahub-io/odh-dashboard' },
        branch: { status: 'PASS', detail: 'rhoai-3.5' },
        container_image: { status: 'PASS', detail: 'quay.io/rhoai/odh-dashboard' },
        pac: { status: 'PASS', detail: 'PaC Repository CR found' },
        builds: { status: 'PASS', detail: 'Latest build succeeded' },
        last_built: { status: 'PASS', detail: 'Last built commit: abc123' },
        nudges: { status: 'PASS', detail: '3 nudge(s) configured' },
      },
      failing: [],
      warnings: [],
      jira_key: '',
    },
    {
      node_id: 'comp-odh-vllm-cpu-v3-5',
      score: 75,
      overall: 'partial',
      badge_color: '#f59e0b',
      checks: {
        repository: { status: 'PASS', detail: 'https://github.com/vllm-project/vllm' },
        branch: { status: 'PASS', detail: 'rhoai-3.5' },
        container_image: { status: 'PASS', detail: 'quay.io/rhoai/vllm' },
        pac: { status: 'WARN', detail: 'No PaC Repository CR found', fix: 'Create a PaC Repository CR' },
        builds: { status: 'PASS', detail: 'Latest build succeeded' },
        last_built: { status: 'PASS', detail: 'Last built commit: def456' },
        nudges: { status: 'INFO', detail: 'No nudges configured' },
      },
      failing: [],
      warnings: ['pac'],
      jira_key: 'RHOAI-12345',
    },
    {
      node_id: 'comp-odh-notebook-v3-5',
      score: 35,
      overall: 'incomplete',
      badge_color: '#ef4444',
      checks: {
        repository: { status: 'PASS', detail: 'https://github.com/opendatahub-io/notebooks' },
        branch: { status: 'FAIL', detail: 'No branch configured', fix: 'Set spec.source.git.revision' },
        container_image: { status: 'FAIL', detail: 'No container image', fix: 'Set spec.containerImage' },
        pac: { status: 'SKIP', detail: 'No repository URL to match' },
        builds: { status: 'FAIL', detail: 'No builds found', fix: 'Trigger an initial build' },
        last_built: { status: 'WARN', detail: 'No successful build recorded' },
        nudges: { status: 'INFO', detail: 'No nudges configured' },
      },
      failing: ['branch', 'container_image', 'builds'],
      warnings: ['last_built'],
      jira_key: '',
    },
  ],
  ic_available: true,
  last_updated: '2026-07-10T12:00:00Z',
};

const MOCK_GRAPH = {
  nodes: [
    {
      id: 'comp-odh-dashboard-v3-5',
      type: 'default',
      data: {
        label: 'odh-dashboard-v3-5',
        nodeType: 'Component',
        hasGaps: false,
        gaps: [],
      },
      position: { x: 50, y: 100 },
    },
    {
      id: 'comp-odh-vllm-cpu-v3-5',
      type: 'default',
      data: {
        label: 'odh-vllm-cpu-v3-5',
        nodeType: 'Component',
        hasGaps: true,
        gaps: [{ type: 'missing_image', severity: 'warning', message: 'No image' }],
      },
      position: { x: 350, y: 100 },
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
      position: { x: 650, y: 100 },
    },
  ],
  edges: [],
};

const MOCK_NODE_DETAIL = {
  id: 'comp-odh-vllm-cpu-v3-5',
  type: 'Component',
  props: { name: 'odh-vllm-cpu-v3-5', url: 'https://github.com/vllm-project/vllm' },
  neighbors: [],
  gaps: [],
};

/**
 * Intercept all backend API calls with mocked responses.
 * This lets us test the frontend in isolation without Neo4j or IC.
 */
async function mockBackendAPIs(page) {
  await page.route('**/api/map/graph', (route) =>
    route.fulfill({ json: MOCK_GRAPH }),
  );
  await page.route('**/api/map/live-status*', async (route) => {
    // Delay live-status so graph nodes load first — the merge effect
    // in App.jsx only triggers on statusMap/onboardingMap change, so if
    // live status arrives before nodes, it merges against an empty list.
    await new Promise((r) => setTimeout(r, 500));
    await route.fulfill({ json: MOCK_LIVE_STATUS });
  });
  await page.route('**/api/map/node/*', (route) =>
    route.fulfill({ json: MOCK_NODE_DETAIL }),
  );
  await page.route('**/api/map/stats', (route) =>
    route.fulfill({
      json: {
        nodes: [{ type: 'Component', count: 3 }],
        edges: [],
      },
    }),
  );
  await page.route('**/api/map/health', (route) =>
    route.fulfill({
      json: { status: 'ok', neo4j: 'connected', total_nodes: 3 },
    }),
  );
}

// ── Test suite ─────────────────────────────────────────────────────────────

/**
 * Wait for onboarding badges to appear on the map nodes.
 * More reliable than a fixed timeout — waits for React state to propagate.
 */
async function waitForOnboardingBadges(page, expectedCount = 3) {
  await page.waitForSelector('[data-testid="rf__wrapper"]', { timeout: 10_000 });
  const badges = page.locator('span[title*="Onboarding:"]');
  await expect(badges).toHaveCount(expectedCount, { timeout: 15_000 });
}

test.describe('Onboarding Overlay', () => {
  test.beforeEach(async ({ page }) => {
    await mockBackendAPIs(page);
    await page.goto('/');
    await waitForOnboardingBadges(page);
  });

  test('onboarding badges render on component nodes', async ({ page }) => {
    // Look for score badges — they're spans with the score text inside nodes
    const badges = page.locator('span[title*="Onboarding:"]');
    await expect(badges).toHaveCount(3);
  });

  test('complete component shows green badge with score 100', async ({ page }) => {
    const badge = page.locator('span[title="Onboarding: 100% — complete"]');
    await expect(badge).toBeVisible();
    await expect(badge).toHaveText('100');
    // Verify green background
    const bg = await badge.evaluate((el) => getComputedStyle(el).backgroundColor);
    expect(bg).toBe('rgb(16, 185, 129)'); // #10b981
  });

  test('partial component shows yellow badge with score 75', async ({ page }) => {
    const badge = page.locator('span[title="Onboarding: 75% — partial"]');
    await expect(badge).toBeVisible();
    await expect(badge).toHaveText('75');
    const bg = await badge.evaluate((el) => getComputedStyle(el).backgroundColor);
    expect(bg).toBe('rgb(245, 158, 11)'); // #f59e0b
  });

  test('incomplete component shows red badge with score 35', async ({ page }) => {
    const badge = page.locator('span[title="Onboarding: 35% — incomplete"]');
    await expect(badge).toHaveCount(1);
    await expect(badge).toHaveText('35');
    const bg = await badge.evaluate((el) => getComputedStyle(el).backgroundColor);
    expect(bg).toBe('rgb(239, 68, 68)'); // #ef4444
  });

  test('badge stacks below gap indicator when both present', async ({ page }) => {
    // odh-vllm-cpu-v3-5 has both gaps and onboarding
    const badge = page.locator('span[title="Onboarding: 75% — partial"]');
    const topValue = await badge.evaluate((el) => el.style.top);
    // When hasGaps is true, badge top should be 16px (stacked below gap badge at -8px)
    expect(topValue).toBe('16px');
  });

  test('badge positioned at top when no gap indicator', async ({ page }) => {
    // odh-dashboard-v3-5 has no gaps
    const badge = page.locator('span[title="Onboarding: 100% — complete"]');
    await expect(badge).toHaveCount(1);
    const topValue = await badge.evaluate((el) => el.style.top);
    expect(topValue).toBe('-8px');
  });
});

test.describe('Onboarding in Detail Panel', () => {
  test.beforeEach(async ({ page }) => {
    await mockBackendAPIs(page);
    await page.goto('/');
    await waitForOnboardingBadges(page);
  });

  test('clicking a node opens detail panel with onboarding section', async ({ page }) => {
    // Click on a component node
    const node = page.locator('div').filter({ hasText: 'odh-vllm-cpu-v3-5' }).first();
    await node.click();

    // Wait for detail panel to load
    await expect(page.getByText('Node Detail')).toBeVisible();

    // Check onboarding section appears
    await expect(page.getByText('Onboarding (75%)')).toBeVisible();
  });

  test('onboarding section shows progress bar', async ({ page }) => {
    const node = page.locator('div').filter({ hasText: 'odh-vllm-cpu-v3-5' }).first();
    await node.click();
    await expect(page.getByText('Node Detail')).toBeVisible();

    // Progress bar is a div with width based on score
    const progressBar = page.locator('div[style*="width: 75%"]');
    await expect(progressBar).toBeVisible();
  });

  test('onboarding section shows check steps with status icons', async ({ page }) => {
    const node = page.locator('div').filter({ hasText: 'odh-vllm-cpu-v3-5' }).first();
    await node.click();
    await expect(page.getByText('Node Detail')).toBeVisible();

    // Check labels should be visible
    await expect(page.getByText('Repository URL')).toBeVisible();
    await expect(page.getByText('Release Branch')).toBeVisible();
    await expect(page.getByText('PipelinesAsCode')).toBeVisible();
    await expect(page.getByText('Build Status')).toBeVisible();
  });

  test('fix suggestions are shown for failing checks', async ({ page }) => {
    // Use a node with failing checks — override the detail response
    await page.route('**/api/map/node/*', (route) =>
      route.fulfill({
        json: {
          id: 'comp-odh-notebook-v3-5',
          type: 'Component',
          props: { name: 'odh-notebook-v3-5' },
          neighbors: [],
          gaps: [],
        },
      }),
    );

    const node = page.locator('div').filter({ hasText: 'odh-notebook-v3-5' }).first();
    await node.click();
    await expect(page.getByText('Node Detail')).toBeVisible();

    // Fix suggestions should be visible
    await expect(page.getByText(/Fix:.*Set spec\.source\.git\.revision/)).toBeVisible();
  });

  test('Jira link appears when jira_key is present', async ({ page }) => {
    const node = page.locator('div').filter({ hasText: 'odh-vllm-cpu-v3-5' }).first();
    await node.click();
    await expect(page.getByText('Node Detail')).toBeVisible();

    const jiraLink = page.locator('a[href="https://issues.redhat.com/browse/RHOAI-12345"]');
    await expect(jiraLink).toBeVisible();
    await expect(jiraLink).toContainText('RHOAI-12345');
  });

  test('no Jira link when jira_key is empty', async ({ page }) => {
    // Click dashboard node (no jira_key)
    await page.route('**/api/map/node/*', (route) =>
      route.fulfill({
        json: {
          id: 'comp-odh-dashboard-v3-5',
          type: 'Component',
          props: { name: 'odh-dashboard-v3-5' },
          neighbors: [],
          gaps: [],
        },
      }),
    );

    const node = page.locator('div').filter({ hasText: 'odh-dashboard-v3-5' }).first();
    await node.click();
    await expect(page.getByText('Node Detail')).toBeVisible();

    // Should not have any Jira link
    const jiraLinks = page.locator('a[href*="issues.redhat.com"]');
    await expect(jiraLinks).toHaveCount(0);
  });
});

test.describe('IC Unavailable', () => {
  test('no onboarding badges when IC is down', async ({ page }) => {
    // Override live-status to return IC unavailable
    await page.route('**/api/map/graph', (route) =>
      route.fulfill({ json: MOCK_GRAPH }),
    );
    await page.route('**/api/map/live-status*', (route) =>
      route.fulfill({
        json: {
          application: 'rhoai-v3-5',
          nodes: [],
          activity: [],
          onboarding: [],
          ic_available: false,
          last_updated: null,
        },
      }),
    );
    await page.route('**/api/map/stats', (route) =>
      route.fulfill({
        json: { nodes: [{ type: 'Component', count: 3 }], edges: [] },
      }),
    );
    await page.route('**/api/map/health', (route) =>
      route.fulfill({
        json: { status: 'ok', neo4j: 'connected', total_nodes: 3 },
      }),
    );

    await page.goto('/');
    await page.waitForSelector('[data-testid="rf__wrapper"]', { timeout: 10_000 });
    // Wait for the live status fetch to complete (IC unavailable indicator)
    // The "Live" indicator or absence confirms the fetch happened
    await page.waitForTimeout(3000);

    const badges = page.locator('span[title*="Onboarding:"]');
    await expect(badges).toHaveCount(0);
  });
});
