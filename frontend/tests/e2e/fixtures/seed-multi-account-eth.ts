import type { APIRequestContext } from "@playwright/test";

export interface SeedResult {
  ethId: string;
  revolutEarnAccountId: string;
  coldWalletAccountId: string;
  revolutApyConfigId: string;
  coldWalletApyConfigId: string;
  yieldTxnId: string;
}

/**
 * Seeds the multi-account-same-instrument fixture:
 *   - ETH crypto instrument
 *   - Revolut Earn account + Cold Wallet account
 *   - APY config for each (Revolut Earn @ 2.37%, Cold Wallet @ 4%)
 *   - One auto-accrual yield row tied to Revolut Earn + ETH
 *
 * The fixture posts source="accrual" with notes="auto-accrual 2.37% APY"
 * to mimic the daily APScheduler accrual job without waiting for the cron tick.
 * This exercises the same POST /api/transactions code path used by the accrual
 * job, which relaxed the yield restriction and added source validation.
 *
 * Instruments and accounts are created idempotently (findOrCreate pattern) so the
 * fixture is safe to call across multiple test runs against the same SQLite DB.
 */
export async function seedMultiAccountEth(
  api: APIRequestContext,
  password: string,
): Promise<SeedResult> {
  // 1. Authenticate — Playwright APIRequestContext stores cookies automatically
  //    so all subsequent calls in this context carry the session cookie.
  const loginResp = await api.post("/api/auth/login", { data: { password } });
  if (!loginResp.ok()) {
    throw new Error(`Login failed (${loginResp.status()}): ${await loginResp.text()}`);
  }

  // 2. Create (or find) ETH instrument.
  //    price_source="coingecko" is the canonical source for crypto (instrument_type="crypto").
  const ethCreateResp = await api.post("/api/instruments", {
    data: {
      symbol: "ETH",
      name: "Ethereum",
      instrument_type: "crypto",
      base_currency: "USD",
      price_source: "coingecko",
    },
  });
  let ethInstrument: { id: string; symbol: string };
  if (ethCreateResp.ok()) {
    ethInstrument = await ethCreateResp.json();
  } else {
    // Instrument likely already exists — find it in the catalog.
    const listResp = await api.get("/api/instruments");
    if (!listResp.ok()) throw new Error(`Failed to list instruments: ${await listResp.text()}`);
    const instruments: Array<{ id: string; symbol: string }> = await listResp.json();
    const existing = instruments.find((i) => i.symbol === "ETH");
    if (!existing) {
      throw new Error(
        `Could not create ETH instrument (${ethCreateResp.status()}: ${await ethCreateResp.text()}) and it was not found in the catalog`,
      );
    }
    ethInstrument = existing;
  }

  // 3. Create (or find) Revolut Earn and Cold Wallet accounts.
  const accountsListResp = await api.get("/api/accounts");
  if (!accountsListResp.ok()) {
    throw new Error(`Failed to list accounts: ${await accountsListResp.text()}`);
  }
  const existingAccounts: Array<{ id: string; name: string }> = await accountsListResp.json();

  const findOrCreateAccount = async (name: string, accountType: string) => {
    const existing = existingAccounts.find((a) => a.name === name);
    if (existing) return existing;
    const resp = await api.post("/api/accounts", {
      data: { name, account_type: accountType },
    });
    if (!resp.ok()) {
      throw new Error(`Failed to create account "${name}" (${resp.status()}): ${await resp.text()}`);
    }
    return resp.json() as Promise<{ id: string; name: string }>;
  };

  const revolutEarn = await findOrCreateAccount("Revolut Earn", "broker");
  const coldWallet = await findOrCreateAccount("Cold Wallet", "wallet");

  // 4. Create APY configs for each (account, instrument) pair.
  //    The POST endpoint closes any prior open row for the pair automatically
  //    (apy_config router: effective_to cascade). A 409 means the exact same
  //    effective_from already exists — treat as idempotent and fetch the existing id.
  const today = new Date().toISOString().slice(0, 10);

  const createApyConfig = async (accountId: string, apyRate: string) => {
    const resp = await api.post("/api/apy-config", {
      data: {
        account_id: accountId,
        instrument_id: ethInstrument.id,
        apy_rate: apyRate,
        effective_from: today,
      },
    });
    if (resp.ok()) return (await resp.json()) as { id: string };
    if (resp.status() === 409) {
      // Already exists for this pair+date. Fetch all configs and find the open row.
      const listResp = await api.get(
        `/api/apy-config?instrument_id=${ethInstrument.id}&account_id=${accountId}`,
      );
      if (!listResp.ok()) {
        throw new Error(
          `APY config 409 conflict and list fallback failed (${listResp.status()})`,
        );
      }
      const configs: Array<{ id: string; effective_to: string | null }> = await listResp.json();
      const openRow = configs.find((c) => c.effective_to === null);
      if (!openRow) throw new Error(`No open APY config found after 409 for account ${accountId}`);
      return openRow;
    }
    throw new Error(
      `Failed to create APY config for account ${accountId} (${resp.status()}): ${await resp.text()}`,
    );
  };

  const revolutApyConfig = await createApyConfig(revolutEarn.id, "0.0237");  // 2.37%
  const coldWalletApyConfig = await createApyConfig(coldWallet.id, "0.04");  // 4%

  // 5. Create an auto-accrual yield row tied to Revolut Earn + ETH.
  //    source="accrual" + notes prefix "auto-accrual " is exactly what the daily
  //    APScheduler accrual job writes. EditTxnDialog detects auto-accrual yield
  //    via `notes?.startsWith("auto-accrual ")` and renders
  //    the ActionBanner rather than the editable YieldForm.
  //
  //    Relaxed to allow POST /api/transactions with txn_type="yield"
  //    and added source validation. This avoids raw SQL seeding and exercises the real
  //    validated API surface.
  const yieldResp = await api.post("/api/transactions", {
    data: {
      account_id: revolutEarn.id,
      instrument_id: ethInstrument.id,
      txn_type: "yield",
      date: today,
      quantity: "0.001",
      notes: "auto-accrual 2.37% APY",
      source: "accrual",
    },
  });
  if (!yieldResp.ok()) {
    throw new Error(
      `Could not seed auto-accrual yield row (${yieldResp.status()}): ${await yieldResp.text()}`,
    );
  }
  const yieldTxn = (await yieldResp.json()) as { id: string };

  return {
    ethId: ethInstrument.id,
    revolutEarnAccountId: revolutEarn.id,
    coldWalletAccountId: coldWallet.id,
    revolutApyConfigId: revolutApyConfig.id,
    coldWalletApyConfigId: coldWalletApyConfig.id,
    yieldTxnId: yieldTxn.id,
  };
}
