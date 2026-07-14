import { test, expect } from "@playwright/test";

/**
 * Demo-mode e2e: proves the three
 * visitor-facing demo behaviours cannot silently regress.
 *
 *   1. The persistent demo banner is visible and has NO close/dismiss control
 *      (non-dismissible by construction via ActionBanner).
 *   2. The Settings page shows no "Software updates" panel: the UI
 *      half. The enforcing control is the 403 on the apply API.
 *   3. Visiting /login lands in an authenticated session at /track via the
 *      credential-free auto-route, never the password form.
 *
 * IMPORTANT: this needs a running DEMO stack (compose.demo.yml),
 * which sets DEMO_MODE=true. The dev / hermetic-test stacks are NOT demo, so
 * the spec self-gates: if /api/config reports demo:false it skips rather than
 * failing, staying inert on non-demo stacks. Bring up the demo stack first:
 *
 *   docker compose -f compose.yml -f compose.demo.yml up -d
 *   PW_BASE_URL=http://localhost:8082 \
 *     npx playwright test tests/e2e/demo.spec.ts --project=chromium
 *
 * It is NOT a *.snapshot.spec.ts, so the snapshots-chromium project never picks
 * it up; under the chromium project it skips on a non-demo stack. It runs during
 * UAT because it requires the demo stack.
 */

const BASE_URL = process.env.PW_BASE_URL ?? "http://localhost:8082";

test.describe("public demo mode", () => {
  // This spec asserts demo-only surfaces, so it must only run against a demo
  // stack. Skip when /api/config reports the instance is not in demo mode,
  // AND skip (rather than throw) when the target is unreachable or returns
  // something that isn't the expected JSON. Both mean "wrong stack", not a
  // genuine assertion failure.
  test.beforeEach(async ({ request }) => {
    let demo = false;
    try {
      const res = await request.get(`${BASE_URL}/api/config`, { timeout: 5_000 });
      const body = (await res.json()) as { demo?: boolean };
      demo = body.demo === true;
    } catch {
      demo = false;
    }
    test.skip(
      !demo,
      "instance is not in demo mode, unreachable, or a different stack entirely, demo e2e needs the compose.demo.yml stack",
    );
  });

  test("credential-free /login auto-route lands authenticated on /track", async ({
    page,
  }) => {
    // In demo the password form is skipped, the middleware
    // bounces the visitor into /api/auth/demo-login, which mints the shared
    // session and redirects to /track.
    await page.goto("/login");
    await expect(page).toHaveURL(/\/track/, { timeout: 10_000 });
    // The password form must never be shown in demo.
    await expect(page.getByLabel("Password", { exact: true })).toHaveCount(0);
  });

  test("persistent demo banner is visible and non-dismissible", async ({
    page,
  }) => {
    // Enter via the auto-route so we land authenticated inside the app shell
    // where the app-wide banner is mounted.
    await page.goto("/login");
    await expect(page).toHaveURL(/\/track/, { timeout: 10_000 });

    // The banner frames the synthetic resetting demo and is
    // mounted app-wide. ActionBanner uses role="status".
    const banner = page
      .getByRole("status")
      .filter({ hasText: /demo/i })
      .first();
    await expect(banner).toBeVisible({ timeout: 10_000 });

    // It MUST carry no close/dismiss affordance (contrast UpdateBanner,
    // which has a "Dismiss" button). Assert no dismiss/close control exists in
    // the banner.
    await expect(
      banner.getByRole("button", { name: /dismiss|close/i }),
    ).toHaveCount(0);
  });

  test("Settings page hides the Software updates panel in demo", async ({
    page,
  }) => {
    await page.goto("/login");
    await expect(page).toHaveURL(/\/track/, { timeout: 10_000 });

    // The Software updates panel is hidden in demo.
    await page.goto("/settings");
    await expect(
      page.getByRole("heading", { name: "Settings" }),
    ).toBeVisible({ timeout: 10_000 });
    await expect(
      page.getByRole("heading", { name: "Software updates" }),
    ).toHaveCount(0);
  });
});
