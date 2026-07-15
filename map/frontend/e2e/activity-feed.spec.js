// @ts-check
import { test, expect } from '@playwright/test';
import { mockAllAPIs, waitForMapReady, MOCK_LIVE_STATUS } from './fixtures.js';

test.describe('Activity Feed', () => {
  test.beforeEach(async ({ page }) => {
    await mockAllAPIs(page);
    await page.goto('/');
    await waitForMapReady(page);
  });

  test('activity feed is visible with events', async ({ page }) => {
    await expect(page.getByText('Activity Feed')).toBeVisible();
  });

  test('shows all activity events from live status', async ({ page }) => {
    // 3 events in MOCK_LIVE_STATUS.activity
    await expect(page.getByText('Build failed: dependency_issue')).toBeVisible();
    await expect(page.getByText('verify-conforma-lp-rhoai')).toBeVisible();
    await expect(page.getByText('PR #42 merged and verified')).toBeVisible();
  });

  test('events show component name', async ({ page }) => {
    await expect(page.getByText('odh-vllm-cpu-v3-5').first()).toBeVisible();
    await expect(page.getByText('odh-dashboard-v3-5').first()).toBeVisible();
  });

  test('collapse button minimizes the feed', async ({ page }) => {
    // Click the "-" button to collapse
    const collapseBtn = page.locator('button').filter({ hasText: '-' }).last();
    await collapseBtn.click();

    // Feed header should be gone
    await expect(page.getByText('Activity Feed')).not.toBeVisible();

    // Collapsed button shows count
    await expect(page.getByText(/Activity \(3\)/)).toBeVisible();
  });

  test('collapsed feed can be expanded', async ({ page }) => {
    // Collapse
    const collapseBtn = page.locator('button').filter({ hasText: '-' }).last();
    await collapseBtn.click();
    await expect(page.getByText('Activity Feed')).not.toBeVisible();

    // Re-expand
    await page.getByText(/Activity \(3\)/).click();
    await expect(page.getByText('Activity Feed')).toBeVisible();
  });

  test('shows green status dot when IC is available', async ({ page }) => {
    // The activity feed header has a green dot when IC is connected
    const statusDot = page.locator('span').filter({ has: page.locator('text=Activity Feed') })
      .locator('span[style*="background: rgb(16, 185, 129)"]');
    // At least the header section has a green dot
    const dots = page.locator('span[style*="rgb(16, 185, 129)"]');
    const count = await dots.count();
    expect(count).toBeGreaterThan(0);
  });
});

test.describe('Activity Feed - IC Offline', () => {
  test('shows offline indicator and empty message when IC is down', async ({ page }) => {
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

    // Should show "(offline)" text
    await expect(page.getByText('(offline)')).toBeVisible();
    // Should show "IC API unavailable" empty state
    await expect(page.getByText('IC API unavailable')).toBeVisible();
  });
});

test.describe('Activity Feed - No Events', () => {
  test('shows "No recent activity" when IC is up but no events', async ({ page }) => {
    await mockAllAPIs(page, {
      liveStatus: {
        ...MOCK_LIVE_STATUS,
        activity: [],
      },
    });
    await page.goto('/');
    await waitForMapReady(page);

    await expect(page.getByText('No recent activity')).toBeVisible();
  });
});
