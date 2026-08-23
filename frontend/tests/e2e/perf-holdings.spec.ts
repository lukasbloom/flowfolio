import { test, expect, type Locator } from "@playwright/test";
import { loginViaUi } from "./helpers/functionalAuth";
import { expectNoErrorToast } from "./helpers/assertions";

/**
 * Performance table (holdings) read-only integration spec. Exercises the
 * /track dashboard's Performance section: the table renders, the timeframe
 * toggle re-renders it without an error surface, and cells show either a
 * formatted percentage or the "insufficient data" placeholder. Creates
 * nothing, so no cleanup is needed.
 *
 * Pre-requisites for running:
 *   1. Dev compose stack: docker compose -f compose.multi.yml -f compose.dev.yml up -d
 *   2. PW_APP_PASSWORD env var set to the same value as APP_PASSWORD in .env
 *      (or APP_PASSWORD is exported in the shell running the test)
 *
 * Run: cd frontend && npm run test:e2e -- perf-holdings
 */

// The % return (col 4) and TWRR (col 6) cells must show either a formatted
// percentage or the exact "—" insufficient-data placeholder.
async function expectPercentOrPlaceholder(row: Locator): Promise<void> {
  const cells = row.locator("td");
  const pctText = (await cells.nth(4).innerText()).trim();
  expect(pctText === "—" || pctText.includes("%")).toBeTruthy();
  const twrrText = (await cells.nth(6).innerText()).trim();
  expect(twrrText === "—" || twrrText.includes("%")).toBeTruthy();
}

test.describe("performance table", () => {
  test("renders rows, survives a timeframe switch, and cells show a percent or the placeholder", async ({
    page,
  }) => {
    await loginViaUi(page);

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
    await expectPercentOrPlaceholder(bodyRows.first());

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
    await expectNoErrorToast(page);

    // Re-check the placeholder contract after the re-render too.
    await expectPercentOrPlaceholder(bodyRows.first());
  });
});
