import { test, expect } from "@playwright/test";

/**
 * Stale-session recovery: a browser carrying an invalid/expired session cookie
 * must end up on the login form, not locked into an empty dashboard shell.
 *
 * Read-only spec: navigations and a failed login attempt only, no DB writes.
 * Run: cd frontend && npm run test:e2e -- stale-session
 */

const BOGUS_JWT =
  "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9." +
  "eyJzdWIiOiJ1c2VyIiwiZXhwIjo5OTk5OTk5OTk5fQ." +
  "invalid-signature-0000000000000000000000000";

test.describe("stale session cookie", () => {
  test("invalid cookie on /track recovers to the login form", async ({
    browser,
    baseURL,
  }) => {
    const context = await browser.newContext();
    await context.addCookies([
      {
        name: "session",
        value: BOGUS_JWT,
        url: baseURL!,
        httpOnly: true,
        sameSite: "Strict",
      },
    ]);
    const page = await context.newPage();

    // Middleware sees a cookie and lets /track load; every API call 401s.
    // The 401 interceptor must clear the dead cookie and land us on /login.
    await page.goto("/track");
    await expect(page).toHaveURL(/\/login/, { timeout: 15_000 });
    await expect(page.getByLabel(/password/i)).toBeVisible({
      timeout: 10_000,
    });

    // The dead cookie must be gone so /login no longer bounces to /track.
    const cookies = await context.cookies(baseURL!);
    expect(cookies.find((c) => c.name === "session")).toBeUndefined();

    await context.close();
  });

  test("wrong password shows an error without a redirect loop", async ({
    page,
  }) => {
    // Guard: the 401 interceptor must NOT swallow /api/auth/login failures —
    // a wrong password renders an inline error and stays on /login.
    await page.goto("/login");
    await page.getByLabel(/password/i).fill("definitely-wrong-password");
    await page.getByRole("button", { name: /sign in/i }).click();

    await expect(page.getByText(/incorrect|invalid|wrong/i)).toBeVisible({
      timeout: 10_000,
    });
    await expect(page).toHaveURL(/\/login/);
  });
});
