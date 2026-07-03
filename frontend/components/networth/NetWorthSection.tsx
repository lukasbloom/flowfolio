"use client";

import { useState, type ReactNode } from "react";

import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { InstrumentMultiSelect } from "@/components/ui/instrument-multi-select";
import { TimeframeToggle } from "@/components/ui/timeframe-toggle";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { formatDateRange } from "@/lib/format";
import { useInstrumentFilter } from "@/lib/instrument-filter";
import { usePref } from "@/lib/prefs";
import { useTagFilter } from "@/lib/tag-filter";
import { useCurrency } from "@/lib/currency";

import { NetWorthChart } from "./NetWorthChart";
import { NW_PRESETS, type NwTimeframe } from "./timeframe";

// Three peer toggles, each with its own localStorage key.
// Defaults reproduce today's dashboard view (Tx on, Cost basis off, Yields off).
const NW_SHOW_TRANSACTIONS_KEY = "flowfolio.nwShowTransactions";
const NW_SHOW_COST_BASIS_KEY = "flowfolio.nwShowCostBasis";
const NW_SHOW_YIELDS_KEY = "flowfolio.nwShowYields";
const NW_INSTRUMENT_FILTER_KEY = "flowfolio.instrumentFilter.networth";

export type ChartViewMode = "value" | "price";

interface NetWorthSectionProps {
  /** Optional heading override; defaults to "Net worth". */
  heading?: string;
  /** When set, the chart is scoped to a single instrument's contribution. */
  instrumentId?: string;
  /** ARIA label hook for the section's heading. */
  headingId?: string;
  /**
   * Optional price-mode toggle. When provided, a segmented
   * control appears between "Holding value (€)" and "Price per unit". The
   * parent component renders the price-mode chart by reading the current
   * viewMode via the optional `priceChartSlot` render prop. NetWorthSection
   * still owns timeframe + showTransactions so both modes share controls.
   */
  priceChartSlot?: (args: {
    viewMode: ChartViewMode;
    timeframe: NwTimeframe;
    from: Date | null;
    to: Date | null;
    showTransactions: boolean;
    displayCurrency: "EUR" | "USD";
  }) => ReactNode;
}

/**
 * Shared header + chart bundle: timeframe selector, three peer toggles
 * (Transactions / Cost basis / Yields) each persisted in its own
 * localStorage slot, and the chart itself. Used both on the dashboard
 * (whole-portfolio) and on the instrument detail page (scoped via
 * `instrumentId`).
 *
 * Defaults reproduce today's dashboard look: Transactions
 * on, Cost basis off, Yields off. Toggling Cost basis ON triggers a refetch
 * with `?include_cost_basis=true` and renders the second step line on the
 * chart. Yields is implicitly disabled when Transactions is off (markers
 * don't render at all in that state).
 */
export function NetWorthSection({
  heading = "Net worth",
  instrumentId,
  headingId,
  priceChartSlot,
}: NetWorthSectionProps) {
  const [timeframe, setTimeframe] = useState<NwTimeframe>("1y");
  const [from, setFrom] = useState<Date | null>(null);
  const [to, setTo] = useState<Date | null>(null);
  const [viewMode, setViewMode] = useState<ChartViewMode>("value");
  const { tagFilter } = useTagFilter();
  const { currency: displayCurrency } = useCurrency();

  // Cookie-backed prefs (lib/prefs.tsx): the (app) server
  // layout seeds PrefsProvider from the request cookies, so SSR renders the
  // stored toggle states, no default→stored flash after hydration and no
  // throwaway default-key fetch.
  // Tx defaults on (today's behavior); Cost basis + Yields default off.
  const [txRaw, setTxRaw] = usePref(NW_SHOW_TRANSACTIONS_KEY);
  const [cbRaw, setCbRaw] = usePref(NW_SHOW_COST_BASIS_KEY);
  const [yieldsRaw, setYieldsRaw] = usePref(NW_SHOW_YIELDS_KEY);
  const showTransactions = txRaw !== "0";
  const showCostBasis = cbRaw === "1";
  const showYields = yieldsRaw === "1";

  const setShowTransactions = (next: boolean) => {
    setTxRaw(next ? "1" : "0");
  };
  const setShowCostBasis = (next: boolean) => {
    setCbRaw(next ? "1" : "0");
  };
  const setShowYields = (next: boolean) => {
    setYieldsRaw(next ? "1" : "0");
  };

  const txSwitchId = headingId
    ? `${headingId}-show-transactions`
    : "nw-show-transactions";
  const cbSwitchId = headingId
    ? `${headingId}-show-cost-basis`
    : "nw-show-cost-basis";
  const yieldsSwitchId = headingId
    ? `${headingId}-show-yields`
    : "nw-show-yields";

  // When this section is mounted on the dashboard
  // (no `instrumentId` prop) it owns the per-chart multi-select pill,
  // backed by an independent localStorage slot. On the instrument detail
  // page the parent locks the scope by passing `instrumentId`; the pill
  // is suppressed and the chart is fed `[instrumentId]` directly.
  const [filterIds, setFilterIds] = useInstrumentFilter(NW_INSTRUMENT_FILTER_KEY);
  const showFilterPill = !instrumentId;
  const effectiveIds = instrumentId ? [instrumentId] : filterIds;

  // Price-mode hides cost-basis (semantically replaced by an avg-cost reference
  // line baked into the price chart) and yields (a EUR-denominated concept that
  // doesn't translate to per-unit price). Toggles remain visible so the user
  // can see why they're inert and the persisted preference is preserved.
  const isPriceMode = priceChartSlot !== undefined && viewMode === "price";
  const cbDisabled = isPriceMode;
  const yieldsDisabled = !showTransactions || isPriceMode;

  return (
    <section className="mt-8 space-y-4" aria-labelledby={headingId}>
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-baseline gap-2">
          <h2 id={headingId} className="text-2xl font-semibold leading-tight">
            {heading}
          </h2>
          {timeframe === "custom" && from && to && (
            <span className="text-sm font-normal text-muted-foreground">
              {formatDateRange(from, to)}
            </span>
          )}
        </div>
        <div className="flex flex-nowrap items-center gap-3 overflow-x-auto sm:overflow-visible">
          {priceChartSlot ? (
            <ToggleGroup
              type="single"
              value={viewMode}
              onValueChange={(next) => {
                if (next === "value" || next === "price") setViewMode(next);
              }}
              variant="outline"
              size="sm"
              className="shrink-0"
              aria-label="Chart view"
            >
              <ToggleGroupItem value="value" className="text-xs">
                Holding value
              </ToggleGroupItem>
              <ToggleGroupItem value="price" className="text-xs">
                Price per unit
              </ToggleGroupItem>
            </ToggleGroup>
          ) : null}
          <div className="flex shrink-0 items-center gap-3 min-h-11">
            <div className="flex items-center gap-2">
              <Switch
                id={txSwitchId}
                checked={showTransactions}
                onCheckedChange={setShowTransactions}
                aria-label="Show transaction markers"
              />
              <Label
                htmlFor={txSwitchId}
                className="text-xs text-muted-foreground"
              >
                Transactions
              </Label>
            </div>
            <div
              className={
                "flex items-center gap-2" +
                (cbDisabled ? " opacity-50 pointer-events-none" : "")
              }
              aria-disabled={cbDisabled}
            >
              <Switch
                id={cbSwitchId}
                checked={showCostBasis}
                onCheckedChange={setShowCostBasis}
                aria-label="Show cost basis line"
                disabled={cbDisabled}
              />
              <Label
                htmlFor={cbSwitchId}
                className="text-xs text-muted-foreground"
              >
                Cost basis
              </Label>
            </div>
            {/* When Transactions is off OR we're in price mode, the Yields
                toggle is a no-op. Visually disable so the user sees why it's
                inert; the underlying preference persists. */}
            <div
              className={
                "flex items-center gap-2" +
                (yieldsDisabled ? " opacity-50 pointer-events-none" : "")
              }
              aria-disabled={yieldsDisabled}
            >
              <Switch
                id={yieldsSwitchId}
                checked={showYields}
                onCheckedChange={setShowYields}
                aria-label="Show yield markers"
                disabled={yieldsDisabled}
              />
              <Label
                htmlFor={yieldsSwitchId}
                className="text-xs text-muted-foreground"
              >
                Yields
              </Label>
            </div>
          </div>
          {showFilterPill ? (
            <InstrumentMultiSelect
              value={filterIds}
              onChange={setFilterIds}
            />
          ) : null}
          <TimeframeToggle
            presets={NW_PRESETS}
            value={timeframe}
            onChange={(next) => setTimeframe(next as NwTimeframe)}
            ariaLabel="Net worth timeframe"
            customRange={{
              from,
              to,
              onChange: ({ from: f, to: t }) => {
                setFrom(f);
                setTo(t);
              },
            }}
          />
        </div>
      </div>
      {isPriceMode ? (
        priceChartSlot!({
          viewMode,
          timeframe,
          from,
          to,
          showTransactions,
          displayCurrency,
        })
      ) : (
        <NetWorthChart
          timeframe={timeframe}
          from={from}
          to={to}
          showTransactions={showTransactions}
          showCostBasis={showCostBasis}
          showYields={showYields}
          instrumentIds={effectiveIds}
          tagFilter={tagFilter}
          hasParentBackfill={Boolean(instrumentId)}
        />
      )}
    </section>
  );
}
