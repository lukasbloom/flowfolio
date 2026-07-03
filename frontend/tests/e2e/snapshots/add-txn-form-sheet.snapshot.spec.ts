import { test, expect, type Page } from "@playwright/test";
import { sanitizeHtml } from "../helpers/sanitizeHtml";
import { resetGoldenDb } from "../helpers/dbReset";

const FIXED_INSTANT = "2026-04-30T12:00:00Z";

/**
 * Open the AddTxnFormSheet by:
 *   1. Clicking the header "+Add" / "Add transaction" button (aria-label="Add transaction")
 *   2. Clicking the picker option matching `variant`
 *
 * After this function returns, the form-sheet dialog is open and stable.
 */
async function openAddForm(
  page: Page,
  variant: "Buy" | "Sell" | "Trade" | "Spend" | "Yield"
): Promise<void> {
  // Click the header AddButton (aria-label="Add transaction", id="add-trigger").
  // Use the specific ID to avoid matching the FAB button (#add-trigger-fab) which
  // can be simultaneously visible in sequential test runs.
  await page.locator("#add-trigger").click();

  // Wait for the picker dialog to open.
  const pickerDialog = page.getByRole("dialog").filter({ hasText: "Add transaction" });
  await expect(pickerDialog).toBeVisible({ timeout: 3_000 });

  // Click the picker option. The options use role="menuitem" inside role="menu".
  // Each button's accessible name includes both the label ("Buy") and description text,
  // so we match with hasText to find the element containing the variant label.
  const menu = pickerDialog.getByRole("menu");
  await menu.getByRole("menuitem").filter({ hasText: new RegExp(`^${variant}`, "i") }).click();

  // Wait for the form-sheet dialog to open (picker closes, form opens).
  // The form dialog title differs per variant: "Add buy", "Add sell", etc.
  const formDialog = page
    .getByRole("dialog")
    .filter({ has: page.locator("form") });
  await expect(formDialog).toBeVisible({ timeout: 3_000 });
}

/**
 * Capture the `<form>` body inside the currently-open form-sheet dialog,
 * sanitize transient IDs and data-state, and return the stable HTML string.
 *
 * Waits for network idle so that Radix combobox options (fetched from the API)
 * are populated and stable before the snapshot is taken.
 */
async function captureFormBody(page: Page): Promise<string> {
  // The form dialog is the dialog containing a <form> element.
  const formEl = page
    .getByRole("dialog")
    .filter({ has: page.locator("form") })
    .locator("form");
  await expect(formEl).toBeVisible({ timeout: 3_000 });
  // Wait for network idle so API-populated combobox options are stable.
  await page.waitForLoadState("networkidle", { timeout: 5_000 });
  const raw = await formEl.innerHTML();
  return sanitizeHtml(raw);
}

test.describe("AddTxnFormSheet snapshots", () => {
  test.beforeEach(async ({ page }) => {
    await resetGoldenDb();
    await page.clock.install({ time: FIXED_INSTANT });
    // Navigate to /activity — the header +Add button is present on every authed page.
    await page.goto("/activity");
  });

  test("Buy variant", async ({ page }) => {
    await openAddForm(page, "Buy");
    expect(await captureFormBody(page)).toMatchSnapshot("add-buy.html");
  });

  test("Sell variant", async ({ page }) => {
    await openAddForm(page, "Sell");
    expect(await captureFormBody(page)).toMatchSnapshot("add-sell.html");
  });

  test("Trade variant", async ({ page }) => {
    await openAddForm(page, "Trade");
    expect(await captureFormBody(page)).toMatchSnapshot("add-trade.html");
  });

  test("Spend variant", async ({ page }) => {
    await openAddForm(page, "Spend");
    expect(await captureFormBody(page)).toMatchSnapshot("add-spend.html");
  });

  test("Yield variant", async ({ page }) => {
    await openAddForm(page, "Yield");
    expect(await captureFormBody(page)).toMatchSnapshot("add-yield.html");
  });
});
