import type { APIRequestContext } from "@playwright/test";

export interface ReconcileSeedResult {
  accountId: string;
  btcId: string;
  appQty: string;
}

/**
 * Seeds a single-account reconcile fixture for the polish spec:
 *   - BTC crypto instrument
 *   - A dedicated "Recon Polish" account
 *   - One buy transaction so BTC shows up as an app holding (app_qty > 0) in
 *     the reconcile preview.
 *
 * The reconcile preview rows derive from app holdings (transactions), so a
 * single buy is enough to make BTC appear. The SPEC then drives the row state
 * from the UI by typing into the Snapshot qty input:
 *   - snapshot qty "0"  → phantom  (app says >0, broker says 0)
 *   - snapshot qty ≠ app → qty_drift (full Accept/Reject/Dismiss)
 *
 * Instruments and accounts are created idempotently (findOrCreate) so the
 * fixture is safe across repeated runs against the same SQLite DB.
 */
export async function seedReconcileDrift(
  api: APIRequestContext,
  password: string,
): Promise<ReconcileSeedResult> {
  // 1. Authenticate — APIRequestContext stores the session cookie automatically.
  const loginResp = await api.post("/api/auth/login", { data: { password } });
  if (!loginResp.ok()) {
    throw new Error(
      `Login failed (${loginResp.status()}): ${await loginResp.text()}`,
    );
  }

  // 2. Create (or find) BTC instrument.
  const btcCreateResp = await api.post("/api/instruments", {
    data: {
      symbol: "BTC",
      name: "Bitcoin",
      instrument_type: "crypto",
      base_currency: "USD",
      price_source: "coingecko",
    },
  });
  let btc: { id: string; symbol: string };
  if (btcCreateResp.ok()) {
    btc = await btcCreateResp.json();
  } else {
    const listResp = await api.get("/api/instruments");
    if (!listResp.ok())
      throw new Error(`Failed to list instruments: ${await listResp.text()}`);
    const instruments: Array<{ id: string; symbol: string }> =
      await listResp.json();
    const existing = instruments.find((i) => i.symbol === "BTC");
    if (!existing) {
      throw new Error(
        `Could not create BTC (${btcCreateResp.status()}: ${await btcCreateResp.text()}) and it was not found in the catalog`,
      );
    }
    btc = existing;
  }

  // 3. Create (or find) the "Recon Polish" account.
  const accountsListResp = await api.get("/api/accounts");
  if (!accountsListResp.ok()) {
    throw new Error(`Failed to list accounts: ${await accountsListResp.text()}`);
  }
  const existingAccounts: Array<{ id: string; name: string }> =
    await accountsListResp.json();
  let account = existingAccounts.find((a) => a.name === "Recon Polish");
  if (!account) {
    const resp = await api.post("/api/accounts", {
      data: { name: "Recon Polish", account_type: "wallet" },
    });
    if (!resp.ok()) {
      throw new Error(
        `Failed to create account "Recon Polish" (${resp.status()}): ${await resp.text()}`,
      );
    }
    account = (await resp.json()) as { id: string; name: string };
  }

  // 4. Ensure a BTC holding exists in this account by recording a buy. The
  //    reconcile preview keys app_qty off the net holding, so a single buy of
  //    qty "1" gives a deterministic app_qty. Idempotency: the preview always
  //    reports the NET qty, so re-running this fixture would accumulate qty.
  //    Only buy if the account has no BTC holding yet.
  const today = new Date().toISOString().slice(0, 10);
  const previewResp = await api.get(
    `/api/reconciliation/preview?account_id=${account.id}&snapshot_date=${today}`,
  );
  let appQty = "1";
  let hasBtc = false;
  if (previewResp.ok()) {
    const preview: { rows: Array<{ instrument_id: string; app_qty: string }> } =
      await previewResp.json();
    const existingRow = preview.rows.find((r) => r.instrument_id === btc.id);
    if (existingRow) {
      hasBtc = true;
      appQty = existingRow.app_qty;
    }
  }

  if (!hasBtc) {
    const buyResp = await api.post("/api/transactions", {
      data: {
        account_id: account.id,
        instrument_id: btc.id,
        txn_type: "buy",
        date: today,
        quantity: "1",
        unit_price: "50000",
        price_currency: "USD",
      },
    });
    if (!buyResp.ok()) {
      throw new Error(
        `Could not seed BTC buy (${buyResp.status()}): ${await buyResp.text()}`,
      );
    }
    appQty = "1";
  }

  return { accountId: account.id, btcId: btc.id, appQty };
}
