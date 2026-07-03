import { test, expect, type Page } from "@playwright/test";
import { sanitizeHtml } from "../helpers/sanitizeHtml";
import { resetGoldenDb } from "../helpers/dbReset";

/**
 * EditTxnDialog snapshot suite.
 *
 * Six variants, one per EditTxnDialog form branch:
 *   1. Buy          — Revolut · AAPL · 2025-08-15 · qty 2 @ $190.50
 *   2. Sell (trade) — Bit2Me · SOL sell leg · 2026-03-20 · qty 2 @ $198.00
 *      (Every sell is a trade-pair leg. Clicking Edit on the
 *       sell leg opens the dual-leg trade dialog — this IS the only sell-context
 *       EditTxnDialog visual state the production app ever produces.)
 *   3. Trade        — Bit2Me · USDC→ETH · 2025-12-01, targeted via the ETH
 *       buy leg (qty 0.135 is row-unique). Verifies dual-chip header.
 *   4. Spend        — Revolut · USDC · 2026-01-08 · "VPS rental"
 *   5. Yield-manual — MyInvestor · MSCI-W · 2026-03-31 (no ActionBanner)
 *   6. Yield-auto-accrual — Revolut Earn · ETH · 2026-04-25 (ActionBanner present)
 *
 * Row-locator strategy (triple-filter per apy-deep-link.spec.ts:58–64):
 *   TxnList renders one <tr> per leg (sell rows show txn_type="sell" via CSS capitalize).
 *   The link icon on trade-pair legs has no inline text; filtering on "Trade" returns 0 rows.
 *   Three disambiguating hasText fragments give a single deterministic <tr> match.
 */

const FIXED_INSTANT = "2026-04-30T12:00:00Z";

async function openEditDialog(page: Page, filters: string[]): Promise<void> {
  // Triple-filter row locator — Badge+span whitespace collapse means single-text
  // filters are insufficient. Three fragments give a deterministic single-row match.
  // TxnList was virtualized (commit 3ac44e3), so match role="row"
  // div rows AND legacy native <tr> so the selector survives both surfaces.
  // Virtualization also means rows below the fold are NOT in the DOM until
  // scrolled near the viewport. Window-scroll until the row materializes.
  const buildRow = () => {
    let r = page.locator('[role="row"], tr');
    for (const f of filters) r = r.filter({ hasText: f });
    return r.first();
  };
  let target = buildRow();
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
    target = buildRow();
  }
  await expect(target).toBeVisible({ timeout: 8_000 });
  await target.getByRole("button", { name: /edit transaction/i }).click();
  // Wait for the dialog to be fully open (data-state="open", not "opening").
  await expect(page.locator('[role="dialog"][data-state="open"]')).toBeVisible({ timeout: 5_000 });
}

async function captureDialogBody(page: Page): Promise<string> {
  // Capture the dialog-body slice only (NOT title/footer).
  const body = page.getByTestId("dialog-body").first();
  await expect(body).toBeVisible({ timeout: 5_000 });
  // Wait for any loading skeletons to disappear. For trade-pair dialogs, the
  // pairedLeg is resolved from the TanStack Query cache asynchronously — the
  // dialog shows Skeleton placeholders until the cache hydrates.
  // Also waits for isLoading=true (initial transaction fetch) to complete.
  await expect(body.locator('[data-slot="skeleton"]')).toHaveCount(0, { timeout: 8_000 });
  // Wait for ALL disabled comboboxes (account + instrument display selects) to
  // show non-empty values. The Currency select is enabled, not disabled, so it
  // doesn't appear in this query. Both account and instrument values come from
  // async TanStack Query fetches; with FLOWFOLIO_NULL_POOL=true on the test stack
  // every request gets a fresh SQLite connection (no stale-inode race), so both
  // queries resolve reliably within ~500ms.
  // Exception: yield-auto-accrual ActionBanner dialog has 0 disabled comboboxes.
  await page.waitForFunction(
    () => {
      const body = document.querySelector('[data-testid="dialog-body"]');
      if (!body) return false;
      const disabledCombos = Array.from(
        body.querySelectorAll('button[role="combobox"][disabled]'),
      );
      if (disabledCombos.length === 0) return true; // no locked selects (e.g. auto-accrual)
      return disabledCombos.every((btn) => {
        const sv = btn.querySelector('[data-slot="select-value"]');
        return sv ? (sv as HTMLElement).innerText.trim() !== "" : false;
      });
    },
    undefined,
    { timeout: 10_000 },
  );
  // Wait for network to be idle — ensures async FX rate lookups and any other
  // background fetches have settled before we capture the dialog body. Without
  // this, the FX rate message (success vs. "could not fetch") is non-deterministic
  // between runs.
  await page.waitForLoadState("networkidle", { timeout: 8_000 });
  const raw = await body.innerHTML();
  return sanitizeHtml(raw);
}

test.describe("EditTxnDialog snapshots", () => {
  test.beforeEach(async ({ page, request }) => {
    await resetGoldenDb();
    // After DB reset, poll the API until transactions are available. This guards
    // against a race condition where SQLAlchemy's connection pool serves the old
    // (pre-reset) DB state via a pooled connection while the new DB file is being
    // opened. Polling via the API request fixture ensures we wait for the server
    // to serve fresh golden data before the browser page loads.
    await expect
      .poll(
        async () => {
          try {
            const response = await request.get("/api/transactions");
            if (!response.ok()) return 0;
            const txns = await response.json() as unknown[];
            return Array.isArray(txns) ? txns.length : 0;
          } catch {
            return 0;
          }
        },
        { intervals: [100, 200, 400], timeout: 5_000 },
      )
      .toBeGreaterThan(0);
    // Navigate after confirming data is available in the API.
    await page.goto("/activity");
    // Wait for the transaction list to load. TxnList shows skeletons (no <tr>) while
    // loading, then either the table or "No transactions yet" (no <tr>). We wait for
    // a known fixture row that is always present after resetGoldenDb():
    // AAPL is always in the golden fixture (2025-05-15 and 2025-08-15 buys).
    // If the page shows "No transactions yet" (SQLAlchemy pool served a stale connection
    // from before the mv-based DB reset), reload once to force a fresh TanStack Query
    // fetch. One reload is always sufficient because resetGoldenDb() completes and the
    // API poll has already confirmed data is visible on a fresh connection.
    // With the virtualized TxnList, the AAPL rows live below the viewport in the
    // showcase-grade fixture (183 rows total, AAPL is mid-2025). Asserting
    // toBeVisible without first scrolling the row into view is racy. We instead
    // wait for at least one row of ANY kind to materialize, which is a sufficient
    // signal that the API responded and TanStack Query hydrated — individual row
    // scrolling happens inside captureRow/openEditDialog.
    const anyRow = page.locator('[role="rowgroup"] [role="row"], table tbody tr').first();
    try {
      await expect(anyRow).toBeVisible({ timeout: 6_000 });
    } catch {
      // Stale render — reload and wait again.
      await page.reload({ waitUntil: "domcontentloaded" });
      await expect(anyRow).toBeVisible({ timeout: 10_000 });
    }
    // Freeze the Date clock for deterministic relative-time rendering, but use
    // setFixedTime() rather than install() so that setTimeout/setInterval timers
    // continue to fire. install() stops all timers, which prevents TanStack Query's
    // internal setTimeout-based scheduling (retry delays, background refetch
    // scheduling) from resolving — causing instruments/accounts queries opened
    // inside dialogs to hang indefinitely.
    await page.clock.setFixedTime(FIXED_INSTANT);
  });

  test("Buy variant: Revolut · AAPL · 2025-08-15", async ({ page }) => {
    await openEditDialog(page, ["AAPL", "Revolut", "190.50"]);
    const cleaned = await captureDialogBody(page);
    expect(cleaned).toMatchSnapshot("edit-buy.html");
  });

  test("Sell variant: SOL sell leg of SOL→USDC trade · Bit2Me · 2026-03-20", async ({ page }) => {
    // There are no standalone sells, every sell is a trade-pair leg.
    // Clicking Edit on the SOL sell leg opens the dual-leg trade dialog in sell-context.
    // Row-uniqueness: SOL has two fixture rows — a 2025-09-10 buy @ $160.00 and this
    // 2026-03-20 sell leg @ $198.00. "198.00" uniquely identifies the sell leg.
    await openEditDialog(page, ["SOL", "Bit2Me", "198.00"]);
    const cleaned = await captureDialogBody(page);
    // Sanity: captured dialog is the trade-edit view — both paired leg symbols present.
    expect(cleaned).toContain("SOL");
    expect(cleaned).toContain("USDC");
    expect(cleaned).toMatchSnapshot("edit-sell.html");
  });

  test("Trade variant: USDC→ETH on Bit2Me · 2025-12-01 (dual-chip header)", async ({ page }) => {
    // Trade pairs render as two separate <tr> rows (one per leg). The Edit button on
    // either leg opens the same dual-leg dialog. We target the ETH buy leg
    // because qty 0.135 is row-unique (the only other ETH row is the 2025-06-05 buy @ qty 0.5).
    // (Prior filter ["USDC","ETH","Bit2Me"] expected
    //  both symbols in one row, impossible per per-leg row layout in TxnList.)
    await openEditDialog(page, ["ETH", "Bit2Me", "0.135"]);
    const cleaned = await captureDialogBody(page);
    // Sanity: dual-chip header content present in body.
    expect(cleaned).toContain("USDC");
    expect(cleaned).toContain("ETH");
    expect(cleaned).toMatchSnapshot("edit-trade.html");
  });

  test("Spend variant: Revolut · USDC · 2026-01-08 (VPS rental)", async ({ page }) => {
    await openEditDialog(page, ["USDC", "Revolut", "VPS rental"]);
    const cleaned = await captureDialogBody(page);
    expect(cleaned).toMatchSnapshot("edit-spend.html");
  });

  test("Yield-manual variant: MyInvestor · MSCI-W · 2026-03-31 (no ActionBanner)", async ({ page }) => {
    await openEditDialog(page, ["MSCI-W", "MyInvestor", "Distribution payment"]);
    const cleaned = await captureDialogBody(page);
    // Inverse sanity: manual yield must NOT show the auto-accrual ActionBanner.
    expect(cleaned).not.toContain("Auto-generated by daily APY accrual");
    expect(cleaned).toMatchSnapshot("edit-yield-manual.html");
  });

  test("Yield-auto-accrual variant: Revolut Earn · ETH · 2026-04-25 (ActionBanner present)", async ({ page }) => {
    await openEditDialog(page, ["ETH", "Revolut Earn", "2.37%"]);
    const cleaned = await captureDialogBody(page);
    // Banner copy verbatim.
    expect(cleaned).toContain("Auto-generated by daily APY accrual");
    expect(cleaned).toMatch(/Edit APY source/);
    expect(cleaned).toMatchSnapshot("edit-yield-auto-accrual.html");
  });
});
