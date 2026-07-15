// @ts-check
import { test, expect } from '@playwright/test';
import { mockAllAPIs, waitForMapReady, MOCK_NODES } from './fixtures.js';

test.describe('Toolbar', () => {
  test.beforeEach(async ({ page }) => {
    await mockAllAPIs(page);
    await page.goto('/');
    await waitForMapReady(page);
  });

  test('shows RHOAI System Map title', async ({ page }) => {
    await expect(page.getByText('RHOAI System Map')).toBeVisible();
  });

  test('shows search input with placeholder', async ({ page }) => {
    const input = page.getByPlaceholder('Search nodes...');
    await expect(input).toBeVisible();
  });

  test('shows filter buttons for all node types', async ({ page }) => {
    const types = ['Application', 'Component', 'Repository', 'Pipeline',
      'TektonTask', 'Workflow', 'Automation', 'ECPolicy'];
    for (const type of types) {
      await expect(page.getByRole('button', { name: type, exact: true })).toBeVisible();
    }
  });

  test('shows gap count badge', async ({ page }) => {
    // MOCK_GAPS has 2 gaps
    await expect(page.getByText('2 issues detected')).toBeVisible();
  });

  test('shows IC status indicator as Live when available', async ({ page }) => {
    await expect(page.getByText('Live')).toBeVisible();
    const indicator = page.locator('span[title="IC API connected — live status active"]');
    await expect(indicator).toBeVisible();
  });

  test('shows IC status as Offline when unavailable', async ({ page }) => {
    await mockAllAPIs(page, {
      liveStatus: {
        application: 'rhoai-v3-5',
        nodes: [], activity: [], onboarding: [],
        ic_available: false, last_updated: null,
      },
    });
    await page.goto('/');
    await page.waitForSelector('[data-testid="rf__wrapper"]', { timeout: 10_000 });
    // Wait for live status fetch
    await page.waitForTimeout(1500);

    await expect(page.getByText('Offline', { exact: true })).toBeVisible();
    const indicator = page.locator('span[title="IC API unavailable"]');
    await expect(indicator).toBeVisible();
  });

  test('shows node/edge stats', async ({ page }) => {
    await expect(page.getByText('7 nodes, 6 edges')).toBeVisible();
  });
});

test.describe('Search', () => {
  test.beforeEach(async ({ page }) => {
    await mockAllAPIs(page);
    await page.goto('/');
    await waitForMapReady(page);
  });

  test('search highlights matching nodes and dims others', async ({ page }) => {
    const input = page.getByPlaceholder('Search nodes...');
    await input.fill('dashboard');
    await page.getByRole('button', { name: 'Search' }).click();

    // Wait for search results to apply to node styles
    await page.waitForTimeout(500);

    // "dashboard" matches 2 nodes (comp-odh-dashboard, repo-odh-dashboard)
    // Non-matching nodes should have opacity < 1
    const dimmedNodes = page.locator('.react-flow__node[style*="opacity: 0.15"]');
    const visibleCount = await dimmedNodes.count();
    // At least some nodes should be dimmed (5 of 7 don't match "dashboard")
    expect(visibleCount).toBeGreaterThan(0);
  });

  test('empty search restores all nodes', async ({ page }) => {
    const input = page.getByPlaceholder('Search nodes...');

    // First search for something
    await input.fill('dashboard');
    await page.getByRole('button', { name: 'Search' }).click();
    await page.waitForTimeout(500);

    // Then clear search
    await input.fill('');
    await page.getByRole('button', { name: 'Search' }).click();
    await page.waitForTimeout(500);

    // All nodes should be visible again (no opacity styling)
    for (const node of MOCK_NODES) {
      await expect(
        page.locator('div').filter({ hasText: node.data.label }).first()
      ).toBeVisible();
    }
  });
});

test.describe('Type Filtering', () => {
  test.beforeEach(async ({ page }) => {
    await mockAllAPIs(page);
    await page.goto('/');
    await waitForMapReady(page);
  });

  test('clicking Component filter highlights component nodes', async ({ page }) => {
    await page.getByRole('button', { name: 'Component', exact: true }).click();
    await page.waitForTimeout(500);

    // Filter should have active styling (blue background)
    const btn = page.getByRole('button', { name: 'Component', exact: true });
    const bg = await btn.evaluate((el) => getComputedStyle(el).backgroundColor);
    expect(bg).toBe('rgb(37, 99, 235)'); // #2563eb
  });

  test('clicking active filter again clears it', async ({ page }) => {
    const btn = page.getByRole('button', { name: 'Component', exact: true });

    // Activate filter
    await btn.click();
    await page.waitForTimeout(200);

    // Deactivate filter
    await btn.click();
    await page.waitForTimeout(200);

    // Button should be back to inactive styling (gray)
    const bg = await btn.evaluate((el) => getComputedStyle(el).backgroundColor);
    expect(bg).toBe('rgb(243, 244, 246)'); // #f3f4f6
  });

  test('switching between filters works', async ({ page }) => {
    // Activate Component filter
    await page.getByRole('button', { name: 'Component', exact: true }).click();
    await page.waitForTimeout(200);

    // Click Pipeline — should deactivate Component and activate Pipeline
    await page.getByRole('button', { name: 'Pipeline', exact: true }).click();
    await page.waitForTimeout(200);

    const compBg = await page.getByRole('button', { name: 'Component', exact: true })
      .evaluate((el) => getComputedStyle(el).backgroundColor);
    const pipeBg = await page.getByRole('button', { name: 'Pipeline', exact: true })
      .evaluate((el) => getComputedStyle(el).backgroundColor);

    expect(compBg).toBe('rgb(243, 244, 246)'); // inactive
    expect(pipeBg).toBe('rgb(37, 99, 235)');   // active
  });
});
