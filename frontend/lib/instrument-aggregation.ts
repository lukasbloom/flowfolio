/**
 * Pure aggregation helpers for the instrument detail page KPI block.
 *
 * Consumes the existing `/api/perf?include_closed=1` payload (filtered to one
 * instrument client-side) and produces a single headline summary across all
 * accounts that hold it. Per-account math is left to the breakdown table; this
 * module only handles the cross-account roll-up.
 *
 * All inputs are Decimal-as-string from the API. All outputs are
 * Decimal-as-string too, except `unrealized_pct` and `quantity` which are
 * derived numbers — strings are preserved end-to-end where the source value
 * is one (no float drift).
 */
import type { PerfHoldingRow } from "@/components/perf/PerfTable";
import { toDisplayNumber } from "./decimal-strings.ts";

export interface InstrumentAggregate {
  /** Sum of `quantity` across open rows. */
  total_quantity: number;
  /** Weighted average cost per unit (display currency). null when no open quantity. */
  weighted_avg_cost: number | null;
  /** Sum of `quantity * current_price` across open rows (display currency). null when any open row is missing a price. */
  market_value: number | null;
  /** Sum of `quantity * weighted_avg_cost` — the open-position cost basis (display currency). */
  cost_basis: number | null;
  /** market_value - cost_basis (display currency). null when either input is null. */
  unrealized: number | null;
  /** unrealized / cost_basis, as a 0..1 ratio (NOT a percent). null when cost_basis is null/0. */
  unrealized_pct: number | null;
  /** Sum of `realized_eur` across ALL rows (open + closed). Display currency per /api/perf. */
  realized_total: number | null;
  /** Picked from the longest-history row (max `twrr_period_days` among open rows). */
  best_twrr: { value: string | null; period_days: number | null; annualized: boolean; reason: string | null } | null;
  /** Most-recent `current_price_fetched_at` across open rows (for the stale badge). */
  latest_price_fetched_at: string | null;
  /** Open-row count. Drives the "fully closed" badge / empty-state copy. */
  open_count: number;
  /** Closed-row count (status === "closed"). */
  closed_count: number;
}

/**
 * Aggregate per-(account, instrument) perf rows into a single per-instrument
 * summary. Rows are expected to already be filtered to a single instrument_id.
 */
export function aggregateInstrument(rows: PerfHoldingRow[]): InstrumentAggregate {
  const openRows = rows.filter((r) => r.status !== "closed");
  const closedRows = rows.filter((r) => r.status === "closed");

  let total_quantity = 0;
  let weighted_cost_sum = 0; // sum(qty_i * avg_cost_i)
  let market_value = 0;
  let market_value_complete = true; // flips to false the moment a row lacks a price
  let cost_basis_sum = 0;
  let cost_basis_complete = true;

  let latestFetched: string | null = null;

  for (const row of openRows) {
    const qty = toDisplayNumber(row.quantity);
    if (!Number.isFinite(qty) || qty <= 0) continue;
    total_quantity += qty;

    if (row.avg_cost !== null && row.avg_cost !== undefined) {
      const ac = toDisplayNumber(row.avg_cost);
      if (Number.isFinite(ac)) {
        weighted_cost_sum += qty * ac;
        cost_basis_sum += qty * ac;
      } else {
        cost_basis_complete = false;
      }
    } else {
      cost_basis_complete = false;
    }

    // latest_price_fetched_at gates on the row contributing a finite price to
    // market_value, so the timestamp surfaced by the StaleBadge can never
    // outpace the price it claims to describe.
    if (row.current_price !== null && row.current_price !== undefined) {
      const cp = toDisplayNumber(row.current_price);
      if (Number.isFinite(cp)) {
        market_value += qty * cp;
        if (row.current_price_fetched_at) {
          if (latestFetched === null || row.current_price_fetched_at > latestFetched) {
            latestFetched = row.current_price_fetched_at;
          }
        }
      } else {
        market_value_complete = false;
      }
    } else {
      market_value_complete = false;
    }
  }

  const weighted_avg_cost = total_quantity > 0 ? weighted_cost_sum / total_quantity : null;
  const cost_basis = cost_basis_complete && total_quantity > 0 ? cost_basis_sum : null;
  const final_market_value = market_value_complete && total_quantity > 0 ? market_value : null;
  const unrealized =
    final_market_value !== null && cost_basis !== null ? final_market_value - cost_basis : null;
  const unrealized_pct =
    unrealized !== null && cost_basis !== null && cost_basis > 0 ? unrealized / cost_basis : null;

  // Realized P&L includes closed rows — closed lots' realized stays on the record.
  let realized_total: number | null = 0;
  let any_realized = false;
  for (const row of rows) {
    if (row.realized_eur === null || row.realized_eur === undefined) continue;
    const v = toDisplayNumber(row.realized_eur);
    if (Number.isFinite(v)) {
      realized_total = (realized_total ?? 0) + v;
      any_realized = true;
    }
  }
  if (!any_realized) realized_total = null;

  // Best TWRR = the open row with the longest period of valid data.
  // Closed rows are excluded; their TWRR is over a different window (twrr_window_days)
  // and aggregating across mixed windows would be misleading.
  let best_twrr: InstrumentAggregate["best_twrr"] = null;
  for (const row of openRows) {
    if (row.twrr === null) continue;
    const pd = row.twrr_period_days ?? 0;
    if (best_twrr === null || pd > (best_twrr.period_days ?? 0)) {
      best_twrr = {
        value: row.twrr,
        period_days: row.twrr_period_days,
        annualized: row.twrr_annualized,
        reason: row.twrr_reason,
      };
    }
  }
  // Surface "no TWRR yet" with a reason when every open row is suppressed.
  if (best_twrr === null && openRows.length > 0) {
    const firstReason = openRows.find((r) => r.twrr_reason)?.twrr_reason ?? null;
    best_twrr = { value: null, period_days: null, annualized: false, reason: firstReason };
  }

  return {
    total_quantity,
    weighted_avg_cost,
    market_value: final_market_value,
    cost_basis,
    unrealized,
    unrealized_pct,
    realized_total,
    best_twrr,
    latest_price_fetched_at: latestFetched,
    open_count: openRows.length,
    closed_count: closedRows.length,
  };
}
