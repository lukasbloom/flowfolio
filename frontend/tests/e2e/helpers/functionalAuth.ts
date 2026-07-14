import type { Page } from "@playwright/test";
import { expect } from "@playwright/test";

/**
 * Shared login helper for FUNCTIONAL specs running against the mutable dev
 * compose stack (localhost:8083), never the hermetic snapshot stack.
 * Extracted from the login steps duplicated across instruments-nav.spec.ts,
 * apy-deep-link.spec.ts, and reconciliation-polish.spec.ts.
 */

/**
 * Resolves the app password from env. Never hardcode it, this is the
 * real dev-stack APP_PASSWORD, not the hermetic TEST_PASSWORD in auth.ts.
 */
export function requireAppPassword(): string {
  const password = process.env.PW_APP_PASSWORD ?? process.env.APP_PASSWORD ?? "";
  if (!password) {
    throw new Error(
      "PW_APP_PASSWORD (or APP_PASSWORD) env var is not set. " +
        "Export it before running e2e tests: export PW_APP_PASSWORD=<your-password>",
    );
  }
  return password;
}

/**
 * Logs into the app via the UI login form and waits for the post-login
 * redirect to /track.
 */
export async function loginViaUi(page: Page, password: string): Promise<void> {
  await page.goto("/login");
  await page.getByLabel(/password/i).fill(password);
  await page.getByRole("button", { name: /sign in/i }).click();
  await expect(page).toHaveURL(/\/track/, { timeout: 10_000 });
}
