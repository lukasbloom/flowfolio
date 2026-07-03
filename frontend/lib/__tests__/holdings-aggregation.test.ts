import { test } from "node:test";
import assert from "node:assert/strict";

// Mirrors lib/__tests__/format.test.ts: explicit .ts extension (lib/__tests__ is
// excluded from tsconfig.json), run via `node --test --experimental-strip-types`.
// Imports the pure module under test from ../holdings-aggregation.ts.
import { aggregateHoldingsByInstrument } from "../holdings-aggregation.ts";
import type { PerfHoldingRow } from "../../components/perf/PerfTable.tsx";

// Minimal factory — fills required PerfHoldingRow fields with sensible defaults
// so each test only spells out the fields it cares about.
function row(overrides: Partial<PerfHoldingRow>): PerfHoldingRow {
  return {
    account_id: "acct-default",
    account_name: "Default",
    instrument_id: "inst-default",
    instrument_symbol: "DFLT",
    instrument_name: "Default Instrument",
    instrument_type: "crypto",
    display_decimals: null,
    risk_level: null,
    is_banked: false,
    quantity: "0",
    avg_cost: null,
    current_price: null,
    current_price_fetched_at: null,
    percent_return: null,
    realized_eur: null,
    twrr: null,
    twrr_annualized: false,
    twrr_period_days: null,
    twrr_reason: null,
    status: "open",
    ...overrides,
  };
}

test("A: single-account input returns one functionally-identical row", () => {
  const input = [
    row({
      account_id: "a1",
      account_name: "Bit2Me",
      instrument_id: "btc",
      instrument_symbol: "BTC",
      instrument_name: "Bitcoin",
      quantity: "0.5",
      avg_cost: "40000",
      current_price: "60000",
      current_price_fetched_at: "2026-05-25T12:00:00Z",
      percent_return: "0.5",
      realized_eur: "100",
      twrr: "0.42",
      twrr_annualized: true,
      twrr_period_days: 365,
      twrr_reason: null,
    }),
  ];
  const out = aggregateHoldingsByInstrument(input);
  assert.equal(out.length, 1);
  const r = out[0];
  assert.equal(r.instrument_id, "btc");
  assert.equal(r.instrument_symbol, "BTC");
  assert.equal(Number(r.quantity), 0.5);
  assert.equal(Number(r.avg_cost), 40000);
  assert.equal(Number(r.current_price), 60000);
  assert.equal(r.current_price_fetched_at, "2026-05-25T12:00:00Z");
  assert.equal(Number(r.realized_eur), 100);
  // % return = (mv - cb) / cb = (30000 - 20000) / 20000 = 0.5
  assert.equal(Number(r.percent_return), 0.5);
  assert.equal(r.twrr, "0.42");
  assert.equal(r.twrr_annualized, true);
  assert.equal(r.twrr_period_days, 365);
  assert.equal(r.status, "open");
  // Aggregated rows must NOT carry account_id/account_name.
  assert.equal(("account_id" in r) ? (r as unknown as { account_id?: string }).account_id : undefined, undefined);
  assert.equal(("account_name" in r) ? (r as unknown as { account_name?: string }).account_name : undefined, undefined);
});

test("B: two accounts same instrument — qty sums, avg_cost is weighted mean, %return matches manual calc", () => {
  const input = [
    row({
      account_id: "a1",
      instrument_id: "btc",
      quantity: "0.4",
      avg_cost: "30000",
      current_price: "60000",
      current_price_fetched_at: "2026-05-25T10:00:00Z",
    }),
    row({
      account_id: "a2",
      instrument_id: "btc",
      quantity: "0.6",
      avg_cost: "50000",
      current_price: "60000",
      current_price_fetched_at: "2026-05-25T12:00:00Z",
    }),
  ];
  const out = aggregateHoldingsByInstrument(input);
  assert.equal(out.length, 1);
  const r = out[0];
  // qty = 0.4 + 0.6 = 1.0
  assert.equal(Number(r.quantity), 1.0);
  // weighted avg cost = (0.4 * 30000 + 0.6 * 50000) / 1.0 = (12000 + 30000) / 1 = 42000
  assert.equal(Number(r.avg_cost), 42000);
  // current_price — freshest wins, both equal, so 60000
  assert.equal(Number(r.current_price), 60000);
  // freshest fetched_at
  assert.equal(r.current_price_fetched_at, "2026-05-25T12:00:00Z");
  // %return = (mv - cb) / cb = (60000 - 42000) / 42000
  assert.ok(Math.abs(Number(r.percent_return) - (60000 - 42000) / 42000) < 1e-9);
});

test("C: any open row missing avg_cost → percent_return is null on output (incomplete cost basis)", () => {
  const input = [
    row({
      account_id: "a1",
      instrument_id: "eth",
      quantity: "1",
      avg_cost: "2000",
      current_price: "3000",
    }),
    row({
      account_id: "a2",
      instrument_id: "eth",
      quantity: "1",
      avg_cost: null, // missing
      current_price: "3000",
    }),
  ];
  const out = aggregateHoldingsByInstrument(input);
  assert.equal(out.length, 1);
  assert.equal(out[0].percent_return, null);
  // avg_cost itself should also be the weighted mean over rows that DO have it,
  // or null if none contribute. Here one row contributes (qty 1 * 2000) / qty-contributing 1 = 2000.
  // BUT cost-basis-complete flag is false, so percent_return null — that's the gate.
});

test("D: two open rows with different twrr_period_days → output twrr matches the longer-period row", () => {
  const input = [
    row({
      account_id: "a1",
      instrument_id: "btc",
      quantity: "0.5",
      avg_cost: "40000",
      current_price: "60000",
      twrr: "0.10",
      twrr_period_days: 90,
      twrr_annualized: false,
      twrr_reason: "short",
    }),
    row({
      account_id: "a2",
      instrument_id: "btc",
      quantity: "0.5",
      avg_cost: "40000",
      current_price: "60000",
      twrr: "0.42",
      twrr_period_days: 730,
      twrr_annualized: true,
      twrr_reason: "long",
    }),
  ];
  const out = aggregateHoldingsByInstrument(input);
  assert.equal(out.length, 1);
  const r = out[0];
  // Longer-period row wins.
  assert.equal(r.twrr, "0.42");
  assert.equal(r.twrr_period_days, 730);
  assert.equal(r.twrr_annualized, true);
  assert.equal(r.twrr_reason, "long");
});

test("E: one open + one closed row for same instrument → status open, realized sums both", () => {
  const input = [
    row({
      account_id: "a1",
      instrument_id: "xrp",
      quantity: "100",
      avg_cost: "0.5",
      current_price: "0.8",
      realized_eur: "10",
      status: "open",
    }),
    row({
      account_id: "a2",
      instrument_id: "xrp",
      quantity: "0",
      avg_cost: null,
      current_price: null,
      realized_eur: "25",
      status: "closed",
    }),
  ];
  const out = aggregateHoldingsByInstrument(input);
  assert.equal(out.length, 1);
  const r = out[0];
  assert.equal(r.status, "open");
  // realized = 10 + 25 = 35 across both rows
  assert.equal(Number(r.realized_eur), 35);
});

test("F: empty input returns []", () => {
  const out = aggregateHoldingsByInstrument([]);
  assert.deepEqual(out, []);
});

test("G: stable ordering — output is insertion-order of first-seen instrument_id", () => {
  const input = [
    row({ instrument_id: "btc", instrument_symbol: "BTC", quantity: "1" }),
    row({ instrument_id: "eth", instrument_symbol: "ETH", quantity: "1" }),
    row({ instrument_id: "btc", instrument_symbol: "BTC", quantity: "1" }), // dup — same group
    row({ instrument_id: "vwce", instrument_symbol: "VWCE", quantity: "1" }),
  ];
  const out = aggregateHoldingsByInstrument(input);
  assert.deepEqual(
    out.map((r) => r.instrument_id),
    ["btc", "eth", "vwce"],
  );
});

// Extra coverage on the closed-only group — important for the "closed positions" surface
// even though /holdings/closed uses a different component. PerfTable in mode="closed"
// (today via /api/closed) would still be aggregator-eligible if filterBy is not "account".
test("H: all-closed group → status closed, last_close + last_close_date inherited from first closed row", () => {
  const input = [
    row({
      account_id: "a1",
      instrument_id: "xrp",
      quantity: "0",
      avg_cost: null,
      current_price: null,
      realized_eur: "10",
      status: "closed",
      last_close: "0.65",
      last_close_date: "2026-04-30",
      twrr_window_days: 200,
    }),
  ];
  const out = aggregateHoldingsByInstrument(input);
  assert.equal(out.length, 1);
  const r = out[0];
  assert.equal(r.status, "closed");
  assert.equal(r.last_close, "0.65");
  assert.equal(r.last_close_date, "2026-04-30");
  assert.equal(r.twrr_window_days, 200);
});

test("J: freshest row has no current_price → latestFetched falls back to the older priced row's timestamp", () => {
  const input = [
    row({
      account_id: "a1",
      instrument_id: "btc",
      quantity: "0.5",
      avg_cost: "30000",
      current_price: "60000",
      current_price_fetched_at: "2026-05-25T08:00:00Z",
    }),
    row({
      account_id: "a2",
      instrument_id: "btc",
      quantity: "0.5",
      avg_cost: "30000",
      current_price: null,
      current_price_fetched_at: "2026-05-25T12:00:00Z", // fresher BUT no price
    }),
  ];
  const out = aggregateHoldingsByInstrument(input);
  assert.equal(out.length, 1);
  // The 12:00 row contributes neither price nor timestamp to the output.
  assert.equal(Number(out[0].current_price), 60000);
  assert.equal(out[0].current_price_fetched_at, "2026-05-25T08:00:00Z");
});

// Extra coverage: current_price freshness picks across timestamps.
test("I: differing current_price_fetched_at — freshest wins for current_price + max for fetched_at", () => {
  const input = [
    row({
      account_id: "a1",
      instrument_id: "btc",
      quantity: "1",
      avg_cost: "30000",
      current_price: "59000",
      current_price_fetched_at: "2026-05-25T08:00:00Z",
    }),
    row({
      account_id: "a2",
      instrument_id: "btc",
      quantity: "1",
      avg_cost: "30000",
      current_price: "60000",
      current_price_fetched_at: "2026-05-25T12:00:00Z",
    }),
  ];
  const out = aggregateHoldingsByInstrument(input);
  assert.equal(out.length, 1);
  assert.equal(Number(out[0].current_price), 60000);
  assert.equal(out[0].current_price_fetched_at, "2026-05-25T12:00:00Z");
});
