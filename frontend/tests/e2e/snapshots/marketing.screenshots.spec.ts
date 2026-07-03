import { test, expect, type Page } from "@playwright/test";
import { mkdirSync } from "node:fs";
import path from "node:path";
import { resetGoldenDb } from "../helpers/dbReset";

/**
 * Marketing hero-shot capture. Writes six published PNGs under
 * docs/screenshots/ from the synthetic golden seed only (never
 * the real DB) at the frozen clock 2026-04-30T12:00:00Z (reproducible).
 *
 * These are publishable artifacts, NOT assertion baselines — no toMatchSnapshot.
 * Detail on the wait/stability strategy lives next to captureSurface below.
 */

const FIXED_INSTANT = "2026-04-30T12:00:00Z";

// frontend/tests/e2e/snapshots -> repo root -> docs/screenshots
const SCREENSHOT_DIR = path.resolve(__dirname, "../../../../docs/screenshots");

interface Surface {
  route: string;
  selector: string;
  /** base filename, suffixed with .<viewport>.png */
  name: string;
}

// Routes verified against the page components: the per-holding %-return + TWRR
// comparison view is PerformanceSection on /track (the dashboard), NOT /compare.
// /compare hosts the allocation donuts.
const SURFACES: Surface[] = [
  // Compare/performance — per-holding %-return + TWRR (PerformanceSection on /track)
  { route: "/track", selector: '[data-testid="performance-table"]', name: "compare-performance" },
  // Track/net-worth — net-worth-over-time chart on /track
  { route: "/track", selector: '[data-testid="networth-chart"]', name: "track-networth" },
  // Allocation/concentration — allocation donuts on /compare (first = "By type")
  { route: "/compare", selector: '[data-testid="allocation-pie"]', name: "allocation-concentration" },
];

const VIEWPORTS = [
  { label: "desktop", width: 1440, height: 900 },
  { label: "mobile", width: 390, height: 844 },
] as const;

/**
 * Bring a hero surface into frame and capture it deterministically.
 *
 * 1. networkidle — all API fetches resolved (charts render only after data lands).
 * 2. Wait for the surface's own named data-testid to be visible (up to 15s) —
 *    every surface is awaited via a real selector, never a bare timeout.
 * 3. Scroll it to the top of the viewport so the clip frames the surface.
 * 4. Stability poll — two consecutive identical innerHTML captures means ECharts
 *    has finished its init/animation dance (same poll captureWidget uses).
 * 5. page.screenshot with a clip computed from the surface bounding box, capped
 *    to the viewport so the frame stays opaque and tight on the surface.
 */
async function captureSurface(
  page: Page,
  selector: string,
  fileName: string,
  viewport: { width: number; height: number }
): Promise<void> {
  await page.waitForLoadState("networkidle");
  const surface = page.locator(selector).first();
  await expect(surface).toBeVisible({ timeout: 15_000 });
  await surface.evaluate((el) => el.scrollIntoView({ block: "start" }));

  // Stability poll — ECharts has an internal init dance even after networkidle.
  let prev = "";
  for (let i = 0; i < 6; i++) {
    const html = await surface.innerHTML();
    if (i > 0 && html === prev) break;
    prev = html;
    await page.waitForTimeout(150);
  }

  const box = await surface.boundingBox();
  if (!box) throw new Error(`No bounding box for ${selector}`);
  const x = Math.max(0, box.x);
  const y = Math.max(0, box.y);
  const clip = {
    x,
    y,
    width: Math.min(box.width, viewport.width - x),
    height: Math.min(box.height, viewport.height - y),
  };
  await page.screenshot({ path: path.join(SCREENSHOT_DIR, fileName), clip });
}

test.describe("Marketing hero screenshots", () => {
  test.beforeAll(() => {
    mkdirSync(SCREENSHOT_DIR, { recursive: true });
  });

  test.beforeEach(async ({ page }) => {
    // Synthetic golden seed only, never the real DB.
    await resetGoldenDb();
    // Let the SQLite pool settle on the freshly-replaced DB inode (600ms is
    // comfortably past the observed mv(1) race window — see headline-widgets spec).
    await page.waitForTimeout(600);
    // Frozen clock so the captured surfaces are reproducible.
    await page.clock.install({ time: FIXED_INSTANT });
  });

  for (const vp of VIEWPORTS) {
    test(`hero surfaces @ ${vp.label} (${vp.width}x${vp.height})`, async ({ page }) => {
      await page.setViewportSize({ width: vp.width, height: vp.height });
      for (const surface of SURFACES) {
        await page.goto(surface.route);
        await captureSurface(page, surface.selector, `${surface.name}.${vp.label}.png`, vp);
      }
    });
  }
});
