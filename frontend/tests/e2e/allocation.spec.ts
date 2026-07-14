import { test, expect } from "@playwright/test";
import { requireAppPassword, loginViaUi } from "./helpers/functionalAuth";

/**
 * Allocation (donut charts) read-only integration spec. Exercises the
 * /compare analytics page: the four allocation donuts render a canvas each,
 * and switching the breakdown (via the "Exclude closed" toggle, which is
 * the one control on this page that re-fetches all four dimensions'
 * slices at once, by type, risk, account, and banked-vs-non-banked all
 * recompute together) survives without an error surface.
 *
 * Note on "breakdown selector": /compare shows all four dimensions
 * (type/risk/account/banked) simultaneously as separate donuts rather than
 * one control that switches between them. The nearest thing to a
 * "breakdown selector" that actually re-renders all four at once is the
 * ExcludeClosedToggle switch, so this spec exercises that.
 *
 * No cleanup needed. This spec creates nothing. Canvas *contents* are not
 * asserted (that is the visual-snapshot suite's job), only presence and
 * absence of errors.
 *
 * Selectors used (recon'd from the components, see Step 1 of plan 009):
 *   - Analytics heading:      getByRole("heading", { level: 1, name: "Analytics" })  (compare/page.tsx)
 *   - Allocation section:     getByRole("heading", { name: "Allocation" })
 *   - Pie titles:             getByRole("heading", { level: 3, name: "By type" | "By risk" | "By account" | "By banked / non-banked" })  (AllocationPie.tsx <h3>)
 *   - Pie containers:         '[data-testid="allocation-pie"]'  (AllocationPie.tsx, one per dimension)
 *   - Canvas element:         '[data-testid="allocation-pie"] canvas'  (ECharts CanvasRenderer)
 *   - Breakdown toggle:       getByRole("switch", { name: "Exclude closed positions" })  (ExcludeClosedToggle.tsx)
 *   - Error surface check:    getByText(/failed|error/i) at count 0 (sonner toast is this repo's error surface),
 *                             plus the per-pie inline error text "Could not load allocation data." absent
 *
 * Pre-requisites for running:
 *   1. Dev compose stack: docker compose -f compose.multi.yml -f compose.dev.yml up -d
 *   2. PW_APP_PASSWORD env var set to the same value as APP_PASSWORD in .env
 *      (or APP_PASSWORD is exported in the shell running the test)
 *
 * Run: cd frontend && npm run test:e2e -- allocation
 */

const PIE_TITLES = ["By type", "By risk", "By account", "By banked / non-banked"];

test.describe("allocation donuts", () => {
  test("all four donuts render a canvas and survive a breakdown switch", async ({ page }) => {
    const password = requireAppPassword();
    await loginViaUi(page, password);

    await page.goto("/compare");
    await expect(page.getByRole("heading", { level: 1, name: "Analytics" })).toBeVisible({
      timeout: 10_000,
    });
    await expect(page.getByRole("heading", { name: "Allocation" })).toBeVisible();

    // 1. Each donut renders with its title and a canvas element.
    const pies = page.locator('[data-testid="allocation-pie"]');
    await expect(pies).toHaveCount(PIE_TITLES.length, { timeout: 10_000 });
    for (const title of PIE_TITLES) {
      await expect(page.getByRole("heading", { level: 3, name: title })).toBeVisible({
        timeout: 10_000,
      });
    }
    for (let i = 0; i < PIE_TITLES.length; i++) {
      await expect(pies.nth(i).locator("canvas").first()).toBeVisible({ timeout: 10_000 });
    }

    // 2. Breakdown switch: toggling "Exclude closed" re-fetches all four
    // dimensions' slices. Assert no error surface appears afterwards.
    const toggle = page.getByRole("switch", { name: "Exclude closed positions" });
    await expect(toggle).toBeVisible({ timeout: 10_000 });
    await toggle.click();

    await expect(page.getByRole("heading", { name: "Allocation" })).toBeVisible();
    for (const title of PIE_TITLES) {
      await expect(page.getByRole("heading", { level: 3, name: title })).toBeVisible();
    }
    for (let i = 0; i < PIE_TITLES.length; i++) {
      await expect(pies.nth(i).locator("canvas").first()).toBeVisible({ timeout: 10_000 });
    }
    await expect(page.getByText(/failed|error/i)).toHaveCount(0);
    await expect(page.getByText("Could not load allocation data.")).toHaveCount(0);

    // Toggle back so the URL/query state left behind is the page default.
    await toggle.click();
  });
});
