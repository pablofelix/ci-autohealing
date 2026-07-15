// @ts-check
import { test, expect } from '@playwright/test';
import { mockAllAPIs, waitForMapReady, MOCK_CHAT_RESPONSE } from './fixtures.js';

test.describe('Chat Panel', () => {
  test.beforeEach(async ({ page }) => {
    await mockAllAPIs(page);
    await page.goto('/');
    await waitForMapReady(page);
  });

  test('shows "Ask Map" button when collapsed', async ({ page }) => {
    await expect(page.getByText('Ask Map')).toBeVisible();
  });

  test('clicking "Ask Map" opens the chat panel', async ({ page }) => {
    await page.getByText('Ask Map').click();
    await expect(page.getByText('Map Assistant')).toBeVisible();
  });

  test('shows suggestion buttons when empty', async ({ page }) => {
    await page.getByText('Ask Map').click();
    await expect(page.getByText('Map Assistant')).toBeVisible();
    await expect(page.getByText('Ask about the RHOAI CI/CD infrastructure.')).toBeVisible({ timeout: 5000 });
    await expect(page.getByRole('button', { name: 'Summarize current state' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'What needs attention?' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Are we ready to release?' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Explain Conforma' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'How does nudging work?' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Walk me through a release' })).toBeVisible();
  });

  test('chat input has correct placeholder', async ({ page }) => {
    await page.getByText('Ask Map').click();
    const input = page.getByPlaceholder('Ask about the map...');
    await expect(input).toBeVisible();
  });

  test('send button is disabled when input is empty', async ({ page }) => {
    await page.getByText('Ask Map').click();
    const sendBtn = page.getByRole('button', { name: 'Send' });
    await expect(sendBtn).toBeDisabled();
  });

  test('sending a message shows user bubble and assistant response', async ({ page }) => {
    await page.getByText('Ask Map').click();

    const input = page.getByPlaceholder('Ask about the map...');
    await input.fill('What is the dashboard component?');
    await page.getByRole('button', { name: 'Send' }).click();

    // User message should appear (blue background)
    await expect(page.getByText('What is the dashboard component?')).toBeVisible();

    // "Thinking..." should appear briefly
    // Then assistant response should appear
    await expect(page.getByText(MOCK_CHAT_RESPONSE.response)).toBeVisible({ timeout: 5000 });
  });

  test('input is cleared after sending', async ({ page }) => {
    await page.getByText('Ask Map').click();

    const input = page.getByPlaceholder('Ask about the map...');
    await input.fill('test message');
    await page.getByRole('button', { name: 'Send' }).click();

    await expect(input).toHaveValue('');
  });

  test('shows error message when chat API fails', async ({ page }) => {
    await mockAllAPIs(page, { chatError: true });
    await page.goto('/');
    await waitForMapReady(page);

    await page.getByText('Ask Map').click();
    const input = page.getByPlaceholder('Ask about the map...');
    await input.fill('This will fail');
    await page.getByRole('button', { name: 'Send' }).click();

    // Error message should appear (red styling)
    await expect(page.getByText(/Chat unavailable|500/)).toBeVisible({ timeout: 5000 });
  });

  test('collapse button closes the chat', async ({ page }) => {
    await page.getByText('Ask Map').click();
    await expect(page.getByText('Map Assistant')).toBeVisible();

    // Both ActivityFeed and ChatPanel have "-" buttons.
    // The page has exactly 2 collapse buttons with text "-".
    // ActivityFeed is the first, ChatPanel is the second.
    const collapseBtns = page.locator('button').filter({ hasText: /^-$/ });
    await collapseBtns.nth(1).click();

    await expect(page.getByText('Map Assistant')).not.toBeVisible();
    await expect(page.getByText('Ask Map')).toBeVisible();
  });

  test('shows selected node context when a node is clicked', async ({ page }) => {
    // Click a node using the React Flow node label
    const nodeLabel = page.locator('.react-flow__node div[title="odh-dashboard-v3-5"]');
    await nodeLabel.click();
    await expect(page.getByText('Node Detail')).toBeVisible();

    // Then open chat
    await page.getByText('Ask Map').click();

    // Chat header should show the selected node ID as context
    await expect(page.getByText('comp-odh-dashboard-v3-5').first()).toBeVisible();
    // Placeholder should reference the node
    const input = page.getByPlaceholder(/Ask about comp-odh-dashboard-v3-5/);
    await expect(input).toBeVisible();
  });
});
