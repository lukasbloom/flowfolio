import { test } from "node:test";
import assert from "node:assert/strict";

// NOTE: Imports `../instrument-aggregation.ts` with the explicit extension
// because Node's ESM resolver (used at runtime via
// `node --test --experimental-strip-types`) requires it. The `lib/__tests__/`
// directory is excluded from `tsconfig.json` so TypeScript's normal "no .ts
// extension in imports" rule doesn't fire here; the file is consumed only by
// Node's standalone test runner, never by the Next.js bundle.
import { aggregateInstrument } from "../instrument-aggregation.ts";
import type { PerfHoldingRow } from "../../components/perf/PerfTable.tsx";

// Characterization tests for the instrument-page KPI roll-up.
// These pin CURRENT behavior of `aggregateInstrument`, INCLUDING the float
// display-math that comes from its internal Number() coercions. A later change
// may route those coercions through a Decimal helper; these tests are what
// makes that refactor safe (they document "what the function does today").

// Minimal factory: every field PerfHoldingRow declares, defaulted so each test
// only sets what it exercises. Values that drive the math are Decimal-as-string
// exactly as the /api/perf payload delivers them.
function row(o: Partial<PerfHoldingRow> & { account_id?: string } = {}): PerfHoldingRow {
  return {
    account_id: o.account_id ?? "a1",
    account_name: o.account_name ?? "Acct",
    instrument_id: "i1",
    instrument_symbol: "X",
    instrument_name: "X Inc",
    instrument_type: "stock",
    risk_level: null,
    is_banked: false,
    quantity: o.quantity ?? "0",
    avg_cost: o.avg_cost ?? null,
    current_price: o.current_price ?? null,
    current_price_fetched_at: o.current_price_fetched_at ?? null,
    percent_return: null,
    realized_eur: o.realized_eur ?? null,
    twrr: o.twrr ?? null,
    twrr_annualized: o.twrr_annualized ?? false,
    twrr_period_days: o.twrr_period_days ?? null,
    twrr_reason: o.twrr_reason ?? null,
    status: o.status,
  } as PerfHoldingRow;
}

// --- Single row -----------------------------------------------------------

test("single open row: aggregate mirrors the row's own values", () => {
  const agg = aggregateInstrument([
    row({
      quantity: "10",
      avg_cost: "2",
      current_price: "3",
      current_price_fetched_at: "2026-01-01T00:00:00Z",
      realized_eur: "5",
      twrr: "0.1",
      twrr_period_days: 30,
      twrr_reason: null,
    }),
  ]);
  assert.equal(agg.total_quantity, 10);
  assert.equal(agg.weighted_avg_cost, 2);
  assert.equal(agg.market_value, 30);
  assert.equal(agg.cost_basis, 20);
  assert.equal(agg.unrealized, 10);
  assert.equal(agg.unrealized_pct, 0.5);
  assert.equal(agg.realized_total, 5);
  assert.deepEqual(agg.best_twrr, {
    value: "0.1",
    period_days: 30,
    annualized: false,
    reason: null,
  });
  assert.equal(agg.latest_price_fetched_at, "2026-01-01T00:00:00Z");
  assert.equal(agg.open_count, 1);
  assert.equal(agg.closed_count, 0);
});

// --- Two accounts, same instrument ---------------------------------------

test("two accounts: weighted_avg_cost = (q1*c1 + q2*c2)/(q1+q2), market value + realized sum", () => {
  // q1=10@2, q2=30@4 => weighted cost = (20 + 120)/40 = 3.5
  const agg = aggregateInstrument([
    row({
      account_id: "a1",
      quantity: "10",
      avg_cost: "2",
      current_price: "3",
      current_price_fetched_at: "2026-01-01T00:00:00Z",
      realized_eur: "1",
    }),
    row({
      account_id: "a2",
      quantity: "30",
      avg_cost: "4",
      current_price: "5",
      current_price_fetched_at: "2026-02-01T00:00:00Z",
      realized_eur: "2",
    }),
  ]);
  assert.equal(agg.total_quantity, 40);
  assert.equal(agg.weighted_avg_cost, 3.5);
  assert.equal(agg.market_value, 180); // 10*3 + 30*5
  assert.equal(agg.cost_basis, 140); // 10*2 + 30*4
  assert.equal(agg.unrealized, 40);
  assert.equal(agg.unrealized_pct, 40 / 140); // 0.2857142857142857
  assert.equal(agg.realized_total, 3); // 1 + 2
  // latest_price_fetched_at is the max across open rows.
  assert.equal(agg.latest_price_fetched_at, "2026-02-01T00:00:00Z");
  assert.equal(agg.open_count, 2);
});

test("characterization: float display-math is preserved through Number() coercion (known IEEE-754)", () => {
  // 0.1 + 0.2 == 0.30000000000000004 in IEEE-754; the function does NOT correct
  // this.
  const agg = aggregateInstrument([
    row({
      account_id: "a1",
      quantity: "1",
      avg_cost: "0.1",
      current_price: "0.1",
      current_price_fetched_at: "2026-01-01T00:00:00Z",
    }),
    row({
      account_id: "a2",
      quantity: "1",
      avg_cost: "0.2",
      current_price: "0.2",
      current_price_fetched_at: "2026-01-02T00:00:00Z",
    }),
  ]);
  assert.equal(agg.market_value, 0.30000000000000004); // KNOWN float-display behavior
  assert.equal(agg.cost_basis, 0.30000000000000004); // KNOWN float-display behavior
  assert.equal(agg.weighted_avg_cost, 0.15000000000000002); // 0.30000000000000004 / 2
});

// --- Zero-quantity row mixed in -------------------------------------------

test("zero-quantity row is excluded from weighted-cost / value math but still counts as an open row", () => {
  const agg = aggregateInstrument([
    row({
      account_id: "a1",
      quantity: "10",
      avg_cost: "2",
      current_price: "3",
      current_price_fetched_at: "2026-01-01T00:00:00Z",
    }),
    row({
      account_id: "a2",
      quantity: "0",
      avg_cost: "99",
      current_price: "99",
      current_price_fetched_at: "2026-09-01T00:00:00Z",
    }),
  ]);
  // Math only reflects the qty>0 row.
  assert.equal(agg.total_quantity, 10);
  assert.equal(agg.weighted_avg_cost, 2);
  assert.equal(agg.market_value, 30);
  assert.equal(agg.cost_basis, 20);
  // The zero-qty row is `continue`d BEFORE its price contributes, so its later
  // fetched_at timestamp does NOT leak into latest_price_fetched_at.
  assert.equal(agg.latest_price_fetched_at, "2026-01-01T00:00:00Z");
  // ...but it is not status:"closed", so it is still counted as open.
  assert.equal(agg.open_count, 2);
  assert.equal(agg.closed_count, 0);
});

// --- Missing current_price ------------------------------------------------

test("missing current_price makes market_value (and unrealized/_pct) null while cost_basis survives", () => {
  const agg = aggregateInstrument([
    row({ quantity: "10", avg_cost: "2", current_price: null }),
  ]);
  assert.equal(agg.cost_basis, 20);
  assert.equal(agg.market_value, null);
  assert.equal(agg.unrealized, null);
  assert.equal(agg.unrealized_pct, null);
  // No finite price contributed, so no timestamp is surfaced.
  assert.equal(agg.latest_price_fetched_at, null);
});

// --- Closed row + open row -------------------------------------------------

test("realized_total sums across BOTH open and closed rows; value math uses open rows only", () => {
  const agg = aggregateInstrument([
    row({
      account_id: "a1",
      quantity: "10",
      avg_cost: "2",
      current_price: "3",
      current_price_fetched_at: "2026-01-01T00:00:00Z",
      realized_eur: "5",
      status: "open",
      twrr: "0.2",
      twrr_period_days: 100,
    }),
    row({
      account_id: "a2",
      quantity: "5",
      avg_cost: "1",
      current_price: "2",
      status: "closed",
      realized_eur: "7",
      twrr: "0.9",
      twrr_period_days: 999,
    }),
  ]);
  // Only the open row feeds quantity / value / cost.
  assert.equal(agg.total_quantity, 10);
  assert.equal(agg.market_value, 30);
  assert.equal(agg.cost_basis, 20);
  // Realized spans both rows.
  assert.equal(agg.realized_total, 12); // 5 + 7
  assert.equal(agg.open_count, 1);
  assert.equal(agg.closed_count, 1);
  // TWRR is picked from OPEN rows only — the closed row's longer 999-day window
  // is intentionally ignored.
  assert.deepEqual(agg.best_twrr, {
    value: "0.2",
    period_days: 100,
    annualized: false,
    reason: null,
  });
});

test("realized_total is null when no row carries a finite realized_eur", () => {
  const agg = aggregateInstrument([
    row({ quantity: "10", avg_cost: "2", current_price: "3", realized_eur: null }),
  ]);
  assert.equal(agg.realized_total, null);
});

// --- TWRR representative selection ----------------------------------------

test("best_twrr is the OPEN row with the largest twrr_period_days", () => {
  const agg = aggregateInstrument([
    row({
      account_id: "a1",
      quantity: "1",
      avg_cost: "1",
      current_price: "1",
      current_price_fetched_at: "2026-01-01T00:00:00Z",
      twrr: "0.05",
      twrr_period_days: 10,
      twrr_annualized: false,
      twrr_reason: "r10",
    }),
    row({
      account_id: "a2",
      quantity: "1",
      avg_cost: "1",
      current_price: "1",
      current_price_fetched_at: "2026-01-02T00:00:00Z",
      twrr: "0.50",
      twrr_period_days: 365,
      twrr_annualized: true,
      twrr_reason: "r365",
    }),
  ]);
  assert.deepEqual(agg.best_twrr, {
    value: "0.50",
    period_days: 365,
    annualized: true,
    reason: "r365",
  });
});

test("when every open row has null twrr, best_twrr surfaces a null value with the first available reason", () => {
  const agg = aggregateInstrument([
    row({
      quantity: "1",
      avg_cost: "1",
      current_price: "1",
      current_price_fetched_at: "2026-01-01T00:00:00Z",
      twrr: null,
      twrr_reason: "insufficient",
    }),
  ]);
  assert.deepEqual(agg.best_twrr, {
    value: null,
    period_days: null,
    annualized: false,
    reason: "insufficient",
  });
});

// --- Empty input ----------------------------------------------------------

test("empty rows produce an all-null/zero aggregate with best_twrr null", () => {
  const agg = aggregateInstrument([]);
  assert.equal(agg.total_quantity, 0);
  assert.equal(agg.weighted_avg_cost, null);
  assert.equal(agg.market_value, null);
  assert.equal(agg.cost_basis, null);
  assert.equal(agg.unrealized, null);
  assert.equal(agg.unrealized_pct, null);
  assert.equal(agg.realized_total, null);
  assert.equal(agg.best_twrr, null);
  assert.equal(agg.latest_price_fetched_at, null);
  assert.equal(agg.open_count, 0);
  assert.equal(agg.closed_count, 0);
});
