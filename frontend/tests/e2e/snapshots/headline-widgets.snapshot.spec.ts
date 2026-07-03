import { test, expect, type Page } from "@playwright/test";
import { sanitizeHtml } from "../helpers/sanitizeHtml";
import { resetGoldenDb } from "../helpers/dbReset";

const FIXED_INSTANT = "2026-04-30T12:00:00Z";

/**
 * Capture the innerHTML of a widget identified by `selector`, wait for it to
 * stabilise (ECharts has an internal init dance after the API response lands),
 * sanitize transient IDs, and assert against a stored HTML snapshot.
 *
 * Strategy:
 * 1. Wait for networkidle (all API fetches complete).
 * 2. Wait for the widget element to be visible (up to 15s). This covers
 *    React re-render after TanStack Query resolves.
 * 3. Stability poll: capture innerHTML twice 150ms apart; when they match the
 *    ECharts animation is done. Up to 4 attempts before falling back to latest.
 *    (Mitigates ECharts animation frame non-determinism.)
 */
async function captureWidget(
  page: Page,
  selector: string,
  snapshotName: string
): Promise<void> {
  // Wait for the page to finish loading API data before looking for the widget.
  // The chart container is conditionally rendered only after data arrives; without
  // networkidle the locator arrives too early and times out.
  await page.waitForLoadState("networkidle");
  const widget = page.locator(selector).first();
  await expect(widget).toBeVisible({ timeout: 15_000 });
  // Stability poll — require two consecutive identical captures before snapshotting.
  // ECharts has an internal init dance even after networkidle.
  let prev = "";
  for (let i = 0; i < 4; i++) {
    const html = await widget.innerHTML();
    if (i > 0 && html === prev) {
      const cleaned = sanitizeHtml(html);
      expect(cleaned).toMatchSnapshot(snapshotName);
      return;
    }
    prev = html;
    await page.waitForTimeout(150);
  }
  // Fallback — accept the latest captured value and snapshot it.
  expect(sanitizeHtml(prev)).toMatchSnapshot(snapshotName);
}

test.describe("Headline widget snapshots", () => {
  test.beforeEach(async ({ page }) => {
    await resetGoldenDb();
    // Brief pause to let the SQLite connection pool in the API settle on the
    // newly-replaced DB file. The atomic mv(1) inside test_db_reset.sh replaces
    // the inode at /data/flowfolio.db instantly, but SQLAlchemy's AsyncAdaptedQueuePool
    // may still have a handle to the OLD inode for the first ~500ms after the mv.
    // 600ms is comfortably past the observed race window.
    await page.waitForTimeout(600);
    await page.clock.install({ time: FIXED_INSTANT });
  });

  test("NetWorthChart on /track", async ({ page }) => {
    await page.goto("/track");
    // The NetWorthChart wrapper carries data-testid="networth-chart".
    await captureWidget(page, '[data-testid="networth-chart"]', "networth-chart.html");
  });

  test("AllocationPie on /compare", async ({ page }) => {
    await page.goto("/compare");
    // The AllocationPie wrapper carries data-testid="allocation-pie".
    // The /compare page renders 4 AllocationPie instances; .first() captures
    // the "By type" donut which is the canonical snapshot.
    await captureWidget(page, '[data-testid="allocation-pie"]', "allocation-pie.html");
  });

  test("ContributionBars on /compare", async ({ page }) => {
    await page.goto("/compare");
    // The ContributionBars wrapper carries data-testid="contribution-bars".
    await captureWidget(page, '[data-testid="contribution-bars"]', "contribution-bars.html");
  });
});
