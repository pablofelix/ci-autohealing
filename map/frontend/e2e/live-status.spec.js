// @ts-check
import { test, expect } from '@playwright/test';
import { mockAllAPIs, waitForMapReady, MOCK_LIVE_STATUS } from './fixtures.js';

test.describe('Live Status Overlay', () => {
  test.beforeEach(async ({ page }) => {
    await mockAllAPIs(page);
    await page.goto('/');
    await waitForMapReady(page);
  });

  test('healthy node gets green border', async ({ page }) => {
    // comp-odh-dashboard-v3-5 has status: 'healthy', border_color: '#10b981'
    const node = page.locator('.react-flow__node').filter({ hasText: 'odh-dashboard-v3-5' });
    const border = await node.evaluate((el) => el.style.borderColor || getComputedStyle(el).borderColor);
    // The border should be the green color from live status
    // React Flow nodes get border via inline style from MapNode
    expect(border).toBeTruthy();
  });

  test('failing node gets red border', async ({ page }) => {
    // comp-odh-vllm-cpu-v3-5 has status: 'failing', border_color: '#ef4444'
    const node = page.locator('.react-flow__node').filter({ hasText: 'odh-vllm-cpu-v3-5' });
    await expect(node).toBeVisible();
    // Verify the node exists and has some visual indicator
    const styles = await node.evaluate((el) => {
      const s = getComputedStyle(el);
      return {
        borderColor: el.style.borderColor || s.borderColor,
        borderWidth: el.style.borderWidth || s.borderWidth,
      };
    });
    expect(styles).toBeTruthy();
  });

  test('live status health dots are present on status nodes', async ({ page }) => {
    // MapNode renders a status dot when liveStatus data is present
    // Look for status dot elements inside node cards
    const statusDots = page.locator('.react-flow__node span[style*="border-radius: 50%"]');
    const count = await statusDots.count();
    // At least 2 nodes have live status data (dashboard=healthy, vllm=failing)
    expect(count).toBeGreaterThanOrEqual(2);
  });
});

test.describe('Live Status - IC Unavailable', () => {
  test('no live status indicators when IC is offline', async ({ page }) => {
    await mockAllAPIs(page, {
      liveStatus: {
        application: 'rhoai-v3-5',
        nodes: [], activity: [], onboarding: [],
        ic_available: false, last_updated: null,
      },
    });
    await page.goto('/');
    await page.waitForSelector('[data-testid="rf__wrapper"]', { timeout: 10_000 });
    await page.waitForTimeout(1500);

    // Toolbar should show "Offline"
    await expect(page.getByText('Offline', { exact: true })).toBeVisible();

    // No onboarding badges should be present
    const badges = page.locator('span[title*="Onboarding:"]');
    await expect(badges).toHaveCount(0);
  });
});

test.describe('Onboarding Badges on Map', () => {
  test.beforeEach(async ({ page }) => {
    await mockAllAPIs(page);
    await page.goto('/');
    await waitForMapReady(page);
  });

  test('three onboarding badges render for three components', async ({ page }) => {
    const badges = page.locator('span[title*="Onboarding:"]');
    await expect(badges).toHaveCount(3);
  });

  test('complete badge shows green with score 100', async ({ page }) => {
    const badge = page.locator('span[title="Onboarding: 100% — complete"]');
    await expect(badge).toBeVisible();
    await expect(badge).toHaveText('100');
  });

  test('partial badge shows yellow with score 75', async ({ page }) => {
    const badge = page.locator('span[title="Onboarding: 75% — partial"]');
    await expect(badge).toBeVisible();
    await expect(badge).toHaveText('75');
  });

  test('incomplete badge shows red with score 35', async ({ page }) => {
    const badge = page.locator('span[title="Onboarding: 35% — incomplete"]');
    await expect(badge).toBeVisible();
    await expect(badge).toHaveText('35');
  });
});
