// @ts-check
import { test, expect } from '@playwright/test';
import { mockAllAPIs, waitForMapReady, MOCK_GRAPH, MOCK_NODES } from './fixtures.js';

test.describe('Graph Loading', () => {
  test('renders all nodes from the graph API', async ({ page }) => {
    await mockAllAPIs(page);
    await page.goto('/');
    await waitForMapReady(page);

    for (const node of MOCK_NODES) {
      await expect(page.locator('div').filter({ hasText: node.data.label }).first()).toBeVisible();
    }
  });

  test('shows correct number of nodes and edges in stats', async ({ page }) => {
    await mockAllAPIs(page);
    await page.goto('/');
    await waitForMapReady(page);

    await expect(page.getByText('7 nodes, 6 edges')).toBeVisible();
  });

  test('shows node type labels on each card', async ({ page }) => {
    await mockAllAPIs(page);
    await page.goto('/');
    await waitForMapReady(page);

    // MapNode renders nodeType as a label inside the card
    await expect(page.locator('div').filter({ hasText: 'Application' }).first()).toBeVisible();
    await expect(page.locator('div').filter({ hasText: 'Component' }).first()).toBeVisible();
    await expect(page.locator('div').filter({ hasText: 'Repository' }).first()).toBeVisible();
  });

  test('shows gap badge on nodes with hasGaps=true', async ({ page }) => {
    await mockAllAPIs(page);
    await page.goto('/');
    await waitForMapReady(page);

    // odh-vllm has hasGaps=true, ec-registry has hasGaps=true
    const gapBadges = page.locator('span[title*="issue(s) detected"]');
    await expect(gapBadges).toHaveCount(2, { timeout: 5000 });
  });
});

test.describe('Loading State', () => {
  test('shows loading screen while graph is fetching', async ({ page }) => {
    // Delay the graph response so we can see the loading state
    await page.route('**/api/map/graph', async (route) => {
      await new Promise((r) => setTimeout(r, 2000));
      await route.fulfill({ json: MOCK_GRAPH });
    });
    await page.route('**/api/map/stats', (route) =>
      route.fulfill({ json: { nodes: [], edges: [] } }),
    );
    await page.route('**/api/map/health', (route) =>
      route.fulfill({ json: { status: 'ok' } }),
    );
    await page.route('**/api/map/live-status*', (route) =>
      route.fulfill({ json: { nodes: [], activity: [], onboarding: [], ic_available: false } }),
    );

    await page.goto('/');
    await expect(page.getByText('Loading System Map...')).toBeVisible();
    await expect(page.getByText('Connecting to Neo4j')).toBeVisible();
  });
});

test.describe('Error State', () => {
  test('shows connection error when backend is unreachable', async ({ page }) => {
    await page.route('**/api/map/graph', (route) =>
      route.fulfill({ status: 500, json: { detail: 'Neo4j connection refused' } }),
    );
    await page.route('**/api/map/stats', (route) =>
      route.fulfill({ json: { nodes: [], edges: [] } }),
    );
    await page.route('**/api/map/health', (route) =>
      route.fulfill({ json: { status: 'ok' } }),
    );
    await page.route('**/api/map/live-status*', (route) =>
      route.fulfill({ json: { nodes: [], activity: [], onboarding: [], ic_available: false } }),
    );

    await page.goto('/');
    await expect(page.getByText('Connection Error')).toBeVisible({ timeout: 10_000 });
  });

  test('retry button reloads the graph', async ({ page }) => {
    let callCount = 0;
    await page.route('**/api/map/graph', (route) => {
      callCount++;
      if (callCount === 1) {
        return route.fulfill({ status: 500, json: { detail: 'fail' } });
      }
      return route.fulfill({ json: MOCK_GRAPH });
    });
    await page.route('**/api/map/stats', (route) =>
      route.fulfill({ json: { nodes: [{ type: 'Component', count: 3 }], edges: [] } }),
    );
    await page.route('**/api/map/health', (route) =>
      route.fulfill({ json: { status: 'ok' } }),
    );
    await page.route('**/api/map/live-status*', async (route) => {
      await new Promise((r) => setTimeout(r, 500));
      await route.fulfill({ json: { nodes: [], activity: [], onboarding: [], ic_available: false } });
    });

    await page.goto('/');
    await expect(page.getByText('Connection Error')).toBeVisible({ timeout: 10_000 });

    await page.getByRole('button', { name: 'Retry' }).click();
    await page.waitForSelector('[data-testid="rf__wrapper"]', { timeout: 10_000 });
  });
});
