"use client";

import { useMemo, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { format } from "date-fns";
import { ArrowDown, ArrowUp, ArrowUpDown } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { CustomRangeEmpty } from "@/components/charts/CustomRangeEmpty";
import { PercentCell } from "@/components/perf/PercentCell";
import { type PerfTimeframe } from "@/components/perf/timeframe";
import { RealizedCell } from "@/components/perf/RealizedCell";
import { TwrrCell } from "@/components/perf/TwrrCell";
import { StaleBadge } from "@/components/holdings/StaleBadge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableFooter,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { apiFetch } from "@/lib/api-client";
import { useCurrency } from "@/lib/currency";
import { useTagFilter } from "@/lib/tag-filter";
import { decimalsFor, formatMoney, formatQuantity } from "@/lib/format";
import {
  aggregateHoldingsByInstrument,
  type AggregatedHoldingRow,
} from "@/lib/holdings-aggregation";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";

/**
 * PerfHoldingRow is the canonical shape used by PerfTable,
 * ClosedPositionsTable (via mode="closed" + priceCellRenderer), and AllocationDrill
 * (via filterBy). These consumers use this type without re-extending it.
 */
export interface PerfHoldingRow {
  account_id: string;
  account_name: string;
  instrument_id: string;
  instrument_symbol: string;
  instrument_name: string;
  instrument_type: string;
  // Per-instrument override (null → use per-type default
  // from frontend/lib/format.ts:DEFAULT_DECIMALS_BY_TYPE).
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
  // Open/closed discriminator (default-undefined treated as open)
  status?: "open" | "closed";

  // Closed-mode fields (present in /api/closed responses)
  last_close?: string | null;
  last_close_date?: string | null;
  twrr_window_days?: number | null;
}

/**
 * PUBLIC PROP CONTRACT (all four props defined here).
 * AllocationDrill and ClosedPositionsTable consume these
 * props without re-extending PerfTable.
 */
export interface PerfTableProps {
  rows?: PerfHoldingRow[];
  mode?: "open" | "closed";
  priceCellRenderer?: (row: PerfHoldingRow) => ReactNode;
  filterBy?: {
    dimension: "type" | "risk" | "account" | "banked";
    value: string;
  };
  // When true, this PerfTable reads ?excludeClosed=1
  // from the URL and requests /api/perf?include_closed=1 by default (toggle off = closed included).
  // /track does NOT set this; AllocationDrill on /compare does.
  respectExcludeClosed?: boolean;
  // When true, append a totals row to both the desktop table
  // footer and the mobile-card stack. AllocationDrill opts in,
  // default false keeps every other call site (/track, /holdings/active, /holdings/closed
  // via HoldingsTable alias, /holdings/i/[id]) visually unchanged.
  showTotals?: boolean;
  // Timeframe + custom-range dates are now owned by the
  // parent section (PerformanceSection on /track). When provided, PerfTable
  // skips the legacy `?perf=` URL read. `from`/`to` are required only when
  // `timeframe === "custom"`; otherwise they're ignored.
  timeframe?: PerfTimeframe;
  from?: Date | null;
  to?: Date | null;
}

/**
 * PerfTable renders aggregated rows by default. The render
 * body only reads fields the union shares between PerfHoldingRow and
 * AggregatedHoldingRow (everything per-instrument: instrument_*, quantity,
 * avg_cost, current_price, current_price_fetched_at, percent_return,
 * realized_eur, twrr*, status, last_close*, display_decimals). The two
 * account-only fields (account_id / account_name) are gated behind the
 * `"account_id" in row` narrowing — used for React keys and the
 * filterBy.dimension === "account" branch only.
 */
type RenderRow = AggregatedHoldingRow | PerfHoldingRow;

type SortKey =
  | "instrument"
  | "quantity"
  | "avg_cost"
  | "current_price"
  | "percent_return"
  | "realized"
  | "twrr";
type SortDir = "asc" | "desc";

const SORT_LABELS: Record<SortKey, string> = {
  instrument: "Instrument",
  quantity: "Qty",
  avg_cost: "Avg cost",
  current_price: "Current price",
  percent_return: "% return",
  realized: "Realized",
  twrr: "TWRR",
};

function parseTimeframe(value: string | null): PerfTimeframe {
  // Legacy URL fallback for callers that don't pass `timeframe` as a prop
  // (e.g. ClosedPositionsTable, HoldingsTable). "custom" isn't recognised
  // here on purpose — it would require accompanying from/to dates, which
  // the URL no longer persists.
  return value === "1m" || value === "3m" || value === "1y" || value === "all" ? value : "1y";
}

function numericValue(row: RenderRow, key: SortKey): number | null {
  if (key === "instrument") return null;
  if (key === "realized") {
    return row.realized_eur === null ? null : Number(row.realized_eur);
  }
  if (key === "current_price") {
    const v = row.last_close ?? row.current_price;
    return v === null || v === undefined ? null : Number(v);
  }
  const raw = row[key as keyof RenderRow];
  if (raw === null || raw === undefined) return null;
  return Number(raw);
}

function compareRows(
  a: RenderRow,
  b: RenderRow,
  sortKey: SortKey,
  sortDir: SortDir
): number {
  if (sortKey === "instrument") {
    const result = a.instrument_symbol.localeCompare(b.instrument_symbol, "en");
    return sortDir === "asc" ? result : -result;
  }

  const av = numericValue(a, sortKey);
  const bv = numericValue(b, sortKey);
  // Null sorts to bottom regardless of direction
  if (av === null && bv === null) return 0;
  if (av === null) return 1;
  if (bv === null) return -1;

  const result = av - bv;
  return sortDir === "asc" ? result : -result;
}

function SortIcon({ active, dir }: { active: boolean; dir: SortDir }) {
  if (active) {
    const Icon = dir === "asc" ? ArrowUp : ArrowDown;
    return <Icon className="size-3 text-foreground" aria-hidden="true" />;
  }
  return (
    <ArrowUpDown
      className="size-3 text-muted-foreground opacity-0 group-hover/header:opacity-100"
      aria-hidden="true"
    />
  );
}

function HeaderButton({
  sortKey,
  activeKey,
  sortDir,
  onSort,
  align = "left",
  children,
}: {
  sortKey: SortKey;
  activeKey: SortKey;
  sortDir: SortDir;
  onSort: (key: SortKey) => void;
  align?: "left" | "right";
  children: ReactNode;
}) {
  const active = sortKey === activeKey;
  return (
    <button
      type="button"
      className={cn(
        "group/header inline-flex w-full items-center gap-1 text-xs font-medium",
        align === "right" && "justify-end"
      )}
      onClick={() => onSort(sortKey)}
      aria-pressed={active}
    >
      <span>{children}</span>
      <SortIcon active={active} dir={sortDir} />
    </button>
  );
}

function LoadingRows() {
  return (
    <div className="space-y-2">
      {[0, 1, 2].map((i) => (
        <Skeleton key={i} className="h-10 w-full" />
      ))}
    </div>
  );
}

/**
 * Apply client-side filter based on filterBy prop.
 */
function applyFilterBy(
  rows: PerfHoldingRow[],
  filterBy: PerfTableProps["filterBy"]
): PerfHoldingRow[] {
  if (!filterBy) return rows;
  return rows.filter((row) => {
    switch (filterBy.dimension) {
      // AllocationPie now humanizes the by-type slice labels
      // ("Stock" / "ETF" / …) while the backend row payload keeps
      // canonical lowercase enums ("stock" / "etf"). Compare
      // case-insensitively so the drill click still matches.
      case "type":
        return row.instrument_type.toLowerCase() === filterBy.value.toLowerCase();
      case "risk":
        return (
          (row.risk_level ?? "").toLowerCase() === filterBy.value.toLowerCase()
        );
      case "account":
        return row.account_name === filterBy.value;
      case "banked":
        return (row.is_banked ? "Banked" : "Non-banked") === filterBy.value;
      default:
        return true;
    }
  });
}

export function PerfTable({
  rows: rowsProp,
  mode = "open",
  priceCellRenderer,
  filterBy,
  respectExcludeClosed = false,
  showTotals = false,
  timeframe: timeframeProp,
  from = null,
  to = null,
}: PerfTableProps) {
  const { currency } = useCurrency();
  const { tagFilter } = useTagFilter();
  const searchParams = useSearchParams();
  // When the parent provides `timeframe`, it owns the value
  // (PerformanceSection on /track). Otherwise fall back to the legacy ?perf=
  // URL param for callers that haven't migrated (ClosedPositionsTable,
  // HoldingsTable). `?perf=` URL persistence is intentionally dropped on the
  // dashboard, bookmarked /track?perf=3m links revert to default 1y.
  const timeframe: PerfTimeframe =
    timeframeProp ?? parseTimeframe(searchParams.get("perf"));

  // includeClosed is true when respectExcludeClosed is on AND toggle is OFF
  // (closed-by-default when toggle is absent; URL ?excludeClosed=1 opts out).
  const excludeClosedParam = searchParams.get("excludeClosed") === "1";
  const includeClosed = mode === "open" && respectExcludeClosed && !excludeClosedParam;
  const [sortKey, setSortKey] = useState<SortKey>("twrr");
  const [sortDir, setSortDir] = useState<SortDir>("desc");

  // When timeframe is "custom" but dates aren't picked yet,
  // skip the network call and surface the dedicated empty state below. The
  // backend would 422 a `?timeframe=custom` without from/to, and pre-issuing
  // that request just to render an error is wasteful.
  const customDatesReady = timeframe !== "custom" || (from !== null && to !== null);

  const apiPath = mode === "closed" ? "/api/closed" : "/api/perf";
  const fromIso = from ? format(from, "yyyy-MM-dd") : null;
  const toIso = to ? format(to, "yyyy-MM-dd") : null;
  const customQuery =
    timeframe === "custom" && fromIso && toIso
      ? `&from=${fromIso}&to=${toIso}`
      : "";
  // /api/closed used to ignore `timeframe`. It now accepts the
  // same shape as /api/perf. Both modes now forward timeframe when "custom"
  // is in play (and presets when mode==="open"). Closed-mode preset requests
  // omit the timeframe to preserve the previous wire shape for any
  // unmigrated callers that hit /api/closed.
  const timeframeQuery =
    mode === "open" || timeframe === "custom" ? `&timeframe=${timeframe}` : "";
  const url = `${apiPath}?currency=${currency}${timeframeQuery}${customQuery}${tagFilter ? `&tag=${encodeURIComponent(tagFilter)}` : ""}${includeClosed ? "&include_closed=1" : ""}`;

  const { data: fetchedData, isLoading, isError } = useQuery({
    queryKey: [
      "perf",
      mode,
      timeframe,
      currency,
      tagFilter,
      includeClosed,
      fromIso,
      toIso,
    ],
    queryFn: () => apiFetch<PerfHoldingRow[]>(url),
    enabled: rowsProp === undefined && customDatesReady,
    staleTime: 30_000,
  });

  const rawData = rowsProp ?? fetchedData;

  const filteredData = useMemo(
    () => applyFilterBy(rawData ?? [], filterBy),
    [rawData, filterBy]
  );

  // Aggregate cross-account rows by instrument_id by default.
  // Carve-out: when the caller drills the "By account" pie on /compare,
  // filterBy.dimension === "account", aggregating then would collapse the very
  // cut the user just clicked. In that one case alone, keep raw
  // per-(account, instrument) rows. All other drills + the bare /track and
  // /holdings/active surfaces aggregate.
  const displayRows: RenderRow[] = useMemo(() => {
    if (filterBy?.dimension === "account") return filteredData;
    return aggregateHoldingsByInstrument(filteredData);
  }, [filteredData, filterBy]);

  const sortedRows = useMemo(
    () => [...displayRows].sort((a, b) => compareRows(a, b, sortKey, sortDir)),
    [displayRows, sortDir, sortKey]
  );

  // Slice-level totals row, gated by `showTotals`.
  // (a) Price falls back to `last_close` for closed rows surfaced through the
  //     unified /api/perf?include_closed=1 response — mirrors renderPriceCell.
  // (b) Quantity / Avg-cost / TWRR cells render em-dash because cross-instrument
  //     quantities and per-instrument time windows are not naively combinable.
  // (c) % return is a WEIGHTED return = (Σ market - Σ cost) / Σ cost, NOT a
  //     mean-of-per-row-percents (the latter would over-weight tiny positions).
  // (d) Only AllocationDrill opts in today; every other call site keeps
  //     showTotals=false and renders byte-identically.
  const totals = useMemo(() => {
    if (!showTotals || sortedRows.length === 0) return null;
    let market_value = 0;
    let cost_basis = 0;
    let realized = 0;
    let mv_complete = true;
    let cb_complete = true;
    let any_realized = false;
    for (const r of sortedRows) {
      const q = Number(r.quantity);
      const priceRaw = r.current_price ?? r.last_close ?? null;
      if (Number.isFinite(q) && priceRaw !== null && priceRaw !== undefined) {
        const p = Number(priceRaw);
        if (Number.isFinite(p)) {
          market_value += q * p;
        } else {
          mv_complete = false;
        }
      } else {
        mv_complete = false;
      }
      if (Number.isFinite(q) && r.avg_cost !== null && r.avg_cost !== undefined) {
        const ac = Number(r.avg_cost);
        if (Number.isFinite(ac)) {
          cost_basis += q * ac;
        } else {
          cb_complete = false;
        }
      } else {
        cb_complete = false;
      }
      if (r.realized_eur !== null && r.realized_eur !== undefined) {
        const rz = Number(r.realized_eur);
        if (Number.isFinite(rz)) {
          realized += rz;
          any_realized = true;
        }
      }
    }
    const pct: string | null =
      mv_complete && cb_complete && cost_basis > 0
        ? String((market_value - cost_basis) / cost_basis)
        : null;
    return {
      market_value: mv_complete ? market_value : null,
      cost_basis: cb_complete ? cost_basis : null,
      realized: any_realized ? realized : null,
      percent_return: pct,
    };
  }, [showTotals, sortedRows]);

  function toggleSort(nextKey: SortKey) {
    if (nextKey === sortKey) {
      setSortDir((dir) => (dir === "asc" ? "desc" : "asc"));
      return;
    }
    setSortKey(nextKey);
    setSortDir(nextKey === "instrument" ? "asc" : "desc");
  }

  // Custom timeframe selected but no range picked → show
  // the dedicated empty state (no network call was issued; see `enabled` guard
  // on the useQuery above).
  if (rowsProp === undefined && timeframe === "custom" && !customDatesReady) {
    return <CustomRangeEmpty />;
  }
  if (rowsProp === undefined && isLoading) return <LoadingRows />;
  if (rowsProp === undefined && isError) {
    return (
      <p className="text-sm text-destructive">
        Could not load performance rows. Check the backend connection and try again.
      </p>
    );
  }
  if (sortedRows.length === 0) {
    return (
      <div className="rounded-lg border border-border bg-card p-6 text-center">
        <h2 className="text-base font-semibold">No performance data yet</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Add transactions and price history to compare holding performance.
        </p>
      </div>
    );
  }

  // TWRR header label for closed mode
  const twrrHeader = (() => {
    if (mode === "closed") {
      const allAnnualized =
        sortedRows.length > 0 &&
        sortedRows.every((r) => (r.twrr_window_days ?? 0) >= 365);
      return allAnnualized ? "TWRR (annualized)" : "TWRR (hold window)";
    }
    return timeframe === "1y" || timeframe === "all" ? "TWRR (annualized)" : "Period return";
  })();

  const priceHeader = mode === "closed" ? "Last close" : "Current price";

  // Render the price cell: use priceCellRenderer if provided, else default by mode.
  // priceCellRenderer is supplied by callers (e.g. ClosedPositionsTable) that
  // pre-pass raw PerfHoldingRow[] via the `rows` prop — and `rows` callers are
  // exactly the surfaces that never aggregate (filterBy is either undefined or
  // for the account-drill carve-out). So the cast back to PerfHoldingRow inside
  // the priceCellRenderer branch is safe at runtime.
  function renderPriceCell(row: RenderRow): ReactNode {
    if (priceCellRenderer) return priceCellRenderer(row as PerfHoldingRow);
    // Per-row closed check for the unified /api/perf?include_closed=1 surface.
    if (row.status === "closed") {
      return (
        <span className="text-muted-foreground tabular-nums">—</span>
      );
    }
    if (mode === "closed") {
      return (
        <span className="tabular-nums">
          {row.last_close === null || row.last_close === undefined
            ? "—"
            : formatMoney(row.last_close, currency)}
        </span>
      );
    }
    return (
      <>
        <span className="tabular-nums">
          {row.current_price === null ? "—" : formatMoney(row.current_price, currency)}
        </span>
        <StaleBadge fetchedAt={row.current_price_fetched_at} />
      </>
    );
  }

  return (
    <>
      <div className="hidden md:block">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>
                <HeaderButton
                  sortKey="instrument"
                  activeKey={sortKey}
                  sortDir={sortDir}
                  onSort={toggleSort}
                >
                  Instrument
                </HeaderButton>
              </TableHead>
              <TableHead className="text-right">
                <HeaderButton
                  sortKey="quantity"
                  activeKey={sortKey}
                  sortDir={sortDir}
                  onSort={toggleSort}
                  align="right"
                >
                  Qty
                </HeaderButton>
              </TableHead>
              <TableHead className="text-right">
                <HeaderButton
                  sortKey="avg_cost"
                  activeKey={sortKey}
                  sortDir={sortDir}
                  onSort={toggleSort}
                  align="right"
                >
                  Avg cost
                </HeaderButton>
              </TableHead>
              <TableHead className="text-right">
                <HeaderButton
                  sortKey="current_price"
                  activeKey={sortKey}
                  sortDir={sortDir}
                  onSort={toggleSort}
                  align="right"
                >
                  {priceHeader}
                </HeaderButton>
              </TableHead>
              <TableHead className="text-right">
                <HeaderButton
                  sortKey="percent_return"
                  activeKey={sortKey}
                  sortDir={sortDir}
                  onSort={toggleSort}
                  align="right"
                >
                  % return
                </HeaderButton>
              </TableHead>
              <TableHead className="text-right">
                <HeaderButton
                  sortKey="realized"
                  activeKey={sortKey}
                  sortDir={sortDir}
                  onSort={toggleSort}
                  align="right"
                >
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <span>Realized</span>
                    </TooltipTrigger>
                    <TooltipContent>
                      Lifetime realized gain. Computed from FIFO-matched disposals (sells and
                      spends).
                    </TooltipContent>
                  </Tooltip>
                </HeaderButton>
              </TableHead>
              <TableHead className="text-right">
                <HeaderButton
                  sortKey="twrr"
                  activeKey={sortKey}
                  sortDir={sortDir}
                  onSort={toggleSort}
                  align="right"
                >
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <span>{twrrHeader}</span>
                    </TooltipTrigger>
                    <TooltipContent>
                      Time-Weighted Return — performance independent of cash-flow timing, expressed annualized.
                    </TooltipContent>
                  </Tooltip>
                </HeaderButton>
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {sortedRows.map((row) => (
              <TableRow
                key={
                  "account_id" in row && row.account_id
                    ? `${row.account_id}::${row.instrument_id}`
                    : row.instrument_id
                }
                className={cn(row.status === "closed" && "text-muted-foreground")}
              >
                <TableCell>
                  <Link
                    href={`/holdings/i/${row.instrument_id}`}
                    className="font-semibold hover:underline"
                  >
                    {row.instrument_symbol}
                  </Link>
                  {row.status === "closed" && (
                    <Badge variant="secondary" className="ml-2">Closed</Badge>
                  )}
                  <div className="max-w-[18rem] truncate text-xs text-muted-foreground">
                    {row.instrument_name}
                  </div>
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {formatQuantity(row.quantity, decimalsFor({ instrumentType: row.instrument_type, displayDecimals: row.display_decimals }))}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {row.avg_cost === null ? "—" : formatMoney(row.avg_cost, currency)}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {renderPriceCell(row)}
                </TableCell>
                <TableCell className="text-right">
                  <PercentCell value={row.percent_return} />
                </TableCell>
                <TableCell className="text-right">
                  <RealizedCell value={row.realized_eur} currency={currency} />
                </TableCell>
                <TableCell className="text-right">
                  <TwrrCell value={row.twrr} />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
          {showTotals && totals && (
            <TableFooter>
              <TableRow>
                <TableCell className="font-semibold">Total</TableCell>
                <TableCell className="text-right tabular-nums text-muted-foreground">—</TableCell>
                {/* Avg-cost column carries Σ cost basis in the totals row (per-unit → total overload, same pattern as Excel SUM rows). */}
                <TableCell className="text-right tabular-nums">
                  {totals.cost_basis === null ? "—" : formatMoney(totals.cost_basis, currency)}
                </TableCell>
                {/* Current price / Last close column carries Σ market value in the totals row. */}
                <TableCell className="text-right tabular-nums">
                  {totals.market_value === null ? "—" : formatMoney(totals.market_value, currency)}
                </TableCell>
                <TableCell className="text-right">
                  {totals.percent_return === null ? (
                    <span className="text-muted-foreground">—</span>
                  ) : (
                    <PercentCell value={totals.percent_return} />
                  )}
                </TableCell>
                <TableCell className="text-right">
                  {totals.realized === null ? (
                    <span className="text-muted-foreground">—</span>
                  ) : (
                    <RealizedCell value={String(totals.realized)} currency={currency} />
                  )}
                </TableCell>
                <TableCell className="text-right tabular-nums text-muted-foreground">—</TableCell>
              </TableRow>
            </TableFooter>
          )}
        </Table>
      </div>

      <div className="space-y-3 md:hidden">
        <label
          className="block text-xs font-medium text-muted-foreground"
          htmlFor="perf-mobile-sort"
        >
          Sort
        </label>
        <select
          id="perf-mobile-sort"
          className="min-h-11 w-full rounded-md border border-border bg-background px-3 text-sm"
          value={`${sortKey}:${sortDir}`}
          onChange={(event) => {
            const [nextKey, nextDir] = event.target.value.split(":") as [SortKey, SortDir];
            setSortKey(nextKey);
            setSortDir(nextDir);
          }}
        >
          <option value="twrr:desc">Sort: {SORT_LABELS.twrr} ↓</option>
          <option value="twrr:asc">Sort: {SORT_LABELS.twrr} ↑</option>
          <option value="percent_return:desc">Sort: {SORT_LABELS.percent_return} ↓</option>
          <option value="realized:desc">Sort: {SORT_LABELS.realized} ↓</option>
          <option value="avg_cost:desc">Sort: {SORT_LABELS.avg_cost} ↓</option>
          <option value="instrument:asc">Sort: Name A-Z</option>
        </select>

        {sortedRows.map((row) => (
          <div
            key={
              "account_id" in row && row.account_id
                ? `${row.account_id}::${row.instrument_id}`
                : row.instrument_id
            }
            className={cn("rounded-lg border border-border bg-card p-4", row.status === "closed" && "text-muted-foreground")}
          >
            <div className="flex min-w-0 items-baseline justify-between gap-3 text-base font-semibold">
              <div className="flex min-w-0 items-center gap-2">
                <Link
                  href={`/holdings/i/${row.instrument_id}`}
                  className="min-w-0 truncate hover:underline"
                >
                  {row.instrument_symbol}
                </Link>
                {row.status === "closed" && (
                  <Badge variant="secondary">Closed</Badge>
                )}
              </div>
              <PercentCell value={row.percent_return} className="shrink-0" />
            </div>
            <div className="mt-1 truncate text-xs text-muted-foreground">
              {row.instrument_name}
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-x-1 gap-y-1 text-xs text-muted-foreground">
              <span className="tabular-nums">{formatQuantity(row.quantity, decimalsFor({ instrumentType: row.instrument_type, displayDecimals: row.display_decimals }))}</span>
              <span>@</span>
              <span className="tabular-nums">
                {row.avg_cost === null ? "—" : formatMoney(row.avg_cost, currency)}
              </span>
              <span>→</span>
              {row.status === "closed" ? (
                <span className="tabular-nums">—</span>
              ) : mode === "closed" ? (
                <span className="tabular-nums">
                  {row.last_close === null || row.last_close === undefined
                    ? "—"
                    : formatMoney(row.last_close, currency)}
                </span>
              ) : (
                <>
                  <span className="tabular-nums">
                    {row.current_price === null ? "—" : formatMoney(row.current_price, currency)}
                  </span>
                  <StaleBadge fetchedAt={row.current_price_fetched_at} />
                </>
              )}
            </div>
            <div className="mt-2 text-sm">
              <span className="text-muted-foreground">Realized: </span>
              <RealizedCell value={row.realized_eur} currency={currency} />
            </div>
            <div className="mt-1 text-sm">
              <span className="text-muted-foreground">{twrrHeader}: </span>
              <TwrrCell value={row.twrr} />
            </div>
          </div>
        ))}

        {showTotals && totals && (
          <div className="rounded-lg border border-border bg-muted/40 p-4 font-semibold">
            <div className="flex items-baseline justify-between gap-3 text-base">
              <span>Total</span>
              {totals.percent_return === null ? (
                <span className="text-muted-foreground">—</span>
              ) : (
                <PercentCell value={totals.percent_return} className="shrink-0" />
              )}
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
              <span>Cost basis:</span>
              <span className="tabular-nums">
                {totals.cost_basis === null ? "—" : formatMoney(totals.cost_basis, currency)}
              </span>
              <span>·</span>
              <span>Market value:</span>
              <span className="tabular-nums">
                {totals.market_value === null ? "—" : formatMoney(totals.market_value, currency)}
              </span>
            </div>
            <div className="mt-2 text-sm">
              <span className="text-muted-foreground">Realized: </span>
              {totals.realized === null ? (
                <span className="text-muted-foreground">—</span>
              ) : (
                <RealizedCell value={String(totals.realized)} currency={currency} />
              )}
            </div>
          </div>
        )}
      </div>
    </>
  );
}
