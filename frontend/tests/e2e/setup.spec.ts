import { test, expect } from "@playwright/test";

/**
 * First-run wizard e2e: proves the bootstrap flow on an UNCLAIMED instance.
 *
 * Asserts the first-run contract cannot silently regress:
 *   1. Any app route on an unclaimed instance lands on /setup with "Set your password"
 *   2. Filling + submitting the form claims the instance and redirects into the app
 *      (lands on /track, authenticated by the issued session cookie)
 *   3. Navigating to /setup AFTER the claim redirects to /login (the wizard is shut)
 *
 * IMPORTANT — this needs an UNCLAIMED instance. The hermetic snapshot stack
 * (compose.test.yml) and the dev stack both pre-seed APP_PASSWORD, so they are
 * ALWAYS claimed and the wizard is skipped there. Run this against a clean-volume
 * boot with NO APP_PASSWORD, e.g.:
 *
 *   docker run -d --name flowfolio-setup -p 8082:8080 \
 *     -e BACKUP_ENCRYPTION_KEY=test-key flowfolio:smoke
 *   PW_BASE_URL=http://localhost:8082 \
 *     npx playwright test setup.spec --project=chromium
 *
 * The spec self-gates: if it finds the instance already claimed (claimed:true),
 * it skips rather than failing, so it is inert on the pre-seeded snapshot/dev
 * stacks. It is NOT a *.snapshot.spec.ts, so the snapshots-chromium project
 * never picks it up; under the chromium project it skips on a claimed stack.
 */

const BASE_URL = process.env.PW_BASE_URL ?? "http://localhost:8080";
// A fresh, sufficiently strong password (backend ClaimRequest min_length=8).
const SETUP_PASSWORD = "setup-pass-1234";

test.describe("first-run setup wizard", () => {
  // This spec mutates global instance state (it claims the admin password), so
  // it must never run against a shared pre-seeded stack. Skip when already claimed.
  test.beforeEach(async ({ request }) => {
    const res = await request.get(`${BASE_URL}/api/setup/status`);
    const body = (await res.json()) as { claimed: boolean };
    test.skip(
      body.claimed,
      "instance already claimed (pre-seeded stack) — wizard e2e needs a clean-volume boot",
    );
  });

  test("unclaimed → /setup, claim → /track, post-claim /setup → /login", async ({
    page,
  }) => {
    // ── 1. Any app route on an unclaimed instance lands on /setup ──────────────
    await page.goto("/track");
    await expect(page).toHaveURL(/\/setup/, { timeout: 10_000 });
    await expect(
      page.getByRole("heading", { name: "Set your password" }),
    ).toBeVisible();

    // ── 2. Fill + submit the form → claims and redirects into the app ──────────
    await page.getByLabel("Password", { exact: true }).fill(SETUP_PASSWORD);
    await page.getByLabel("Confirm password").fill(SETUP_PASSWORD);
    await page.getByRole("button", { name: "Set password" }).click();
    // The claim issues the session cookie, so the user lands authenticated on /track.
    await expect(page).toHaveURL(/\/track/, { timeout: 10_000 });

    // ── 3. Visiting /setup after the claim no longer renders the wizard ────────
    // The middleware redirects a claimed /setup to /login; because the claim also
    // issued a session cookie, /login then bounces an authenticated user on to
    // /track. Either way the wizard is shut — assert we do NOT stay on /setup and
    // never see the "Set your password" heading again.
    await page.goto("/setup");
    await expect(page).not.toHaveURL(/\/setup/, { timeout: 10_000 });
    await expect(
      page.getByRole("heading", { name: "Set your password" }),
    ).toHaveCount(0);
  });
});
