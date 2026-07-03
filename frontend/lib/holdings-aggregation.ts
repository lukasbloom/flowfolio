/**
 * Cross-instrument aggregation for the holdings PerfTable.
 *
 * Sibling to `instrument-aggregation.ts` (which collapses already-filtered
 * per-(account, instrument) rows for ONE instrument into a single headline
 * summary). This module does the same per-field math but groups MANY
 * instruments at once and emits one row per `instrument_id`, type-compatible
 * with PerfTable's render — minus `account_id` / `account_name`, which only
 * make sense on raw (account, instrument) rows.
 *
 * Caller pattern (PerfTable.tsx):
 *
 *     const displayRows =
 *       filterBy?.dimension === "account"
 *         ? filteredData                              // by-account drill: per-(account,instrument) preserved
 *         : aggregateHoldingsByInstrument(filteredData);
 *
 * The carve-out exists because "what's in this account?" only makes sense
 * with rows split by account — aggregating across accounts would erase the
 * very cut the user just clicked.
 *
 * All inputs are Decimal-as-string from `/api/perf` / `/api/closed`. Outputs
 * remain Decimal-as-string for fields the API serialises that way (quantity,
 * avg_cost, current_price, percent_return, realized_eur, twrr) so the render
 * pipeline can keep using string-based decimal helpers (formatMoney,
 * formatPercent, formatQuantity) end-to-end. Float math is used for
 * intermediate aggregation only — same compromise as `instrument-aggregation.ts`.
 */
import type { PerfHoldingRow } from "@/components/perf/PerfTable";
import { toDisplayNumber } from "./decimal-strings.ts";

/**
 * One row per instrument. Identical shape to `PerfHoldingRow` minus
 * `account_id` / `account_name` (which carry no meaning once accounts are
 * collapsed).
 */
export interface AggregatedHoldingRow {
  instrument_id: string;
  instrument_symbol: string;
  instrument_name: string;
  instrument_type: string;
  display_decimals?: number | null;
  risk_level: string | null;
  is_banked: boolean;
  quantity: string;
  avg_cost: string | null;
  current_price: string | null;
  current_price_fetched_at: string | null;
  percent_return: string | null;
  realized_eur: string | null;
  twrr: string | null;
  twrr_annualized: boolean;
  twrr_period_days: number | null;
  twrr_reason: string | null;
  status?: "open" | "closed";

  // Closed-mode fields (forwarded from the picked closed row when status === "closed").
  last_close?: string | null;
  last_close_date?: string | null;
  twrr_window_days?: number | null;
}

/**
 * Pick the open row with the longest `twrr_period_days` whose `twrr` is non-null.
 * Falls back to surfacing the first non-null `twrr_reason` if no row has a value
 * (matches `instrument-aggregation.ts` fallback so suppression reasons still reach the UI).
 */
function pickBestTwrr(openRows: PerfHoldingRow[]): {
  value: string | null;
  period_days: number | null;
  annualized: boolean;
  reason: string | null;
} {
  let best: {
    value: string | null;
    period_days: number | null;
    annualized: boolean;
    reason: string | null;
  } | null = null;

  for (const r of openRows) {
    if (r.twrr === null) continue;
    const pd = r.twrr_period_days ?? 0;
    if (best === null || pd > (best.period_days ?? 0)) {
      best = {
        value: r.twrr,
        period_days: r.twrr_period_days,
        annualized: r.twrr_annualized,
        reason: r.twrr_reason,
      };
    }
  }

  if (best === null) {
    if (openRows.length === 0) {
      return { value: null, period_days: null, annualized: false, reason: null };
    }
    const firstReason = openRows.find((r) => r.twrr_reason)?.twrr_reason ?? null;
    return { value: null, period_days: null, annualized: false, reason: firstReason };
  }
  return best;
}

/**
 * Aggregate raw /api/perf rows by `instrument_id`. Output ordering is
 * insertion-order of first-seen instrument_id (downstream sort is the
 * authoritative ordering; this is just a deterministic tie-breaker).
 */
export function aggregateHoldingsByInstrument(
  rows: PerfHoldingRow[],
): AggregatedHoldingRow[] {
  const groups = new Map<string, PerfHoldingRow[]>();
  const order: string[] = [];

  for (const r of rows) {
    const list = groups.get(r.instrument_id);
    if (list) {
      list.push(r);
    } else {
      groups.set(r.instrument_id, [r]);
      order.push(r.instrument_id);
    }
  }

  const out: AggregatedHoldingRow[] = [];
  for (const id of order) {
    const group = groups.get(id)!;
    out.push(aggregateGroup(group));
  }
  return out;
}

function aggregateGroup(group: PerfHoldingRow[]): AggregatedHoldingRow {
  const openRows = group.filter((r) => r.status !== "closed");
  const closedRows = group.filter((r) => r.status === "closed");
  const anyOpen = openRows.length > 0;
  const first = group[0];

  // Quantity sum (over the full group — open AND closed; closed rows carry qty 0
  // in practice but we sum unconditionally so partial states survive).
  let total_quantity = 0;
  for (const r of group) {
    const q = toDisplayNumber(r.quantity);
    if (Number.isFinite(q)) total_quantity += q;
  }

  // Weighted avg_cost across open rows that have BOTH qty > 0 and avg_cost.
  // If NONE contribute → null. Cost-basis-complete flag separately controls %return.
  let weighted_cost_sum = 0;
  let avg_cost_qty_sum = 0;
  let cost_basis_complete = true;
  for (const r of openRows) {
    const q = toDisplayNumber(r.quantity);
    if (!Number.isFinite(q) || q <= 0) continue;
    if (r.avg_cost === null || r.avg_cost === undefined) {
      cost_basis_complete = false;
      continue;
    }
    const ac = toDisplayNumber(r.avg_cost);
    if (!Number.isFinite(ac)) {
      cost_basis_complete = false;
      continue;
    }
    weighted_cost_sum += q * ac;
    avg_cost_qty_sum += q;
  }
  const weighted_avg_cost: number | null =
    avg_cost_qty_sum > 0 ? weighted_cost_sum / avg_cost_qty_sum : null;

  // current_price — pick from the open row with the latest
  // current_price_fetched_at among rows that actually carry a parseable price.
  // current_price_fetched_at output mirrors the picked price's row, so the
  // displayed timestamp can never misrepresent the price's age.
  let latestFetched: string | null = null;
  let pickedPrice: string | null = null;
  let market_value = 0;
  let market_value_complete = true;
  for (const r of openRows) {
    const q = toDisplayNumber(r.quantity);
    if (!Number.isFinite(q) || q <= 0) {
      // qty=0 rows contribute neither price nor MV (they are functionally closed lots)
      continue;
    }
    if (r.current_price === null || r.current_price === undefined) {
      // No price → contributes neither MV nor timestamp.
      market_value_complete = false;
      continue;
    }
    const cp = toDisplayNumber(r.current_price);
    if (!Number.isFinite(cp)) {
      market_value_complete = false;
      continue;
    }
    market_value += q * cp;

    // current_price_fetched_at and pickedPrice move together — only adopt a
    // row's timestamp when we adopt its price. A row with no fetched_at can
    // still seed pickedPrice when none has been picked yet.
    if (r.current_price_fetched_at) {
      if (latestFetched === null || r.current_price_fetched_at > latestFetched) {
        latestFetched = r.current_price_fetched_at;
        pickedPrice = r.current_price;
      } else if (pickedPrice === null) {
        pickedPrice = r.current_price;
      }
    } else if (pickedPrice === null) {
      pickedPrice = r.current_price;
    }
  }

  // percent_return — (mv - cb) / cb. Null if EITHER incomplete (matches
  // instrument-aggregation.ts semantics; null gates downstream rendering).
  let percent_return: string | null = null;
  if (
    anyOpen &&
    cost_basis_complete &&
    market_value_complete &&
    avg_cost_qty_sum > 0 &&
    weighted_avg_cost !== null
  ) {
    const cost_basis = avg_cost_qty_sum * weighted_avg_cost;
    if (cost_basis > 0) {
      percent_return = String((market_value - cost_basis) / cost_basis);
    }
  }

  // realized_eur — sum across ALL rows (open + closed). null only if no row contributed.
  let realized_total = 0;
  let any_realized = false;
  for (const r of group) {
    if (r.realized_eur === null || r.realized_eur === undefined) continue;
    const v = toDisplayNumber(r.realized_eur);
    if (Number.isFinite(v)) {
      realized_total += v;
      any_realized = true;
    }
  }
  const realized_eur: string | null = any_realized ? String(realized_total) : null;

  // TWRR — longest-window open-row pick (closed rows excluded; mixed-window mean is misleading).
  const best = pickBestTwrr(openRows);

  // Closed-mode field inheritance — when the group has NO open rows, surface
  // last_close / last_close_date / twrr_window_days from the first closed row
  // so PerfTable in closed mode (and the unified include_closed=1 surface) still renders them.
  let last_close: string | null | undefined;
  let last_close_date: string | null | undefined;
  let twrr_window_days: number | null | undefined;
  if (!anyOpen && closedRows.length > 0) {
    const c = closedRows[0];
    last_close = c.last_close ?? null;
    last_close_date = c.last_close_date ?? null;
    twrr_window_days = c.twrr_window_days ?? null;
  }

  const aggregated: AggregatedHoldingRow = {
    instrument_id: first.instrument_id,
    instrument_symbol: first.instrument_symbol,
    instrument_name: first.instrument_name,
    instrument_type: first.instrument_type,
    display_decimals: first.display_decimals,
    risk_level: first.risk_level,
    is_banked: first.is_banked,
    quantity: String(total_quantity),
    avg_cost: weighted_avg_cost === null ? null : String(weighted_avg_cost),
    current_price: pickedPrice,
    current_price_fetched_at: latestFetched,
    percent_return,
    realized_eur,
    twrr: best.value,
    twrr_annualized: best.annualized,
    twrr_period_days: best.period_days,
    twrr_reason: best.reason,
    status: anyOpen ? "open" : "closed",
  };

  if (last_close !== undefined) aggregated.last_close = last_close;
  if (last_close_date !== undefined) aggregated.last_close_date = last_close_date;
  if (twrr_window_days !== undefined) aggregated.twrr_window_days = twrr_window_days;

  return aggregated;
}
