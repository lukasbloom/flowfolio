import { test, expect, type Locator, type Page } from "@playwright/test";
import { loginViaUi } from "./helpers/functionalAuth";
import { expectNoErrorToast } from "./helpers/assertions";

/**
 * Allocation (donut charts) read-only integration spec. Exercises the
 * /compare analytics page: all four allocation donuts render a canvas, and
 * toggling "Exclude closed" (the one control that re-fetches all four
 * dimensions' slices at once) survives without an error surface. Creates
 * nothing, so no cleanup is needed.
 *
 * Pre-requisites for running:
 *   1. Dev compose stack: docker compose -f compose.multi.yml -f compose.dev.yml up -d
 *   2. PW_APP_PASSWORD env var set to the same value as APP_PASSWORD in .env
 *      (or APP_PASSWORD is exported in the shell running the test)
 *
 * Run: cd frontend && npm run test:e2e -- allocation
 */

const PIE_TITLES = ["By type", "By risk", "By account", "By banked / non-banked"];

// Each donut renders with its title and a canvas element.
async function expectDonutsRendered(page: Page, pies: Locator): Promise<void> {
  for (const title of PIE_TITLES) {
    await expect(page.getByRole("heading", { level: 3, name: title })).toBeVisible({
      timeout: 10_000,
    });
  }
  for (let i = 0; i < PIE_TITLES.length; i++) {
    await expect(pies.nth(i).locator("canvas").first()).toBeVisible({ timeout: 10_000 });
  }
}

test.describe("allocation donuts", () => {
  test("all four donuts render a canvas and survive a breakdown switch", async ({ page }) => {
    await loginViaUi(page);

    await page.goto("/compare");
    await expect(page.getByRole("heading", { level: 1, name: "Analytics" })).toBeVisible({
      timeout: 10_000,
    });
    await expect(page.getByRole("heading", { name: "Allocation" })).toBeVisible();

    // 1. Each donut renders with its title and a canvas element.
    const pies = page.locator('[data-testid="allocation-pie"]');
    await expect(pies).toHaveCount(PIE_TITLES.length, { timeout: 10_000 });
    await expectDonutsRendered(page, pies);

    // 2. Breakdown switch: toggling "Exclude closed" re-fetches all four
    // dimensions' slices. Assert no error surface appears afterwards.
    const toggle = page.getByRole("switch", { name: "Exclude closed positions" });
    await expect(toggle).toBeVisible({ timeout: 10_000 });
    await toggle.click();

    await expect(page.getByRole("heading", { name: "Allocation" })).toBeVisible();
    await expectDonutsRendered(page, pies);
    await expectNoErrorToast(page);
    await expect(page.getByText("Could not load allocation data.")).toHaveCount(0);

    // Toggle back so the URL/query state left behind is the page default.
    await toggle.click();
  });
});
