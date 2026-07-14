"use client";

import { useMemo } from "react";
import { useQuery, keepPreviousData } from "@tanstack/react-query";
import { format as formatDate } from "date-fns";
import { enGB } from "date-fns/locale";
import * as echarts from "echarts/core";
import { LineChart } from "echarts/charts";
import {
  GridComponent,
  TooltipComponent,
  MarkPointComponent,
  LegendComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import ReactECharts from "echarts-for-react";

import Link from "next/link";
import { AlertTriangle } from "lucide-react";

import { apiFetch } from "@/lib/api-client";
import { useCurrency } from "@/lib/currency";
import { decimalsFor, formatCompactMoney, formatMoney, formatQuantity } from "@/lib/format";
import { ChartSkeleton } from "@/components/charts/ChartSkeleton";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import type { NwTimeframe } from "@/components/networth/timeframe";
import { CustomRangeEmpty } from "@/components/charts/CustomRangeEmpty";
import { ACCENT, LINE_PALETTE, MUTED, NEGATIVE, POSITIVE } from "@/components/charts/palette";
import { escapeHtml, timeXAxis, toIsoDate, tooltipShell, valueYAxis } from "@/lib/chart-utils";

interface InstrumentLite {
  id: string;
  symbol: string;
}

echarts.use([
  LineChart,
  GridComponent,
  TooltipComponent,
  MarkPointComponent,
  LegendComponent,
  CanvasRenderer,
]);

interface NetWorthPoint {
  date: string;
  value: string;
}

interface NetWorthMarker {
  date: string;
  type: "buy" | "sell" | "yield";
  instrument_id: string | null;
  instrument_symbol: string | null;
  // Per-marker context so the tooltip can format
  // quantity at the right precision. Aggregate markers may have both
  // null and the formatter falls back to the 8-decimal legacy default.
  instrument_type?: string | null;
  display_decimals?: number | null;
  quantity: string | null;
  // Already converted to the chart's display currency by the backend.
  value: string;
  count: number;
}

interface NetWorthResponse {
  points: NetWorthPoint[];
  markers: NetWorthMarker[];
  aggregation: string;
  warnings: string[];
  // Optional cost-basis series populated only when the
  // request was made with `?include_cost_basis=true`. Falsy / empty means
  // the chart skips the second series entirely.
  cost_basis_series?: NetWorthPoint[];
}

interface NetWorthChartProps {
  timeframe: NwTimeframe;
  from: Date | null;
  to: Date | null;
  // Three independent toggles. Defaults reproduce
  // today's dashboard look (Tx on, Cost basis off, Yields off).
  showTransactions: boolean;
  showCostBasis: boolean;
  showYields: boolean;
  /**
   * Instrument scope for the request. Empty array (the default) means the
   * full portfolio; one or more ids narrow the chart to their summed
   * contribution. NetWorthSection owns the user-facing multi-select pill
   * and feeds the result down via this prop.
   */
  instrumentIds?: string[];
  /**
   * Active global tag filter (or null when no filter is set). Forwarded to
   * /api/networth as `?tag=<name>` so both the value and cost-basis series
   * narrow to the tagged subset.
   */
  tagFilter?: string | null;
  /**
   * `true` when the chart is rendered inside a per-instrument detail page
   * (the page owns its own Backfill button and the multi-select pill is
   * suppressed). Drives the "missing price" hint copy only.
   */
  hasParentBackfill?: boolean;
}

function buildQueryUrl(
  timeframe: NwTimeframe,
  currency: string,
  from: Date | null,
  to: Date | null,
  instrumentIds: string[],
  includeCostBasis: boolean,
  tagFilter: string | null
): string {
  // Use URLSearchParams so each `instrument_id=<uuid>` lands as its own
  // repeated key — FastAPI relies on the repeated form, NOT a comma-joined
  // value, to populate `list[str] = Query(default_factory=list)`.
  const params = new URLSearchParams();
  params.set("timeframe", timeframe);
  params.set("currency", currency);
  if (timeframe === "custom" && from && to) {
    params.set("from", toIsoDate(from));
    params.set("to", toIsoDate(to));
  }
  for (const id of instrumentIds) {
    params.append("instrument_id", id);
  }
  // Only append when the toggle is on / a tag is set so
  // the URL — and therefore the TanStack Query key — stays stable for the
  // default no-cost-basis / no-tag case (no spurious refetch on mount).
  if (includeCostBasis) {
    params.set("include_cost_basis", "true");
  }
  if (tagFilter) {
    params.set("tag", tagFilter);
  }
  return `/api/networth?${params.toString()}`;
}

export function NetWorthChart({
  timeframe,
  from,
  to,
  showTransactions,
  showCostBasis,
  showYields,
  instrumentIds,
  tagFilter = null,
  hasParentBackfill = false,
}: NetWorthChartProps) {
  const { currency } = useCurrency();

  // Sort the ids before they enter the cache key + URL so toggling the
  // same N instruments in any order produces the same TanStack Query key
  // and dedupes the request.
  const stableIds = useMemo(
    () => (instrumentIds ? [...instrumentIds].sort() : []),
    [instrumentIds]
  );

  // Used on the dashboard view to map warning instrument_ids → symbols →
  // links AND to render the multi-select pill. Reuses the shared
  // "instruments" cache key so it piggybacks on data the rest of the app
  // already has. (Always enabled — the pill needs the symbol map even when
  // a single id is selected so the trigger label can show its symbol.)
  const { data: instruments } = useQuery<InstrumentLite[]>({
    queryKey: ["instruments"],
    queryFn: () => apiFetch<InstrumentLite[]>("/api/instruments"),
    enabled: !hasParentBackfill,
    staleTime: 60_000,
  });

  const { data, isLoading, isError, error, refetch } = useQuery<NetWorthResponse>({
    // Use the same local YYYY-MM-DD that buildQueryUrl emits, so the
    // cache key uniquely identifies a request regardless of timezone offset
    // (toISOString() is UTC and can shift the date near midnight in CET/CEST).
    // Include the (sorted) instrument id list so changing
    // the multi-select selection refetches.
    // Include the cost-basis flag and tag filter so
    // toggling either changes the cache key and triggers a refetch.
    queryKey: [
      "networth",
      {
        timeframe,
        currency,
        from: from ? toIsoDate(from) : null,
        to: to ? toIsoDate(to) : null,
        instrumentIds: stableIds,
        includeCostBasis: showCostBasis,
        tagFilter,
      },
    ],
    queryFn: () =>
      apiFetch<NetWorthResponse>(
        buildQueryUrl(
          timeframe,
          currency,
          from,
          to,
          stableIds,
          showCostBasis,
          tagFilter
        )
      ),
    enabled: timeframe !== "custom" || (!!from && !!to),
    // Whenever a pref in the query key changes mid-session (currency chip,
    // cost-basis toggle, tag/instrument filter, timeframe), keepPreviousData
    // keeps the prior response visible during the refetch instead of dropping
    // `data` to undefined — no <Skeleton> flash, and ECharts performs a smooth
    // in-place transition (notMerge=false + lazyUpdate) rather than clearing
    // the canvas and restarting its entry animation. (Originally added for the
    // post-mount localStorage hydration key flip, which is gone now that prefs are
    // cookie-backed and SSR-correct via lib/prefs.tsx, but the mid-session
    // smoothing is worth keeping.)
    placeholderData: keepPreviousData,
  });

  const option = useMemo(() => {
    if (!data) return null;

    const lineData = data.points.map((p) => [p.date, Number(p.value)]);

    // Build a cost-basis lookup table mirroring the
    // pattern used by the deleted CostBasisOverlay component. Used by the
    // tooltip formatter to add Cost basis + Gap rows when the toggle is on.
    const hasCostBasis =
      showCostBasis &&
      data.cost_basis_series != null &&
      data.cost_basis_series.length > 0;
    const costBasisLineData = hasCostBasis
      ? data.cost_basis_series!.map((p) => [p.date, Number(p.value)])
      : [];
    const costBasisByDate = new Map<string, number>();
    if (hasCostBasis) {
      for (const p of data.cost_basis_series!) {
        costBasisByDate.set(p.date, Number(p.value));
      }
    }
    const valueByDate = new Map<string, number>(
      data.points.map((p) => [p.date, Number(p.value)])
    );

    // Markers are rendered ONLY when Transactions toggle
    // is on. Yields filter is then applied as a sub-toggle. When Tx is off
    // we keep the markPoint key in the option (with empty data) so the
    // option shape stays stable across toggles — avoids ECharts re-init churn.
    const baseMarkers = showTransactions
      ? showYields
        ? data.markers
        : data.markers.filter((m) => m.type !== "yield")
      : [];
    const visibleMarkers = baseMarkers;

    // Group markers by date for tooltip merging on axis trigger.
    const markersByDate = new Map<string, NetWorthMarker[]>();
    for (const m of visibleMarkers) {
      const list = markersByDate.get(m.date) ?? [];
      list.push(m);
      markersByDate.set(m.date, list);
    }

    const markPoints = visibleMarkers.map((m) => {
      const point = data.points.find((p) => p.date === m.date);
      const yValue = point ? Number(point.value) : 0;
      if (m.type === "buy") {
        return {
          name: "buy",
          coord: [m.date, yValue],
          symbol: "rect",
          symbolSize: 12,
          itemStyle: { color: POSITIVE },
        };
      }
      if (m.type === "sell") {
        return {
          name: "sell",
          coord: [m.date, yValue],
          symbol: "rect",
          symbolSize: 12,
          itemStyle: {
            color: "#FAFAFA",
            borderColor: NEGATIVE,
            borderWidth: 2,
          },
        };
      }
      // yield rollup: muted dot
      return {
        name: "yield",
        coord: [m.date, yValue],
        symbol: "circle",
        symbolSize: 10,
        itemStyle: { color: MUTED },
      };
    });

    // With timeframe="all" the time axis can emit several ticks per
    // month when the daily-granularity series is long. Letting ECharts decide
    // tick density via minInterval (28 days for "all") guarantees at most one
    // tick per month so labels never duplicate. hideOverlap mops up any
    // remaining collisions deterministically. The formatter stays stateless so
    // re-renders triggered by hover/resize/dataZoom can't drop a leading label.
    const xAxisLabelFormatter = (val: number) => {
      const d = new Date(val);
      return timeframe === "all"
        ? formatDate(d, "MMMM yyyy", { locale: enGB })
        : formatDate(d, "dd MMMM", { locale: enGB });
    };
    const xAxisMinInterval =
      timeframe === "all" ? 28 * 24 * 60 * 60 * 1000 : undefined;

    return {
      backgroundColor: "transparent",
      animation: true,
      // When the cost-basis line is on, surface a small
      // legend (top-left) so the user can tell the two series apart, same
      // affordance the deleted CostBasisOverlay used. Hidden otherwise to
      // keep the dashboard view clean (single line, no chrome).
      // Legend placed at top-center so it sits above the plot area instead
      // of floating inside the y-axis gutter (where `left: 0` previously
      // pinned it, making the empty column on the left look unintentional).
      legend: hasCostBasis
        ? {
            show: true,
            top: 0,
            left: "center",
            icon: "rect",
            itemWidth: 14,
            itemHeight: 4,
            textStyle: { fontSize: 12, color: ACCENT },
            data: ["Cost basis", "Portfolio value"],
          }
        : { show: false },
      textStyle: {
        fontFamily: "Inter, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif",
        fontSize: 12,
        color: MUTED,
      },
      // An explicit gutter was tuned for EUR ("1 mil €" ~50px) but
      // Spanish-locale USD compact notation produces "1 mil US$" (~70px) which
      // gets clipped. `containLabel: true` lets ECharts measure the rendered
      // axisLabel and reserve exactly the space it needs *within* the grid
      // bounds, so combining it with a tight explicit `left: 12` keeps the
      // gutter snug (no wasted column) without ever clipping a label,
      // regardless of currency or compact-notation width.
      // Bump top padding when the legend is visible so it doesn't collide
      // with the chart line. 24 → 32 mirrors the value used by the
      // legacy CostBasisOverlay.
      grid: { left: 12, right: 12, top: hasCostBasis ? 32 : 24, bottom: 32, containLabel: true },
      xAxis: timeXAxis({ minInterval: xAxisMinInterval, formatter: xAxisLabelFormatter }),
      yAxis: {
        ...valueYAxis((val: number) => formatCompactMoney(val, currency)),
        position: "left",
        // Auto-fit to the data range instead of anchoring at 0,
        // so the portfolio line uses the full plot area (intra-period changes
        // become visually legible even when the portfolio value is, say, ~50k–60k
        // and would otherwise be squashed into the top sliver of a 0-based axis).
        scale: true,
      },
      tooltip: {
        ...tooltipShell("axis"),
        axisPointer: { type: "line", lineStyle: { color: MUTED, type: "dashed" } },
        // Build tooltip from trusted enum/type fields only (no raw notes)
        // to mitigate tooltip XSS.
        formatter: (params: unknown) => {
          const arr = Array.isArray(params) ? params : [params];
          // With two series the params array is now
          // [valueParam, costBasisParam] (or just one when cost basis is
          // off). Find the axis date from the first param — both series
          // share the same x-axis bucket so either entry works.
          const first = arr[0] as { axisValueLabel?: string; value?: [string, number] } | undefined;
          if (!first) return "";
          const isoDate =
            (Array.isArray(first.value) && typeof first.value[0] === "string"
              ? first.value[0]
              : null) ?? "";
          const dateLabel = isoDate
            ? formatDate(new Date(isoDate), "dd MMMM yyyy", { locale: enGB })
            : escapeHtml(first.axisValueLabel ?? "");
          // Don't trust series order, look up the
          // value-line number by date directly (same lookup we use to
          // place markers on the line).
          const portVal = valueByDate.get(isoDate);
          const total =
            portVal != null
              ? formatMoney(portVal, currency)
              : Array.isArray(first.value) && typeof first.value[1] === "number"
                ? formatMoney(first.value[1], currency)
                : "";

          // When the cost-basis line is on, surface
          // Cost basis + Gap rows above the marker list, mirrors the
          // tooltip from the deleted CostBasisOverlay so muscle-memory
          // transfers from /analytics → /.
          let costBasisRows = "";
          if (hasCostBasis) {
            const costVal = costBasisByDate.get(isoDate);
            const gap = portVal != null && costVal != null ? portVal - costVal : null;
            const costStr = costVal != null ? formatMoney(costVal, currency) : "—";
            const gapStr = gap != null ? formatMoney(gap, currency) : "—";
            costBasisRows = [
              `<div style="margin-top:4px;font-size:14px;color:${LINE_PALETTE[0]};">● Cost basis: ${escapeHtml(costStr)}</div>`,
              `<div style="font-size:14px;font-weight:600;color:${ACCENT};">Gap: ${escapeHtml(gapStr)}</div>`,
            ].join("");
          }

          const sameDay = markersByDate.get(isoDate) ?? [];
          const markerLines = sameDay
            .map((m) => {
              const symbolStr = escapeHtml(m.instrument_symbol ?? "—");
              // m.quantity is a Decimal-as-string ("5.000000000000000000").
              // formatQuantity collapses trailing zeros via Intl.NumberFormat and yields
              // a locale-aware string; escapeHtml is kept as defense-in-depth so the
              // tooltip XSS mitigation is undisturbed.
              // Skip the qty render when value is null, non-finite, or zero,
              // a "0" or "NaN" payload would otherwise surface in the tooltip.
              const qtyNum = m.quantity != null ? Number(m.quantity) : null;
              const qty =
                qtyNum != null && Number.isFinite(qtyNum) && qtyNum !== 0
                  ? escapeHtml(
                      formatQuantity(
                        qtyNum,
                        decimalsFor({
                          instrumentType: m.instrument_type,
                          displayDecimals: m.display_decimals,
                        }),
                      ),
                    )
                  : "";
              const val = formatMoney(m.value, currency);
              if (m.type === "buy") {
                return `<div style="display:flex;gap:6px;align-items:center;"><span style="color:${POSITIVE}">■</span><span>Buy ${symbolStr}${qty ? ` ${qty}` : ""} = ${val}</span></div>`;
              }
              if (m.type === "sell") {
                return `<div style="display:flex;gap:6px;align-items:center;"><span style="color:${NEGATIVE}">□</span><span>Sell ${symbolStr}${qty ? ` ${qty}` : ""} = ${val}</span></div>`;
              }
              // yield rollup
              const count = m.count;
              // Yield buckets are keyed by (rollup, instrument_id), so
              // `count` is the per-instrument accrual count, not the number
              // of distinct instruments. Wording adjusted to match.
              return `<div style="display:flex;gap:6px;align-items:center;"><span style="color:${MUTED}">●</span><span>Yield: ${count} accrual${count === 1 ? "" : "s"} ${symbolStr !== "—" ? symbolStr : ""} = ${val}</span></div>`;
            })
            .join("");

          return [
            `<div style="font-size:12px;color:${MUTED};">${dateLabel}</div>`,
            `<div style="font-size:16px;font-weight:600;color:${ACCENT};">${escapeHtml(total)}</div>`,
            costBasisRows,
            markerLines ? `<div style="margin-top:6px;display:flex;flex-direction:column;gap:4px;">${markerLines}</div>` : "",
          ].join("");
        },
      },
      series: [
        // Cost basis goes first so the legend reads "Cost basis, Portfolio
        // value" left-to-right (matches the legacy /analytics overlay).
        // Always emit the series object — but with empty data when the
        // cost-basis toggle is off — so the option shape stays stable
        // across toggles and ECharts doesn't re-init the chart.
        {
          name: "Cost basis",
          type: "line",
          data: costBasisLineData,
          // `step: "end"` holds the prior value until the transaction date,
          // then steps up exactly on it — aligning the riser with the Buy
          // marker. "middle" drew the riser at the inter-day midpoint, making
          // the step look like it landed a day before the transaction.
          step: "end",
          showSymbol: false,
          // `itemStyle.color` drives the legend icon; without it ECharts
          // falls through to its default palette and the legend dot stops
          // matching the line color.
          itemStyle: { color: LINE_PALETTE[0] },
          lineStyle: { color: LINE_PALETTE[0], width: 2 },
          emphasis: { lineStyle: { width: 2 } },
        },
        {
          name: "Portfolio value",
          type: "line",
          data: lineData,
          showSymbol: false,
          smooth: false,
          sampling: "lttb",
          // See note on Cost basis above. Per-marker markPoint itemStyle
          // overrides this for the colored buy/sell/yield squares, so the
          // transaction markers keep their distinct green/red/grey colors.
          itemStyle: { color: ACCENT },
          lineStyle: { color: ACCENT, width: 2 },
          emphasis: { lineStyle: { width: 2 } },
          markPoint: {
            symbol: "rect",
            data: markPoints,
            label: { show: false },
          },
        },
      ],
    };
  }, [data, currency, timeframe, showTransactions, showCostBasis, showYields]);

  if (isLoading) {
    return <ChartSkeleton variant="line" className="h-80 w-full md:h-[420px]" />;
  }

  if (isError) {
    const message = error instanceof Error ? error.message : "Unknown error";
    return (
      <div className="flex h-80 flex-col items-center justify-center gap-3 rounded-lg border border-border bg-background text-center text-sm text-destructive md:h-[420px]">
        <p>Could not load net worth chart. {message}</p>
        <Button variant="ghost" size="sm" onClick={() => refetch()}>
          Retry
        </Button>
      </div>
    );
  }

  // When the user has switched to Custom but not yet applied a
  // from/to range, the query is disabled (see `enabled` in useQuery) so
  // `data` is undefined. The genuinely-empty copy ("Add a transaction…")
  // is wrong here, the user just hasn't picked a range yet.
  if (timeframe === "custom" && (!from || !to)) {
    return <CustomRangeEmpty />;
  }

  if (!data || data.points.length === 0) {
    return (
      <div className="flex h-80 flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border bg-muted/30 text-center text-sm text-muted-foreground md:h-[420px]">
        <p className="font-semibold">No portfolio history yet</p>
        <p>Add a transaction to see your net worth over time.</p>
      </div>
    );
  }

  // Surface backend warnings so the user knows when chart values reflect
  // data gaps (no PriceQuote / no FX rate). Warnings are emitted per-day
  // in the form `missing_price:{instrument_id}:{date}` and `missing_fx:{date}`.
  const missingPriceDates = new Set<string>();
  const missingFxDates = new Set<string>();
  const missingPriceInstruments = new Set<string>();
  for (const w of data.warnings) {
    if (w.startsWith("missing_price:")) {
      const parts = w.split(":");
      if (parts.length === 3) {
        missingPriceInstruments.add(parts[1]);
        missingPriceDates.add(parts[2]);
      }
    } else if (w.startsWith("missing_fx:")) {
      missingFxDates.add(w.slice("missing_fx:".length));
    }
  }
  const hasGaps = missingPriceDates.size > 0 || missingFxDates.size > 0;

  // On the dashboard, render each affected instrument as a link so the
  // user can jump straight to its overview (where the Backfill button
  // lives). On the per-instrument view, the user is already there, so
  // a plain hint pointing at the button-above suffices.
  const symbolByInstrumentId = new Map<string, string>();
  if (instruments) {
    for (const inst of instruments) {
      symbolByInstrumentId.set(inst.id, inst.symbol);
    }
  }
  const affectedLinks = !hasParentBackfill
    ? Array.from(missingPriceInstruments)
        .map((iid) => ({ id: iid, symbol: symbolByInstrumentId.get(iid) ?? "?" }))
        .sort((a, b) => a.symbol.localeCompare(b.symbol))
    : [];

  return (
    <div className="space-y-3">
      {hasGaps && (
        <Alert variant="default" className="border-amber-200 bg-amber-50 text-amber-900">
          <AlertTriangle className="text-amber-600" />
          <AlertDescription className="text-amber-900">
            {missingPriceDates.size > 0 && (
              <span>
                {missingPriceDates.size} day{missingPriceDates.size === 1 ? "" : "s"} with
                no market price
                {hasParentBackfill ? " — try the Backfill button above." : null}
                {!hasParentBackfill && affectedLinks.length > 0 ? (
                  <>
                    {" — Backfill: "}
                    {affectedLinks.map((inst, i) => (
                      <span key={inst.id}>
                        {i > 0 ? ", " : null}
                        <Link
                          href={`/holdings/i/${inst.id}`}
                          className="font-semibold underline decoration-amber-600/40 underline-offset-2 hover:decoration-amber-700"
                        >
                          {inst.symbol}
                        </Link>
                      </span>
                    ))}
                  </>
                ) : null}
              </span>
            )}
            {missingFxDates.size > 0 && (
              <span className={missingPriceDates.size > 0 ? " ml-2" : ""}>
                {missingFxDates.size} day{missingFxDates.size === 1 ? "" : "s"} missing
                EUR/USD FX rate.
              </span>
            )}
          </AlertDescription>
        </Alert>
      )}
      <div
        data-testid="networth-chart"
        className="h-80 w-full md:h-[420px]"
        role="img"
        aria-label={`Net worth chart from ${data.points[0]?.date} to ${data.points[data.points.length - 1]?.date}`}
      >
        <ReactECharts
          option={option ?? {}}
          notMerge={false}
          lazyUpdate
          style={{ width: "100%", height: "100%" }}
        />
      </div>
    </div>
  );
}
