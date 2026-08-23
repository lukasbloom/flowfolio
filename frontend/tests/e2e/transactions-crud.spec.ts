import { test, expect } from "@playwright/test";
import { requireAppPassword, loginViaUi } from "./helpers/functionalAuth";
import { loginViaApi } from "./helpers/auth";

/**
 * Transaction CRUD integration spec: create a buy, edit its quantity, then
 * delete it, all through the real UI against the mutable dev stack. One
 * serial describe: each test builds on the DB state the previous one left
 * behind, and the instrument/account are discovered at runtime from
 * whatever the dev DB has seeded.
 *
 * Pre-requisites for running:
 *   1. Dev compose stack: docker compose -f compose.multi.yml -f compose.dev.yml up -d
 *   2. PW_APP_PASSWORD env var set to the same value as APP_PASSWORD in .env
 *      (or APP_PASSWORD is exported in the shell running the test)
 *
 * Run: cd frontend && npm run test:e2e -- transactions-crud
 * Run TWICE in a row to prove the afterAll cleanup is idempotent.
 */

interface Instrument {
  id: string;
  symbol: string;
  name: string;
}
interface Account {
  id: string;
  name: string;
}

test.describe.serial("transactions CRUD", () => {
  let instrument: Instrument;
  let account: Account;
  let createdTxnId: string | null = null;

  test.beforeAll(async ({ request }) => {
    await loginViaApi(request, requireAppPassword());

    const instrumentsResp = await request.get("/api/instruments");
    if (!instrumentsResp.ok()) {
      throw new Error(`Failed to list instruments: ${await instrumentsResp.text()}`);
    }
    const instruments: Instrument[] = await instrumentsResp.json();
    // STOP condition: an empty seeded catalog means this flow cannot be
    // exercised without seeding data ourselves, which is out of scope.
    if (instruments.length === 0) {
      throw new Error(
        "STOP: the dev DB has no instruments seeded, cannot pick one to buy. " +
          "Report this rather than seeding data.",
      );
    }
    instrument = instruments[0];

    const accountsResp = await request.get("/api/accounts");
    if (!accountsResp.ok()) {
      throw new Error(`Failed to list accounts: ${await accountsResp.text()}`);
    }
    const accounts: Account[] = await accountsResp.json();
    if (accounts.length === 0) {
      throw new Error(
        "STOP: the dev DB has no accounts seeded, cannot pick one to buy into. " +
          "Report this rather than seeding data.",
      );
    }
    account = accounts[0];
  });

  test.beforeEach(async ({ page }) => {
    await loginViaUi(page);
    await page.goto("/activity");
  });

  test("creates a buy transaction and it appears in the ledger", async ({ page }) => {
    await expect(page.getByRole("heading", { level: 1, name: "Activity" })).toBeVisible({
      timeout: 10_000,
    });

    // Open the Add-transaction picker, choose "Buy". #add-trigger is the
    // desktop button (the mobile FAB shares the same accessible name, so
    // role+name alone would be ambiguous).
    await page.locator("#add-trigger").click();
    const picker = page.getByRole("dialog");
    await expect(picker).toBeVisible({ timeout: 10_000 });
    await picker.getByRole("menuitem", { name: "Buy" }).click();

    // The Buy form dialog replaces the picker.
    const formDialog = page.getByRole("dialog");
    await expect(page.getByRole("heading", { name: "Add buy" })).toBeVisible({
      timeout: 10_000,
    });

    // Account select.
    await formDialog.getByLabel("Account", { exact: true }).click();
    await page.getByRole("option", { name: account.name, exact: true }).click();

    // Instrument select. Option text is "<symbol> — <name>".
    await formDialog.getByLabel("Instrument", { exact: true }).click();
    await page
      .getByRole("option", { name: `${instrument.symbol} — ${instrument.name}`, exact: true })
      .click();

    // Quantity + unit price. Date defaults to today already.
    await formDialog.getByLabel("Quantity", { exact: true }).fill("1");
    await formDialog.getByLabel("Unit price", { exact: true }).fill("10");

    // Currency: explicit EUR pick even though it's the field default, so the
    // spec doesn't silently pass if that default ever changes.
    await formDialog.getByLabel("Currency", { exact: true }).click();
    await page.getByRole("option", { name: "EUR", exact: true }).click();

    const [postResp] = await Promise.all([
      page.waitForResponse(
        (r) => r.url().includes("/api/transactions") && r.request().method() === "POST",
      ),
      formDialog.getByRole("button", { name: "Save transaction" }).click(),
    ]);
    expect(postResp.ok()).toBeTruthy();
    const created: { id: string } = await postResp.json();
    createdTxnId = created.id;

    await expect(page.getByText(/Buy added/)).toBeVisible({ timeout: 10_000 });
    await expect(page.getByRole("dialog")).toHaveCount(0);

    // The new row sorts to the top (date desc, then created_at desc). This
    // txn is dated today and is the most recently created.
    const firstRow = page.locator('[role="rowgroup"] [role="row"]').first();
    await expect(firstRow).toBeVisible({ timeout: 10_000 });
    const cells = firstRow.locator('[role="cell"]');
    await expect(cells.nth(1)).toContainText(account.name);
    await expect(cells.nth(2)).toContainText(instrument.symbol);
    await expect(cells.nth(4)).toHaveText("1");
    await expect(cells.nth(5)).toContainText("10.00");
  });

  test("edits the quantity and the ledger reflects it", async ({ page }) => {
    const firstRow = page.locator('[role="rowgroup"] [role="row"]').first();
    await expect(firstRow).toBeVisible({ timeout: 10_000 });
    await expect(firstRow.locator('[role="cell"]').nth(4)).toHaveText("1");

    await firstRow.getByRole("button", { name: "Edit transaction" }).click();
    const editDialog = page.getByRole("dialog");
    await expect(page.getByRole("heading", { name: "Edit buy" })).toBeVisible({
      timeout: 10_000,
    });

    const qtyField = editDialog.getByLabel("Quantity", { exact: true });
    await expect(qtyField).toHaveValue("1", { timeout: 10_000 });
    await qtyField.fill("2");

    const [putResp] = await Promise.all([
      page.waitForResponse(
        (r) => r.url().includes("/api/transactions/") && r.request().method() === "PUT",
      ),
      editDialog.getByRole("button", { name: "Save changes" }).click(),
    ]);
    expect(putResp.ok()).toBeTruthy();

    await expect(page.getByText(/Buy updated/)).toBeVisible({ timeout: 10_000 });
    await expect(page.getByRole("dialog")).toHaveCount(0);

    await expect(firstRow.locator('[role="cell"]').nth(4)).toHaveText("2", {
      timeout: 10_000,
    });
  });

  test("deletes the transaction and it disappears from the ledger", async ({ page }) => {
    const rows = page.locator('[role="rowgroup"] [role="row"]');
    const firstRow = rows.first();
    await expect(firstRow).toBeVisible({ timeout: 10_000 });
    await expect(firstRow.locator('[role="cell"]').nth(4)).toHaveText("2");
    // Fingerprint the full row so we can prove THIS row (not just "a" row)
    // is gone from the top after the delete-triggered refetch.
    const fingerprint = await firstRow.textContent();

    await firstRow.getByRole("button", { name: "Delete transaction" }).click();
    const confirmDialog = page.getByRole("dialog");
    await expect(
      confirmDialog.getByRole("heading", { name: "Delete this transaction?" }),
    ).toBeVisible({ timeout: 10_000 });

    const [deleteResp] = await Promise.all([
      page.waitForResponse(
        (r) => r.url().includes("/api/transactions/") && r.request().method() === "DELETE",
      ),
      confirmDialog.getByRole("button", { name: "Delete", exact: true }).click(),
    ]);
    expect(deleteResp.ok()).toBeTruthy();
    createdTxnId = null; // deleted through the UI, afterAll has nothing left to do

    await expect(page.getByText("Transaction deleted.")).toBeVisible({ timeout: 10_000 });

    // The default ledger view excludes soft-deleted rows entirely (no
    // strikethrough placeholder), so the row simply vanishes: the new top
    // row's full text must differ from the deleted row's fingerprint.
    await expect(rows.first()).not.toHaveText(fingerprint ?? "", { timeout: 10_000 });
  });

  test.afterAll(async ({ request }) => {
    // Idempotent safety net: if an earlier assertion failed mid-flow and the
    // UI delete never ran, remove the leftover transaction via the API. A
    // 404 means it is already gone (soft-deleted or never existed), which is fine.
    if (!createdTxnId) return;
    await loginViaApi(request, requireAppPassword());
    const resp = await request.delete(`/api/transactions/${createdTxnId}`);
    if (!resp.ok() && resp.status() !== 404) {
      console.warn(
        `Cleanup: could not delete leftover transaction ${createdTxnId} (${resp.status()}): ${await resp.text()}`,
      );
    }
  });
});
