import { test, expect, type Page } from "@playwright/test";
import { sanitizeHtml } from "../helpers/sanitizeHtml";
import { resetGoldenDb } from "../helpers/dbReset";

const FIXED_INSTANT = "2026-04-30T12:00:00Z";

async function captureRow(page: Page, filters: string[]): Promise<string> {
  // TxnList was virtualized (commit 3ac44e3). The activity
  // ledger is now a div-based fake table using role="row" / role="cell" instead
  // of native <tr>/<td>. Match either to stay compatible with both surfaces.
  // Virtualization also means rows below the fold are NOT in the DOM until
  // scrolled near the viewport. Use Playwright's `text=` engine with a window
  // scroll loop to bring the matching row into view before asserting.
  const buildLocator = () => {
    let row = page.locator('[role="row"], tr');
    for (const f of filters) row = row.filter({ hasText: f });
    return row.first();
  };
  let target = buildLocator();
  for (let attempt = 0; attempt < 25; attempt++) {
    if ((await target.count()) > 0) {
      try {
        await target.scrollIntoViewIfNeeded({ timeout: 1_000 });
        break;
      } catch {
        // fall through to scroll-and-retry
      }
    }
    await page.evaluate(() => window.scrollBy(0, window.innerHeight * 0.8));
    await page.waitForTimeout(120);
    target = buildLocator();
  }
  await expect(target).toBeVisible({ timeout: 5_000 });
  const raw = await target.innerHTML();
  return sanitizeHtml(raw);
}

test.describe("TxnList row snapshots", () => {
  test.beforeEach(async ({ page }) => {
    await resetGoldenDb();
    // setFixedTime pins Date.now() without intercepting setTimeout/setInterval,
    // which allows TanStack Query's internal fetch timers to run normally.
    // page.clock.install() would freeze all timers and cause intermittent
    // failures when TanStack Query's deferred fetch never fires.
    await page.clock.setFixedTime(FIXED_INSTANT);
    await page.goto("/activity");
    // Wait for the (virtualized div-based) ledger to render at least one row. The
    // TxnList markup now uses role="row" containers — match those plus legacy <tr>
    // so the assertion works under either rendering surface.
    await expect(page.locator('[role="rowgroup"] [role="row"], table tbody tr').first()).toBeVisible({ timeout: 10_000 });
  });

  test("Buy row", async ({ page }) => {
    expect(await captureRow(page, ["AAPL", "Revolut", "190.50"])).toMatchSnapshot("row-buy.html");
  });

  test("Sell row (SOL sell leg of SOL→USDC trade · Bit2Me · 2026-03-20)", async ({ page }) => {
    // The fixture has no standalone sells, every sell is a trade-pair leg.
    // SOL has two rows in the fixture (2025-09-10 buy @ $160.00, 2026-03-20 trade-leg sell @ $198.00);
    // "198.00" disambiguates. The row will include the lucide LinkIcon (paired-trade marker)
    // but NOT the literal text "Trade" — the type cell renders "sell" with a CSS `capitalize` class.
    expect(await captureRow(page, ["SOL", "Bit2Me", "198.00"])).toMatchSnapshot("row-sell.html");
  });

  test("Trade row (ETH buy leg of USDC→ETH trade · Bit2Me · 2025-12-01)", async ({ page }) => {
    // Trade pairs render as TWO separate <tr> rows (one per leg). We capture the ETH buy leg
    // because qty 0.135 is row-unique (the other ETH row is the 2025-06-05 buy at qty 0.5).
    // The row's type cell renders "buy" with capitalize CSS — NOT "Trade". The paired-trade
    // identity is conveyed only by the inline LinkIcon next to the type token.
    // (Prior filter ["USDC", "Bit2Me", "Trade"] never matched
    //  because no row contains the literal text "Trade".)
    expect(await captureRow(page, ["ETH", "Bit2Me", "0.135"])).toMatchSnapshot("row-trade.html");
  });

  test("Spend row", async ({ page }) => {
    expect(await captureRow(page, ["USDC", "Revolut", "VPS rental"])).toMatchSnapshot("row-spend.html");
  });

  test("Yield-manual row (no auto-accrual)", async ({ page }) => {
    const cleaned = await captureRow(page, ["MSCI-W", "MyInvestor", "Distribution payment"]);
    expect(cleaned).not.toContain("auto-accrual");   // sanity — distinguishes the manual branch
    expect(cleaned).toMatchSnapshot("row-yield-manual.html");
  });

  test("Yield-auto-accrual row", async ({ page }) => {
    const cleaned = await captureRow(page, ["ETH", "Revolut Earn", "2.37%"]);
    expect(cleaned).toContain("auto-accrual");        // sanity: the prefix (or badge text) renders
    expect(cleaned).toMatchSnapshot("row-yield-auto-accrual.html");
  });
});
