import { test, expect } from "@playwright/test";

/**
 * Navigation regression test: the instrument catalog is a first-class
 * /instruments destination.
 *
 * Asserts the IA restructure cannot silently regress:
 *   1. /instruments is reachable
 *   2. The /instruments page shows the "Add instrument" control (creation entry)
 *   3. The page heading reads "Instruments"
 *
 * Pre-requisites for running:
 *   1. Dev compose stack: docker compose -f compose.yml -f compose.dev.yml up -d
 *   2. PW_APP_PASSWORD env var set to the same value as APP_PASSWORD in .env
 *      (or APP_PASSWORD is exported in the shell running the test)
 *
 * Run: cd frontend && npm run test:e2e -- instruments-nav
 */

// Password is read from env, never hardcoded.
const APP_PASSWORD = process.env.PW_APP_PASSWORD ?? process.env.APP_PASSWORD ?? "";

test.describe("instruments navigation", () => {
  test("/instruments is reachable with its creation control", async ({
    page,
  }) => {
    if (!APP_PASSWORD) {
      throw new Error(
        "PW_APP_PASSWORD (or APP_PASSWORD) env var is not set. " +
          "Export it before running e2e tests: export PW_APP_PASSWORD=<your-password>",
      );
    }

    // ── Authenticate the browser context ──────────────────────────────────────
    await page.goto("/login");
    await page.getByLabel(/password/i).fill(APP_PASSWORD);
    await page.getByRole("button", { name: /sign in/i }).click();
    // Middleware redirects / → /track after login.
    await expect(page).toHaveURL(/\/track/, { timeout: 10_000 });

    // ── 1. /instruments is reachable directly ─────────────────────────────────
    await page.goto("/instruments");
    await expect(page).toHaveURL(/\/instruments/, { timeout: 10_000 });

    // ── 2. The "Add instrument" creation control is visible ───────────────────
    await expect(
      page.getByRole("button", { name: "Add instrument" }).first(),
    ).toBeVisible({ timeout: 10_000 });

    // ── 3. The page heading reads "Instruments" ───────────────────────────────
    await expect(
      page.getByRole("heading", { level: 1, name: "Instruments" }),
    ).toBeVisible();
  });
});
