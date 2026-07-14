import { test as setup } from "@playwright/test";
import { resetGoldenDb } from "./helpers/dbReset";
import { TEST_PASSWORD } from "./helpers/auth";

const STORAGE_STATE = "tests/e2e/.auth/storageState.json";

/**
 * Hermetic-stack setup: resets the golden DB and stores an authenticated
 * storageState. Wired in as a Playwright project dependency (the "setup"
 * project in playwright.config.ts) for the snapshots/marketing projects
 * only, so the functional `chromium` project against the dev stack never
 * triggers the hermetic reset+login.
 */
setup("reset golden db and log in", async ({ page }) => {
  await resetGoldenDb();
  await page.goto("/login");
  await page.getByLabel(/password/i).fill(TEST_PASSWORD);
  await page.getByRole("button", { name: /sign in/i }).click();
  await page.waitForURL(/\/track/);
  await page.context().storageState({ path: STORAGE_STATE });
});
