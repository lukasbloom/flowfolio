"use client";

import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { format as formatDate } from "date-fns";
import { enGB } from "date-fns/locale";
import * as echarts from "echarts/core";
import { LineChart } from "echarts/charts";
import {
  GridComponent,
  TooltipComponent,
  MarkPointComponent,
  MarkLineComponent,
  LegendComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import ReactECharts from "echarts-for-react";

import { apiFetch } from "@/lib/api-client";
import { formatMoney, formatQuantity, decimalsFor } from "@/lib/format";
import { ChartSkeleton } from "@/components/charts/ChartSkeleton";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { AlertTriangle } from "lucide-react";
import { ACCENT, LINE_PALETTE, MUTED, NEGATIVE, POSITIVE } from "@/components/charts/palette";
import { escapeHtml, timeXAxis, toIsoDate, tooltipShell, valueYAxis } from "@/lib/chart-utils";
import { aggregateInstrument } from "@/lib/instrument-aggregation";
import { useOpenPerf } from "@/hooks/useOpenPerf";
import type { NwTimeframe } from "@/components/networth/timeframe";

echarts.use([
  LineChart,
  GridComponent,
  TooltipComponent,
  MarkPointComponent,
  MarkLineComponent,
  LegendComponent,
  CanvasRenderer,
]);

interface PriceQuote {
  id: string;
  instrument_id: string;
  date: string;
  price: string;
  currency: "EUR" | "USD";
  source: string;
  fetched_at: string;
}

interface Transaction {
  id: string;
  txn_type: string;
  date: string;
  quantity: string;
  unit_price: string | null;
  price_currency: "EUR" | "USD" | null;
  fx_rate_to_eur: string | null;
  instrument_id: string;
  instrument_symbol?: string;
  instrument_type?: string | null;
  display_decimals?: number | null;
  deleted_at: string | null;
}

interface InstrumentPriceChartProps {
  instrumentId: string;
  baseCurrency: "EUR" | "USD";
  instrumentType: string;
  displayDecimals: number | null;
  /** Inherited from NetWorthSection so the timeframe pill controls both modes. */
  timeframe: NwTimeframe;
  from: Date | null;
  to: Date | null;
  /** Reuses the existing Transactions toggle as a marker visibility switch. */
  showTransactions: boolean;
  /** Display currency from the global picker. Drives whether the cost-basis line is shown. */
  displayCurrency: "EUR" | "USD";
}

function filterByTimeframe<T extends { date: string }>(
  rows: T[],
  timeframe: NwTimeframe,
  from: Date | null,
  to: Date | null
): T[] {
  if (rows.length === 0) return rows;
  if (timeframe === "all") return rows;
  if (timeframe === "custom") {
    if (!from || !to) return rows;
    const fromIso = toIsoDate(from);
    const toIso = toIsoDate(to);
    return rows.filter((r) => r.date >= fromIso && r.date <= toIso);
  }
  const days = timeframe === "1m" ? 30 : timeframe === "3m" ? 90 : 365;
  const cutoff = new Date();
  cutoff.setDate(cutoff.getDate() - days);
  const cutoffIso = toIsoDate(cutoff);
  return rows.filter((r) => r.date >= cutoffIso);
}

/**
 * Price-per-unit chart for the instrument detail page.
 *
 * Renders `/api/prices/{id}/history` as a line in the instrument's base
 * currency. Optionally overlays a flat weighted-average-cost reference line
 * when the global display currency equals the instrument base currency —
 * otherwise the line would mix unit-of-account and be misleading.
 *
 * Buy/sell markers are placed at each transaction's `unit_price` in
 * `price_currency`; markers whose currency doesn't match the chart's base
 * currency are skipped (rather than approximated via FX, which would put
 * the marker on a different visual scale from the price line).
 */
export function InstrumentPriceChart({
  instrumentId,
  baseCurrency,
  instrumentType,
  displayDecimals,
  timeframe,
  from,
  to,
  showTransactions,
  displayCurrency,
}: InstrumentPriceChartProps) {
  // Pulls the price-history slice for the active timeframe — the server
  // applies the date-range filter so we get every row in
  // the window with no client-side row cap to worry about.
  const customFromIso = timeframe === "custom" && from ? toIsoDate(from) : null;
  const customToIso = timeframe === "custom" && to ? toIsoDate(to) : null;
  const historyUrl = (() => {
    const params = new URLSearchParams({ timeframe });
    if (timeframe === "custom" && customFromIso && customToIso) {
      params.set("from", customFromIso);
      params.set("to", customToIso);
    }
    return `/api/prices/${instrumentId}/history?${params.toString()}`;
  })();
  const {
    data: rawHistory,
    isLoading: histLoading,
    isError: histError,
  } = useQuery<PriceQuote[]>({
    queryKey: ["price-history", instrumentId, timeframe, customFromIso, customToIso],
    queryFn: () => apiFetch<PriceQuote[]>(historyUrl),
    // For custom range, wait until both endpoints are set; otherwise we'd
    // refetch the full "all" window once and immediately again with dates.
    enabled: timeframe !== "custom" || (!!customFromIso && !!customToIso),
    staleTime: 60_000,
  });

  const { data: transactions } = useQuery<Transaction[]>({
    queryKey: ["transactions", { includeDeleted: false, instrumentId }],
    queryFn: () =>
      apiFetch<Transaction[]>(`/api/transactions?instrument_id=${instrumentId}`),
    staleTime: 30_000,
  });

  // Reuse the same perf cache key as InstrumentKpiBlock for the
  // weighted-avg-cost reference line. When display currency != base
  // currency, perf row avg_cost is in display currency — we hide the line
  // in that case rather than risk a unit mismatch.
  const { data: perfRows } = useOpenPerf(displayCurrency);

  const aggregate = useMemo(() => {
    if (!perfRows) return null;
    return aggregateInstrument(perfRows.filter((r) => r.instrument_id === instrumentId));
  }, [perfRows, instrumentId]);

  const { history, filteredTx, chartCurrency, droppedTxCount, outOfRangeTxCount } = useMemo(() => {
    if (!rawHistory) {
      return {
        history: [] as PriceQuote[],
        filteredTx: [] as Transaction[],
        chartCurrency: baseCurrency,
        droppedTxCount: 0,
        outOfRangeTxCount: 0,
      };
    }

    // Dedup by date: each (instrument, date) may have multiple sources;
    // pick the most-recent fetched_at within the date. The endpoint
    // already orders by date desc then fetched_at desc, so the first row
    // for each date is the winner.
    const byDate = new Map<string, PriceQuote>();
    for (const q of rawHistory) {
      if (!byDate.has(q.date)) byDate.set(q.date, q);
    }
    const dedupedAll = [...byDate.values()].sort((a, b) =>
      a.date.localeCompare(b.date)
    );
    // Most rows carry the instrument's base currency. If a few legacy rows
    // carry the other currency, prefer the dominant one for the chart.
    const counts = new Map<"EUR" | "USD", number>();
    for (const q of dedupedAll) {
      counts.set(q.currency, (counts.get(q.currency) ?? 0) + 1);
    }
    // On a tie, prefer the instrument's base currency rather than whichever
    // currency happened to be inserted into the Map first — otherwise the chart
    // axis can silently flip per refetch when legacy rows match the priced rows
    // 1:1.
    let chartCcy: "EUR" | "USD" = baseCurrency;
    let maxCount = counts.get(baseCurrency) ?? -1;
    for (const [ccy, c] of counts) {
      if (c > maxCount) {
        chartCcy = ccy;
        maxCount = c;
      }
    }
    const mainCurrencyHistory = dedupedAll.filter((q) => q.currency === chartCcy);

    // Server already applied the date-range filter; the
    // client-side pass below is a defense-in-depth no-op for non-custom
    // timeframes but stays in place to absorb any clock-skew edge case.
    const filtered = filterByTimeframe(mainCurrencyHistory, timeframe, from, to);

    let dropped = 0;
    const tx: Transaction[] = [];
    if (transactions) {
      for (const t of transactions) {
        if (t.deleted_at) continue;
        if (t.unit_price === null || t.price_currency === null) continue;
        // Currency must match the chart's axis — placing a USD-priced txn
        // marker on a EUR price line gives a visually wrong y-coord.
        if (t.price_currency !== chartCcy) {
          dropped++;
          continue;
        }
        tx.push(t);
      }
    }
    const filteredTransactions = filterByTimeframe(tx, timeframe, from, to);
    // Drop transactions whose date falls outside the visible line's date range
    // — the axis-trigger tooltip snaps to the nearest line point, so a marker
    // sitting outside that range either lands in the axis gutter (visually
    // "not aligned with the x axis") or buckets onto an endpoint that has
    // nothing to do with the trade. Either way the marker is misleading; we
    // surface the count in an inline hint instead.
    const firstLineDate = filtered.length > 0 ? filtered[0].date : null;
    const lastLineDate = filtered.length > 0 ? filtered[filtered.length - 1].date : null;
    let outOfRange = 0;
    const inRangeTx: Transaction[] = [];
    for (const t of filteredTransactions) {
      if (firstLineDate && lastLineDate && (t.date < firstLineDate || t.date > lastLineDate)) {
        outOfRange++;
        continue;
      }
      inRangeTx.push(t);
    }
    return {
      history: filtered,
      filteredTx: inRangeTx,
      chartCurrency: chartCcy,
      droppedTxCount: dropped,
      outOfRangeTxCount: outOfRange,
    };
  }, [rawHistory, transactions, timeframe, from, to, baseCurrency]);

  const option = useMemo(() => {
    if (history.length === 0) return null;

    const lineData: [string, number][] = history.map((q) => [q.date, Number(q.price)]);

    const showCostLine = displayCurrency === chartCurrency && aggregate?.weighted_avg_cost != null;
    const avgCost = showCostLine ? aggregate!.weighted_avg_cost! : null;

    // Buy/sell markers — only shown when the user wants Transactions on.
    const markPoints = showTransactions
      ? filteredTx.map((t) => {
          const qty = Number(t.quantity);
          const yVal = Number(t.unit_price);
          if (!Number.isFinite(yVal)) return null;
          // qty === 0 rows (yield accruals / adjustments) carry no buy/sell
          // semantics and shouldn't render a green/red marker on the price line.
          if (qty === 0) return null;
          const isBuyish = qty > 0;
          return {
            name: isBuyish ? "buy" : "sell",
            coord: [t.date, yVal],
            symbol: "rect",
            symbolSize: 12,
            itemStyle: isBuyish
              ? { color: POSITIVE }
              : { color: "#FAFAFA", borderColor: NEGATIVE, borderWidth: 2 },
            value: { txId: t.id, type: isBuyish ? "buy" : "sell" },
          };
        }).filter(Boolean)
      : [];

    // Index transactions by the nearest line-data date instead of their own
    // date. With a `trigger: "axis"` tooltip ECharts snaps the cursor to the
    // closest line point, so a lookup keyed on the trade's exact day (e.g.
    // 2025-12-18) never matches the snapped axis label (e.g. 2025-12-31) and
    // the marker info silently disappears. This is especially visible for
    // manual-NAV instruments whose line is sparse (quarterly).
    const lineDates = history.map((q) => q.date);
    const lineDateMs = lineDates.map((d) => new Date(d).getTime());
    const snapToLineDate = (txDate: string): string => {
      if (lineDates.length === 0) return txDate;
      const txMs = new Date(txDate).getTime();
      let bestIdx = 0;
      let bestDist = Math.abs(lineDateMs[0] - txMs);
      for (let i = 1; i < lineDateMs.length; i++) {
        const d = Math.abs(lineDateMs[i] - txMs);
        if (d < bestDist) {
          bestDist = d;
          bestIdx = i;
        }
      }
      return lineDates[bestIdx];
    };
    const txByDate = new Map<string, Transaction[]>();
    if (showTransactions) {
      for (const t of filteredTx) {
        const key = snapToLineDate(t.date);
        const list = txByDate.get(key) ?? [];
        list.push(t);
        txByDate.set(key, list);
      }
    }
    const priceByDate = new Map(history.map((q) => [q.date, Number(q.price)]));

    const xAxisMinInterval =
      timeframe === "all" ? 28 * 24 * 60 * 60 * 1000 : undefined;
    const xAxisLabelFormatter = (val: number) => {
      const d = new Date(val);
      return timeframe === "all"
        ? formatDate(d, "MMMM yyyy", { locale: enGB })
        : formatDate(d, "dd MMMM", { locale: enGB });
    };

    return {
      backgroundColor: "transparent",
      animation: true,
      legend: showCostLine
        ? {
            show: true,
            top: 0,
            left: "center",
            icon: "rect",
            itemWidth: 14,
            itemHeight: 4,
            textStyle: { fontSize: 12, color: ACCENT },
            data: ["Avg cost", "Price"],
          }
        : { show: false },
      textStyle: {
        fontFamily: "Inter, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif",
        fontSize: 12,
        color: MUTED,
      },
      grid: { left: 12, right: 12, top: showCostLine ? 32 : 24, bottom: 32, containLabel: true },
      xAxis: timeXAxis({ minInterval: xAxisMinInterval, formatter: xAxisLabelFormatter }),
      yAxis: {
        ...valueYAxis((val: number) => formatMoney(val, chartCurrency)),
        position: "left",
        scale: true, // price ranges rarely include zero; let the axis breathe
      },
      tooltip: {
        ...tooltipShell("axis"),
        axisPointer: { type: "line", lineStyle: { color: MUTED, type: "dashed" } },
        formatter: (params: unknown) => {
          const arr = Array.isArray(params) ? params : [params];
          const first = arr[0] as { axisValueLabel?: string; value?: [string, number] } | undefined;
          if (!first) return "";
          const isoDate =
            (Array.isArray(first.value) && typeof first.value[0] === "string"
              ? first.value[0]
              : null) ?? "";
          const dateLabel = isoDate
            ? formatDate(new Date(isoDate), "dd MMMM yyyy", { locale: enGB })
            : escapeHtml(first.axisValueLabel ?? "");
          const priceVal = priceByDate.get(isoDate);
          const priceStr =
            priceVal != null
              ? formatMoney(priceVal, chartCurrency)
              : "";
          const sameDay = txByDate.get(isoDate) ?? [];
          const markerLines = sameDay
            .map((t) => {
              const qty = Number(t.quantity);
              const isBuyish = qty > 0;
              const dec = decimalsFor({
                instrumentType: t.instrument_type ?? instrumentType,
                displayDecimals: t.display_decimals ?? displayDecimals,
              });
              const qtyStr = escapeHtml(formatQuantity(Math.abs(qty), dec));
              const unitStr = escapeHtml(
                formatMoney(Number(t.unit_price), chartCurrency)
              );
              const color = isBuyish ? POSITIVE : NEGATIVE;
              const label = isBuyish ? "Buy" : "Sell";
              const marker = isBuyish ? "■" : "□";
              // Surface the trade's own date when it doesn't match the
              // snapped line date — otherwise the tooltip implies the buy
              // happened on the line's quarter-end.
              const txDateStr =
                t.date !== isoDate
                  ? ` <span style="color:${MUTED};font-size:12px;">(${escapeHtml(
                      formatDate(new Date(t.date), "dd MMM", { locale: enGB })
                    )})</span>`
                  : "";
              return `<div style="display:flex;gap:6px;align-items:center;"><span style="color:${color}">${marker}</span><span>${label} ${qtyStr} @ ${unitStr}${txDateStr}</span></div>`;
            })
            .join("");
          return [
            `<div style="font-size:12px;color:${MUTED};">${dateLabel}</div>`,
            `<div style="font-size:16px;font-weight:600;color:${ACCENT};">${escapeHtml(priceStr)}</div>`,
            markerLines
              ? `<div style="margin-top:6px;display:flex;flex-direction:column;gap:4px;">${markerLines}</div>`
              : "",
          ].join("");
        },
      },
      series: [
        ...(showCostLine
          ? [
              {
                name: "Avg cost",
                type: "line" as const,
                data: [] as [string, number][],
                markLine: {
                  silent: true,
                  symbol: ["none", "none"],
                  lineStyle: { color: LINE_PALETTE[0], width: 2, type: "dashed" as const },
                  label: { show: false },
                  data: [{ yAxis: avgCost! }],
                },
                itemStyle: { color: LINE_PALETTE[0] },
                lineStyle: { color: LINE_PALETTE[0], width: 2 },
              },
            ]
          : []),
        {
          name: "Price",
          type: "line" as const,
          data: lineData,
          showSymbol: false,
          smooth: false,
          sampling: "lttb" as const,
          itemStyle: { color: ACCENT },
          lineStyle: { color: ACCENT, width: 2 },
          markPoint: {
            symbol: "rect",
            data: markPoints,
            label: { show: false },
          },
        },
      ],
    };
  }, [
    history,
    filteredTx,
    showTransactions,
    timeframe,
    aggregate,
    chartCurrency,
    displayCurrency,
    instrumentType,
    displayDecimals,
  ]);

  if (histLoading) {
    return <ChartSkeleton variant="line" className="h-80 w-full md:h-[420px]" />;
  }

  if (histError) {
    return (
      <div className="flex h-80 flex-col items-center justify-center gap-3 rounded-lg border border-border bg-background text-center text-sm text-destructive md:h-[420px]">
        <p>Could not load price history.</p>
      </div>
    );
  }

  if (!rawHistory || rawHistory.length === 0) {
    return (
      <div className="flex h-80 flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border bg-muted/30 text-center text-sm text-muted-foreground md:h-[420px]">
        <p className="font-semibold">No price history recorded yet</p>
        <p>Use the Backfill button above to fetch historical quotes.</p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {(droppedTxCount > 0 || outOfRangeTxCount > 0) && (
        <Alert variant="default" className="border-amber-200 bg-amber-50 text-amber-900">
          <AlertTriangle className="text-amber-600" />
          <AlertDescription className="text-amber-900">
            {droppedTxCount > 0 ? (
              <span>
                {droppedTxCount} transaction{droppedTxCount === 1 ? "" : "s"} omitted —
                priced in the other currency.
              </span>
            ) : null}
            {outOfRangeTxCount > 0 ? (
              <span className={droppedTxCount > 0 ? " ml-2" : ""}>
                {outOfRangeTxCount} transaction{outOfRangeTxCount === 1 ? "" : "s"} outside
                the price-history range — backfill more history to plot {outOfRangeTxCount === 1 ? "it" : "them"}.
              </span>
            ) : null}
          </AlertDescription>
        </Alert>
      )}
      <div
        data-testid="instrument-price-chart"
        className="h-80 w-full md:h-[420px]"
        role="img"
        aria-label="Price per unit chart"
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
