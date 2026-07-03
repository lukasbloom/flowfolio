import { test, expect, type Page } from "@playwright/test";
import { sanitizeHtml } from "../helpers/sanitizeHtml";
import { resetGoldenDb } from "../helpers/dbReset";

const FIXED_INSTANT = "2026-04-30T12:00:00Z";

/**
 * Visual regression for the closed-row treatment in AllocationDrill.
 *
 * The golden DB contains at least one closed position: XRP (Bit2Me), seeded by
 * scripts/fixtures/golden_portfolio.py.
 * XRP is 100 units bought then fully sold (net qty = 0, closed).
 * XRP has instrument_type = "crypto", so it appears in the AllocationDrill when
 * the "Crypto" slice is clicked and include_closed=1 (default toggle OFF).
 *
 * When /compare is loaded with the default toggle (excludeClosed OFF),
 * the AllocationDrill on the "Crypto" slice renders the XRP closed row with:
 *   - Badge variant="secondary" reading "Closed"
 *   - text-muted-foreground on the <TableRow>
 *   - em-dash ("—") in the current-price cell (closed price treatment)
 *   - NO line-through
 *
 * This snapshot locks the visual contract so any future regression (e.g., the Badge removed,
 * line-through re-introduced) fails CI immediately.
 *
 * ECharts canvas click strategy:
 * The AllocationPie uses ECharts CanvasRenderer (no SVG DOM paths). Playwright clicks the
 * canvas element at coordinates corresponding to the "Crypto" slice position. The slice
 * occupies approximately 26% of the pie (second slice after ETF). The pie center is at
 * 35% from the left edge of the chart container.
 */

/**
 * Capture the outerHTML of a widget identified by `selector`, wait for it to
 * stabilise (ECharts has an internal init dance after the API response lands),
 * sanitize transient IDs, and assert against a stored HTML snapshot.
 *
 * Adapted from headline-widgets.snapshot.spec.ts.
 * Uses outerHTML (not innerHTML) so the root element's attributes (including
 * data-testid="allocation-drill") appear in the captured baseline.
 * Do not extract to a shared module, extraction is a separate refactor.
 */
async function captureWidget(
  page: Page,
  selector: string,
  snapshotName: string
): Promise<void> {
  await page.waitForLoadState("networkidle");
  const widget = page.locator(selector).first();
  await expect(widget).toBeVisible({ timeout: 15_000 });
  // Stability poll — require two consecutive identical captures before snapshotting.
  // Use outerHTML so the root element's data-testid attribute is included in the baseline.
  let prev = "";
  for (let i = 0; i < 4; i++) {
    const html = await widget.evaluate((el) => el.outerHTML);
    if (i > 0 && html === prev) {
      const cleaned = sanitizeHtml(html);
      expect(cleaned).toMatchSnapshot(snapshotName);
      return;
    }
    prev = html;
    await page.waitForTimeout(150);
  }
  expect(sanitizeHtml(prev)).toMatchSnapshot(snapshotName);
}

/**
 * Click the "Crypto" slice of the "By type" AllocationPie on /compare.
 *
 * Strategy: click the ECharts canvas at a coordinate inside the Crypto slice.
 * The "By type" pie has 5 slices in descending value order:
 *   ETF (44%) → Crypto (26%) → Stock (13%) → Fund (9%) → Stablecoin (7%)
 *
 * ECharts default pie orientation: first slice starts at the top (12 o'clock, 270° math).
 * The pie series has no explicit `startAngle`, so default is 90° (ECharts' convention
 * where 90° = top). Slices are drawn clockwise.
 *
 * Cumulative angle positions (from 12 o'clock, clockwise):
 *   ETF:        0° → 158° (44% of 360°)
 *   Crypto:   158° → 252° (26% of 360°, midpoint at ~205°)
 *   ...
 *
 * The container div is h-72 = 288px tall at desktop breakpoint (filled by canvas).
 * The pie center is at ["35%", "50%"] = (35% of width, 50% of height).
 * The outer radius at "70%" fills 70% of min(containerW, containerH) / 2.
 *
 * Since the mid-radius of the Crypto slice is in the lower-left quadrant from center
 * (roughly 205° clockwise from 12 o'clock = 25° below the leftward horizontal),
 * we click at a position clearly inside the pie ring in that direction.
 *
 * If the first click doesn't open the drill, we try alternative positions.
 */
async function clickCryptoSlice(page: Page): Promise<void> {
  const firstPie = page.locator('[data-testid="allocation-pie"]').first();
  await expect(firstPie).toBeVisible({ timeout: 15_000 });
  await page.waitForLoadState("networkidle");

  const box = await firstPie.boundingBox();
  if (!box) throw new Error("Could not get bounding box of allocation-pie");

  const centerX = box.x + box.width * 0.35;
  const centerY = box.y + box.height * 0.50;

  // Outer radius of the donut ring (70% of the smaller container dimension / 2).
  const containerMinDim = Math.min(box.width, box.height);
  const outerR = (containerMinDim * 0.70) / 2;
  const innerR = (containerMinDim * 0.40) / 2;
  const midR = (outerR + innerR) / 2;

  // The slice layout is fixture-dependent — the showcase-grade golden fixture
  // produces a different "By type" distribution than the prior minimal fixture
  // (stock ~32% / crypto ~25% / etf ~20% / fund ~17% / stablecoin ~6%).
  // Rather than hard-coding the Crypto midpoint angle (which moves whenever the
  // fixture shifts), sweep across plausible clockwise angles and confirm via the
  // drill header which slice we actually opened. Retry until we land on Crypto.
  const drill = page.locator('[data-testid="allocation-drill"]');
  // AllocationDrill is now a DialogContent slot. The slice
  // label lives in DialogTitle ("Holdings — {sliceLabel}") which sits in
  // DialogHeader as a sibling of the testid wrapper, not inside it. Scope to
  // `page` and match the visible DialogTitle text directly. Also wait for the
  // closing animation to settle before retrying clicks (Dialog overlay
  // intercepts pointer events during the ~200ms close transition).
  const dialogTitle = page.getByRole("heading", { name: /^Holdings — /i });

  // Candidate angles to try, ordered by where Crypto is most likely to sit given
  // any reasonable retail-investor distribution (stock-first ECharts ordering puts
  // Crypto in the 100°-200° band more often than not).
  const candidateAngles = [160, 180, 200, 220, 140, 120, 240, 260, 80, 40, 300, 340];

  for (const angleDeg of candidateAngles) {
    const angleRad = ((angleDeg - 90) * Math.PI) / 180;
    const clickX = centerX + midR * Math.cos(angleRad);
    const clickY = centerY + midR * Math.sin(angleRad);
    await page.mouse.click(clickX, clickY);
    await page.waitForTimeout(250);
    if (await drill.isVisible().catch(() => false)) {
      const titleText = (await dialogTitle.textContent().catch(() => "")) ?? "";
      if (/crypto/i.test(titleText)) return;
      // Wrong slice — close the drill and try the next angle.
      // The close affordance is Radix's built-in X with sr-only "Close" label.
      const close = page.getByRole("button", { name: /^close$/i });
      if (await close.isVisible().catch(() => false)) {
        await close.click();
        // Wait for the dialog to fully detach — without this, the ~200ms close
        // animation leaves the overlay catching the next pie click.
        await drill.waitFor({ state: "hidden", timeout: 1500 }).catch(() => {});
      }
    }
  }

  throw new Error(
    "clickCryptoSlice: exhausted candidate angles without landing on the Crypto slice",
  );
}

test.describe("Compare closed-row visual regression", () => {
  test.beforeEach(async ({ page }) => {
    await resetGoldenDb();
    // Brief pause to let the SQLite connection pool in the API settle on the
    // newly-replaced DB file (same pattern as headline-widgets.snapshot.spec.ts).
    await page.waitForTimeout(600);
    await page.clock.install({ time: FIXED_INSTANT });
  });

  test("AllocationDrill renders closed row with muted text + Closed pill + em-dash price", async ({ page }) => {
    await page.goto("/compare");

    // Click the Crypto slice of the first AllocationPie (By type).
    // The Crypto slice contains XRP (closed) in addition to open crypto holdings.
    await clickCryptoSlice(page);

    // Wait for AllocationDrill to appear via its data-testid.
    const drill = page.locator('[data-testid="allocation-drill"]');
    await expect(drill).toBeVisible({ timeout: 8_000 });

    // Wait for the PerfTable inside the drill to load.
    // PerfTable shows a loading skeleton while fetching; wait for it to disappear.
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(300);

    // Capture sanitized drill HTML to baseline.
    await captureWidget(page, '[data-testid="allocation-drill"]', "allocation-drill-closed.html");

    // Sanity assertions on the live HTML.
    // These run in addition to the snapshot match — they catch regressions even if
    // the baseline is force-updated without scrutiny.
    const drillHtml = await drill.innerHTML();
    // Badge "Closed" must appear.
    expect(drillHtml).toContain("Closed");
    // No line-through styling on closed rows.
    expect(drillHtml).not.toContain("line-through");
  });
});
