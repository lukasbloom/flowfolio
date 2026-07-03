import { test, expect } from "@playwright/test";
import { seedReconcileDrift } from "./fixtures/seed-reconcile-drift";

/**
 * Reconciliation-polish integration spec.
 *
 * Locks two UI gaps closed in this plan against silent regression:
 *   - phantom rows render a *disabled* Accept button with a
 *     tooltip explaining why (not a text-only "Accept blocked" treatment).
 *   - per-row Accept/Reject/Dismiss buttons stay fully visible
 *     (no wrap/clip past the Value column) at a ~1483px viewport.
 *
 * The reconcile preview rows derive from app holdings, so the fixture seeds a
 * BTC buy; the spec then drives row state from the UI by typing into the
 * Snapshot qty input (snap "0" → phantom; snap ≠ app → qty_drift).
 *
 * Pre-requisites for running:
 *   1. Dev compose stack: docker compose -f compose.yml -f compose.dev.yml up -d
 *   2. PW_APP_PASSWORD env var set to the same value as APP_PASSWORD in .env
 *      (or APP_PASSWORD is exported in the shell running the test)
 *
 * Run: cd frontend && npm run test:e2e -- reconciliation-polish
 */

// Password is read from env, never hardcoded.
const APP_PASSWORD = process.env.PW_APP_PASSWORD ?? process.env.APP_PASSWORD ?? "";

function requirePassword(): string {
  if (!APP_PASSWORD) {
    throw new Error(
      "PW_APP_PASSWORD (or APP_PASSWORD) env var is not set. " +
        "Export it before running e2e tests: export PW_APP_PASSWORD=<your-password>",
    );
  }
  return APP_PASSWORD;
}

test.describe("RECP: reconciliation polish", () => {
  test("phantom row shows a disabled Accept button + tooltip", async ({
    page,
    request,
  }) => {
    const password = requirePassword();
    const seed = await seedReconcileDrift(request, password);

    // Authenticate the browser context (separate cookie jar from `request`).
    await page.goto("/login");
    await page.getByLabel(/password/i).fill(password);
    await page.getByRole("button", { name: /sign in/i }).click();
    await expect(page).toHaveURL(/\/track/, { timeout: 10_000 });

    // Go straight to the reconcile flow for the seeded account.
    await page.goto(`/reconcile?account=${seed.accountId}`);
    await expect(
      page.getByRole("heading", { name: /Reconcile/i }),
    ).toBeVisible({ timeout: 10_000 });

    // Drive the BTC row into the phantom state: app qty > 0, broker (snapshot)
    // says 0. The desktop table is the default viewport, so the row's Snapshot
    // qty input is the labelled "Snapshot qty for BTC" field.
    const snapInput = page
      .getByLabel("Snapshot qty for BTC")
      .first();
    await snapInput.fill("0");

    // The phantom RowActions now renders a real disabled <Button>Accept</Button>.
    // Assert it is present AND disabled.
    const acceptButton = page
      .getByRole("button", { name: "Accept", includeHidden: true })
      .first();
    await expect(acceptButton).toBeVisible();
    await expect(acceptButton).toBeDisabled();

    // Reject + Dismiss stay active on phantom rows.
    await expect(
      page.getByRole("button", { name: "Reject" }).first(),
    ).toBeEnabled();
    await expect(
      page.getByRole("button", { name: "Dismiss" }).first(),
    ).toBeEnabled();

    // Hover the focusable wrapper around the disabled Accept → tooltip opens.
    // (Disabled buttons swallow pointer events, so the wrapper span carries the
    // hover; the Tooltip content explains why Accept is blocked.)
    await acceptButton.hover();
    await expect(
      page
        .getByText(
          /Accepting a phantom would skip realized-gain accounting\. Use Reject to record the missing sell\./,
        )
        .first(),
    ).toBeVisible({ timeout: 5_000 });
  });

  test("action buttons are not clipped at a ~1483px viewport", async ({
    page,
    request,
  }) => {
    const password = requirePassword();
    const seed = await seedReconcileDrift(request, password);

    // ~1483px is the reported clip point.
    await page.setViewportSize({ width: 1483, height: 900 });

    await page.goto("/login");
    await page.getByLabel(/password/i).fill(password);
    await page.getByRole("button", { name: /sign in/i }).click();
    await expect(page).toHaveURL(/\/track/, { timeout: 10_000 });

    await page.goto(`/reconcile?account=${seed.accountId}`);
    await expect(
      page.getByRole("heading", { name: /Reconcile/i }),
    ).toBeVisible({ timeout: 10_000 });

    // Drive the BTC row into qty_drift (snapshot ≠ app) so the full
    // Accept/Reject/Dismiss triple renders.
    const snapInput = page.getByLabel("Snapshot qty for BTC").first();
    await snapInput.fill("2");

    const accept = page.getByRole("button", { name: "Accept" }).first();
    const reject = page.getByRole("button", { name: "Reject" }).first();
    const dismiss = page.getByRole("button", { name: "Dismiss" }).first();

    // All three buttons must be visible (not clipped/hidden) at 1483px.
    await expect(accept).toBeVisible();
    await expect(reject).toBeVisible();
    await expect(dismiss).toBeVisible();

    // None of the action buttons may overflow the right edge of the viewport
    // (the clip symptom was the triple spilling past the Value column / table).
    for (const btn of [accept, reject, dismiss]) {
      const box = await btn.boundingBox();
      expect(box, "action button should have a bounding box").not.toBeNull();
      if (box) {
        expect(box.x + box.width).toBeLessThanOrEqual(1483);
        expect(box.x).toBeGreaterThanOrEqual(0);
      }
    }
  });
});
