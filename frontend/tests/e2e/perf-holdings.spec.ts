import { test, expect } from "@playwright/test";
import { requireAppPassword, loginViaUi } from "./helpers/functionalAuth";

/**
 * Performance table (holdings) read-only integration spec. Exercises the
 * /track dashboard's Performance section: table renders, the timeframe
 * toggle re-renders it without an error surface, and cells show either a
 * formatted percentage or the documented "insufficient data" placeholder.
 *
 * No cleanup needed. This spec creates nothing.
 *
 * Selectors used (recon'd from the components, see Step 1 of plan 009):
 *   - Dashboard heading:      getByRole("heading", { level: 1, name: "Dashboard" })  (track/page.tsx)
 *   - Performance section:    '[data-testid="performance-table"]'  (PerformanceSection.tsx)
 *   - Section heading:        getByRole("heading", { name: "Performance" })
 *   - Timeframe toggle group: getByRole("group", { name: "Performance timeframe" })  (TimeframeToggle.tsx, role="group")
 *   - Timeframe pill:         getByRole("radio", { name: "3M" | "1M" | "1Y" | "All" })  (ToggleGroupItem -> role="radio")
 *   - Desktop table body row: '[data-testid="performance-table"] table tbody tr'  (PerfTable.tsx shadcn Table)
 *   - Column order (desktop): Instrument, Qty, Avg cost, Current price, % return, Realized, TWRR
 *   - Insufficient-data placeholder: exact text "—" (PercentCell.tsx / TwrrCell.tsx render this for a null value)
 *   - Error surface check:    getByText(/failed|error/i) must stay at count 0 (sonner toast is this repo's error surface)
 *
 * Pre-requisites for running:
 *   1. Dev compose stack: docker compose -f compose.multi.yml -f compose.dev.yml up -d
 *   2. PW_APP_PASSWORD env var set to the same value as APP_PASSWORD in .env
 *      (or APP_PASSWORD is exported in the shell running the test)
 *
 * Run: cd frontend && npm run test:e2e -- perf-holdings
 */

test.describe("performance table", () => {
  test("renders rows, survives a timeframe switch, and cells show a percent or the placeholder", async ({
    page,
  }) => {
    const password = requireAppPassword();
    await loginViaUi(page, password);

    await page.goto("/track");
    await expect(page.getByRole("heading", { level: 1, name: "Dashboard" })).toBeVisible({
      timeout: 10_000,
    });

    const section = page.locator('[data-testid="performance-table"]');
    await expect(section).toBeVisible({ timeout: 10_000 });
    await expect(section.getByRole("heading", { name: "Performance" })).toBeVisible();

    // 1. At least one row renders (the dev DB is seeded).
    const bodyRows = section.locator("table tbody tr");
    await expect(bodyRows.first()).toBeVisible({ timeout: 10_000 });
    expect(await bodyRows.count()).toBeGreaterThan(0);

    // 3 (checked before the timeframe switch too, cheap and gives an
    // early signal if the placeholder contract ever changes).
    const firstRow = bodyRows.first();
    const cells = firstRow.locator("td");
    const pctText = (await cells.nth(4).innerText()).trim();
    expect(pctText === "—" || pctText.includes("%")).toBeTruthy();
    const twrrText = (await cells.nth(6).innerText()).trim();
    expect(twrrText === "—" || twrrText.includes("%")).toBeTruthy();

    // 2. Timeframe switch: click a different preset, assert re-render with
    // no error boundary / toast error.
    const timeframeGroup = page.getByRole("group", { name: "Performance timeframe" });
    await expect(timeframeGroup).toBeVisible();
    await timeframeGroup.getByRole("radio", { name: "3M" }).click();

    // Give the refetch a moment, then assert the section is still healthy:
    // heading intact (no error-boundary unmount), at least one row still
    // rendered, and no "failed"/"error" toast text appeared.
    await expect(section.getByRole("heading", { name: "Performance" })).toBeVisible();
    await expect(bodyRows.first()).toBeVisible({ timeout: 10_000 });
    expect(await bodyRows.count()).toBeGreaterThan(0);
    await expect(page.getByText(/failed|error/i)).toHaveCount(0);

    // Re-check the placeholder contract after the re-render too.
    const cellsAfter = bodyRows.first().locator("td");
    const pctAfter = (await cellsAfter.nth(4).innerText()).trim();
    expect(pctAfter === "—" || pctAfter.includes("%")).toBeTruthy();
    const twrrAfter = (await cellsAfter.nth(6).innerText()).trim();
    expect(twrrAfter === "—" || twrrAfter.includes("%")).toBeTruthy();
  });
});
