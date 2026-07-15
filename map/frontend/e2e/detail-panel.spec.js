// @ts-check
import { test, expect } from '@playwright/test';
import { mockAllAPIs, waitForMapReady } from './fixtures.js';

/**
 * Click a React Flow node by its label.
 * MapNode renders `<div title="{label}">{label}</div>` inside `.react-flow__node`.
 */
async function clickNode(page, label) {
  const node = page.locator(`.react-flow__node div[title="${label}"]`);
  await node.click();
}

test.describe('Detail Panel', () => {
  test.beforeEach(async ({ page }) => {
    await mockAllAPIs(page);
    await page.goto('/');
    await waitForMapReady(page);
  });

  test('clicking a node opens the detail panel', async ({ page }) => {
    await clickNode(page, 'odh-dashboard-v3-5');
    await expect(page.getByText('Node Detail')).toBeVisible();
  });

  test('detail panel shows node type and name', async ({ page }) => {
    await clickNode(page, 'odh-dashboard-v3-5');
    await expect(page.getByText('Node Detail')).toBeVisible();

    // Type label (uppercase) — rendered in DetailPanel as detail.type
    await expect(page.locator('text=COMPONENT').first()).toBeVisible();
    // Name heading
    await expect(page.getByRole('heading', { name: 'odh-dashboard-v3-5' })).toBeVisible();
  });

  test('detail panel shows primary external link', async ({ page }) => {
    await clickNode(page, 'odh-dashboard-v3-5');
    await expect(page.getByText('Node Detail')).toBeVisible();

    const link = page.locator('[data-testid="primary-link"]');
    await expect(link).toBeVisible();
    await expect(link).toHaveAttribute('href', 'https://github.com/opendatahub-io/odh-dashboard');
    await expect(link).toContainText('Open in GitHub');
  });

  test('detail panel shows properties section', async ({ page }) => {
    await clickNode(page, 'odh-dashboard-v3-5');
    await expect(page.getByText('Node Detail')).toBeVisible();

    await expect(page.getByText('Properties')).toBeVisible();
    await expect(page.getByText('Release Branch').first()).toBeVisible();
    await expect(page.getByText('rhoai-3.5')).toBeVisible();
  });

  test('detail panel shows connections with neighbor count', async ({ page }) => {
    await clickNode(page, 'odh-dashboard-v3-5');
    await expect(page.getByText('Node Detail')).toBeVisible();

    await expect(page.getByText('Connections (3)')).toBeVisible();
  });

  test('close button dismisses the panel', async ({ page }) => {
    await clickNode(page, 'odh-dashboard-v3-5');
    await expect(page.getByText('Node Detail')).toBeVisible();

    // The close button has text "x" in the detail panel header
    await page.locator('button:has-text("x")').first().click();
    await expect(page.getByText('Node Detail')).not.toBeVisible();
  });

  test('clicking a neighbor navigates to that node', async ({ page }) => {
    await clickNode(page, 'odh-dashboard-v3-5');
    await expect(page.getByText('Node Detail')).toBeVisible();

    // Click the SOURCES_FROM text inside the detail panel (not the edge label on the graph)
    const neighborRow = page.locator('span').filter({ hasText: 'SOURCES_FROM' });
    await neighborRow.click({ force: true });

    // Panel should now show the repository detail
    await expect(page.locator('text=REPOSITORY').first()).toBeVisible();
  });

  test('node with gaps shows Issues section', async ({ page }) => {
    await clickNode(page, 'ec-registry-rhoai-prod');
    await expect(page.getByText('Node Detail')).toBeVisible();

    await expect(page.getByText('Issues (1)')).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('Expired policy exception')).toBeVisible({ timeout: 5000 });
  });

  test('resize handle has col-resize cursor', async ({ page }) => {
    await clickNode(page, 'odh-dashboard-v3-5');
    await expect(page.getByText('Node Detail')).toBeVisible();

    const handle = page.locator('[title="Drag to resize"]');
    await expect(handle).toBeVisible();
    const cursor = await handle.evaluate((el) => getComputedStyle(el).cursor);
    expect(cursor).toBe('col-resize');
  });
});

test.describe('Impact Analysis Buttons', () => {
  test.beforeEach(async ({ page }) => {
    await mockAllAPIs(page);
    await page.goto('/');
    await waitForMapReady(page);
  });

  test('downstream and upstream buttons are visible in detail panel', async ({ page }) => {
    await clickNode(page, 'odh-dashboard-v3-5');
    await expect(page.getByText('Node Detail')).toBeVisible();

    await expect(page.getByRole('button', { name: 'Downstream Impact' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Upstream Deps' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Clear' })).toBeVisible();
  });

  test('clicking Downstream Impact highlights affected nodes', async ({ page }) => {
    await clickNode(page, 'odh-dashboard-v3-5');
    await expect(page.getByText('Node Detail')).toBeVisible();

    await page.getByRole('button', { name: 'Downstream Impact' }).click();

    // Wait for impact API response to propagate to node styles
    const dimmedNodes = page.locator('.react-flow__node[style*="opacity: 0.12"]');
    await expect(dimmedNodes.first()).toBeVisible({ timeout: 5000 });
    const dimmedCount = await dimmedNodes.count();
    expect(dimmedCount).toBeGreaterThan(0);
  });

  test('Clear button restores all node opacity', async ({ page }) => {
    await clickNode(page, 'odh-dashboard-v3-5');
    await expect(page.getByText('Node Detail')).toBeVisible();

    await page.getByRole('button', { name: 'Downstream Impact' }).click();
    await page.waitForTimeout(500);
    await page.getByRole('button', { name: 'Clear' }).click();
    await page.waitForTimeout(300);

    const dimmedNodes = page.locator('.react-flow__node[style*="opacity: 0.12"]');
    await expect(dimmedNodes).toHaveCount(0);
  });
});
