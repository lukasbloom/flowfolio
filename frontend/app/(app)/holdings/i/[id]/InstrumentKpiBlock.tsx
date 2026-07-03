"use client";

import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api-client";
import { useCurrency } from "@/lib/currency";
import {
  decimalsFor,
  directionalColor,
  formatMoney,
  formatPercent,
  formatQuantity,
  formatSignedMoney,
} from "@/lib/format";
import { Skeleton } from "@/components/ui/skeleton";
import { StaleBadge } from "@/components/holdings/StaleBadge";
import { TwrrCell } from "@/components/perf/TwrrCell";
import { aggregateInstrument } from "@/lib/instrument-aggregation";
import { useOpenPerf } from "@/hooks/useOpenPerf";
import { cn } from "@/lib/utils";

// 1e-6 is a defensible floor across all instrument types: well above crypto's
// 8dp/sat precision (1e-8) and two orders of magnitude below stocks' 4dp tick.
const PER_UNIT_DIVISOR_FLOOR = 1e-6;

interface Instrument {
  id: string;
  instrument_type: string;
  base_currency: "EUR" | "USD";
  display_decimals: number | null;
}

interface InstrumentKpiBlockProps {
  instrumentId: string;
  instrument: Instrument;
}

interface QuoteResponse {
  price: string;
  currency: "EUR" | "USD";
  fetched_at: string;
}

/**
 * Headline KPI block: aggregated quantity, price, market value, avg cost,
 * unrealized + realized P&L, TWRR. Sources `/api/perf?include_closed=1`
 * (filtered client-side to `instrumentId`) so closed rows still contribute to
 * the realized total.
 *
 * Layout: 2-col on mobile, 4-col on md+. Numeric cells use `tabular-nums` to
 * match PerfTable. Gain/loss colored via the existing palette tokens
 * (`text-positive` / `text-negative`).
 */
export function InstrumentKpiBlock({ instrumentId, instrument }: InstrumentKpiBlockProps) {
  const { currency } = useCurrency();

  // Perf rows in display currency (EUR/USD per user pref). The aggregation
  // produces market value, avg cost, realized in the same display currency.
  const {
    data: perfRows,
    isLoading: perfLoading,
    isError: perfError,
  } = useOpenPerf(currency);

  // Native-currency latest quote so the "Current price" tile can show the
  // instrument's own base-currency price alongside the display-currency
  // market value. Skipped for EUR-base instruments when display is EUR (the
  // perf row's current_price already matches).
  const {
    data: nativeQuote,
  } = useQuery({
    queryKey: ["price-latest", instrumentId],
    queryFn: () => apiFetch<QuoteResponse>(`/api/prices/${instrumentId}/latest`),
    staleTime: 30_000,
    retry: false, // 404 is expected for instruments with no quotes yet
  });

  const aggregate = useMemo(() => {
    if (!perfRows) return null;
    const scoped = perfRows.filter((r) => r.instrument_id === instrumentId);
    return aggregateInstrument(scoped);
  }, [perfRows, instrumentId]);

  if (perfLoading) {
    return (
      <div className="rounded-lg border border-border bg-card p-6">
        <Skeleton className="h-32 w-full" />
      </div>
    );
  }

  if (perfError || !aggregate) {
    return (
      <div className="rounded-lg border border-border bg-card p-6 text-sm text-destructive">
        Could not load performance data for this instrument.
      </div>
    );
  }

  // Fully sold instrument: every row is closed. KPI block still renders
  // realized P&L so the user can see lifetime contribution; quantity-keyed
  // tiles (market value, avg cost, unrealized) show "—".
  const isFullyClosed = aggregate.open_count === 0 && aggregate.closed_count > 0;
  // Empty case — instrument has zero transactions of any kind. Should be rare
  // (the page wouldn't normally be reached) but render gracefully.
  if (aggregate.open_count === 0 && aggregate.closed_count === 0) {
    return (
      <div className="rounded-lg border border-border bg-card p-6 text-sm text-muted-foreground">
        No transactions recorded for this instrument yet.
      </div>
    );
  }

  const decimals = decimalsFor({
    instrumentType: instrument.instrument_type,
    displayDecimals: instrument.display_decimals,
  });

  return (
    <div className="rounded-lg border border-border bg-card p-4 md:p-6">
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4 md:gap-6">
        <Kpi
          label="Quantity held"
          value={
            isFullyClosed
              ? "0"
              : formatQuantity(aggregate.total_quantity, decimals)
          }
          hint={
            aggregate.open_count > 1
              ? `Across ${aggregate.open_count} accounts`
              : aggregate.open_count === 1
                ? "1 account"
                : aggregate.closed_count > 0
                  ? "All positions closed"
                  : undefined
          }
        />

        <Kpi
          label="Current price"
          value={
            nativeQuote
              ? formatMoney(nativeQuote.price, nativeQuote.currency)
              : "—"
          }
          hint={
            nativeQuote
              ? nativeQuote.currency !== currency && aggregate.market_value !== null
                ? `${formatMoney(
                    aggregate.market_value / Math.max(aggregate.total_quantity, PER_UNIT_DIVISOR_FLOOR),
                    currency
                  )} per unit`
                : undefined
              : "No quote yet"
          }
          appendix={
            nativeQuote ? (
              <StaleBadge fetchedAt={nativeQuote.fetched_at} />
            ) : null
          }
        />

        <Kpi
          label="Market value"
          value={
            aggregate.market_value !== null
              ? formatMoney(aggregate.market_value, currency)
              : "—"
          }
          hint={
            aggregate.market_value === null && aggregate.open_count > 0
              ? "Awaiting price"
              : undefined
          }
        />

        <Kpi
          label="Average cost"
          value={
            aggregate.weighted_avg_cost !== null
              ? formatMoney(aggregate.weighted_avg_cost, currency)
              : "—"
          }
          hint={
            aggregate.weighted_avg_cost !== null && aggregate.open_count > 1
              ? "Weighted across accounts"
              : undefined
          }
        />
      </div>

      <div className="mt-4 grid grid-cols-2 gap-4 border-t border-border pt-4 md:mt-6 md:grid-cols-3 md:gap-6 md:pt-6">
        <Kpi
          label="Unrealized P&L"
          value={
            aggregate.unrealized !== null ? (
              <SignedMoney value={aggregate.unrealized} currency={currency} />
            ) : (
              <span className="text-muted-foreground">—</span>
            )
          }
          hint={
            aggregate.unrealized_pct !== null ? (
              <span
                className={cn("tabular-nums", directionalColor(aggregate.unrealized_pct))}
              >
                {formatPercent(aggregate.unrealized_pct, { signed: true })}
              </span>
            ) : undefined
          }
        />

        <Kpi
          label="Realized P&L"
          value={
            aggregate.realized_total !== null ? (
              <SignedMoney value={aggregate.realized_total} currency={currency} />
            ) : (
              <span className="text-muted-foreground">—</span>
            )
          }
          hint="Lifetime, includes closed lots"
        />

        <Kpi
          label={
            aggregate.best_twrr?.annualized ? "TWRR (annualized)" : "TWRR"
          }
          value={
            aggregate.best_twrr ? (
              <TwrrCell value={aggregate.best_twrr.value} />
            ) : (
              <span className="text-muted-foreground">—</span>
            )
          }
          hint={twrrHint(aggregate.best_twrr)}
        />
      </div>
    </div>
  );
}

function Kpi({
  label,
  value,
  hint,
  appendix,
}: {
  label: string;
  value: React.ReactNode;
  hint?: React.ReactNode;
  appendix?: React.ReactNode;
}) {
  return (
    <div>
      <div className="text-xs font-medium text-muted-foreground">{label}</div>
      <div className="mt-1 flex flex-wrap items-baseline gap-1 text-lg font-semibold tabular-nums">
        {value}
        {appendix}
      </div>
      {hint != null ? (
        <div className="mt-0.5 text-xs text-muted-foreground">{hint}</div>
      ) : null}
    </div>
  );
}

function SignedMoney({ value, currency }: { value: number; currency: "EUR" | "USD" }) {
  // formatSignedMoney returns the bare formatted value for 0 (no sign) and
  // directionalColor(0) → text-muted-foreground, matching the prior behavior.
  return (
    <span className={directionalColor(value)}>
      {formatSignedMoney(value, currency)}
    </span>
  );
}

function twrrHint(twrr: { period_days: number | null; reason: string | null } | null): string | undefined {
  if (twrr === null) return undefined;
  if (twrr.reason === "insufficient_history") return "Needs ≥7 days of history";
  if (twrr.reason === "missing_fx") return "Missing FX rate";
  if (twrr.period_days != null && twrr.period_days >= 365) {
    const years = (twrr.period_days / 365).toFixed(1);
    return `Over ${years} year${years === "1.0" ? "" : "s"}`;
  }
  if (twrr.period_days != null && twrr.period_days > 0) {
    return `Over ${twrr.period_days} days`;
  }
  return undefined;
}
