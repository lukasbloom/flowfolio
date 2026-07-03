"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import * as echarts from "echarts/core";
import { BarChart } from "echarts/charts";
import {
  GridComponent,
  TooltipComponent,
  LegendComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import ReactECharts from "echarts-for-react";
import Link from "next/link";
import { format as formatDate, parseISO } from "date-fns";
import { enGB } from "date-fns/locale";

import { apiFetch } from "@/lib/api-client";
import { useCurrency } from "@/lib/currency";
import { useTagFilter } from "@/lib/tag-filter";
import { formatCompactMoney, formatMoney } from "@/lib/format";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { ACCENT, MUTED, BORDER, CONTRIB_BAR_PALETTE } from "@/components/charts/palette";
import { PeriodToggle } from "@/components/contributions/PeriodToggle";

echarts.use([BarChart, GridComponent, TooltipComponent, LegendComponent, CanvasRenderer]);

interface ContributionBucket {
  period_label: string;
  period_start: string;
  deposits: string;
  spendings: string;
  realized_gains: string;
  yield_amount: string;
}

interface ContributionsResponse {
  currency: string;
  period: string;
  cost_basis_series: unknown[];
  portfolio_value_series: unknown[];
  buckets: ContributionBucket[];
}

export function ContributionBars() {
  const { currency } = useCurrency();
  const { tagFilter } = useTagFilter();
  const [period, setPeriod] = useState<"month" | "year">("month");

  const { data, isLoading, isError, error, refetch } = useQuery<ContributionsResponse>({
    queryKey: ["contributions-bars", period, currency, tagFilter],
    queryFn: () =>
      apiFetch<ContributionsResponse>(
        `/api/contributions?period=${period}&currency=${currency}${tagFilter ? `&tag=${encodeURIComponent(tagFilter)}` : ""}`
      ),
  });

  // Reformat backend's "%b %y" labels (e.g. "Jan 26") to unambiguous
  // "MMMM yyyy" en-GB locale ("January 2026"). period_start is an ISO date,
  // read as UTC to avoid year-shift on Dec 31 / Jan 1 for users east of UTC
  // (same off-by-one class). Full English month names (en-GB),
  // locale unification. Mirrors NetWorthChart.tsx.
  const categories = useMemo(() => {
    if (!data || !data.buckets.length) return [] as string[];
    return data.buckets.map((b) => {
      const d = parseISO(b.period_start);
      return period === "year"
        ? String(d.getUTCFullYear())
        : formatDate(d, "MMMM yyyy", { locale: enGB });
    });
  }, [data, period]);

  const option = useMemo(() => {
    if (!data || !data.buckets.length) return null;

    const buckets = data.buckets;

    return {
      backgroundColor: "transparent",
      textStyle: {
        fontFamily: "Inter, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif",
        fontSize: 12,
        color: MUTED,
      },
      legend: {
        show: true,
        top: 0,
        left: 0,
        icon: "rect",
        itemWidth: 12,
        itemHeight: 12,
        textStyle: { fontSize: 12, color: ACCENT },
        data: ["Deposits", "Spendings", "Realized gains", "Yield"],
      },
      grid: { right: 12, top: 32, bottom: 40, containLabel: true },
      xAxis: {
        type: "category",
        data: categories,
        axisLine: { lineStyle: { color: BORDER } },
        axisTick: { show: false },
        axisLabel: { color: MUTED, fontSize: 12, hideOverlap: true },
        splitLine: { show: false },
      },
      yAxis: {
        type: "value",
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: {
          color: MUTED,
          fontSize: 12,
          formatter: (val: number) => formatCompactMoney(val, currency),
        },
        splitLine: { lineStyle: { color: BORDER, type: "dashed" } },
      },
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "shadow" },
        backgroundColor: "#FFFFFF",
        borderColor: BORDER,
        borderWidth: 1,
        textStyle: {
          color: ACCENT,
          fontSize: 14,
          fontFamily: "Inter, system-ui, sans-serif",
        },
        padding: [8, 12],
        formatter: (params: unknown) => {
          const arr = Array.isArray(params) ? params : [];
          if (!arr.length) return "";
          const periodLabel = arr[0]
            ? (arr[0] as { axisValue?: string }).axisValue ?? ""
            : "";

          let deposits = 0;
          let spendings = 0;
          let realized = 0;
          let yieldAmt = 0;

          for (const p of arr as Array<{ seriesName?: string; value?: number }>) {
            const val = p.value ?? 0;
            if (p.seriesName === "Deposits") deposits = val;
            else if (p.seriesName === "Spendings") spendings = Math.abs(val);
            else if (p.seriesName === "Realized gains") realized = val;
            else if (p.seriesName === "Yield") yieldAmt = val;
          }

          const netChange = deposits - spendings + realized + yieldAmt;

          const sign = (v: number) => (v >= 0 ? "+" : "");

          return [
            `<div style="font-size:12px;color:${MUTED};">${periodLabel}</div>`,
            `<div style="margin-top:4px;display:flex;gap:8px;"><span style="color:${CONTRIB_BAR_PALETTE.deposits}">■</span><span>Deposits&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;${sign(deposits)}${formatMoney(deposits, currency)}</span></div>`,
            `<div style="display:flex;gap:8px;"><span style="color:${CONTRIB_BAR_PALETTE.spendings}">■</span><span>Spendings&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;−${formatMoney(spendings, currency)}</span></div>`,
            `<div style="display:flex;gap:8px;"><span style="color:${CONTRIB_BAR_PALETTE.realized}">■</span><span>Realized gains&nbsp;${sign(realized)}${formatMoney(realized, currency)}</span></div>`,
            `<div style="display:flex;gap:8px;"><span style="color:${CONTRIB_BAR_PALETTE.yield}">■</span><span>Yield&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;${sign(yieldAmt)}${formatMoney(yieldAmt, currency)}</span></div>`,
            `<div style="margin-top:6px;font-weight:600;font-size:14px;color:${ACCENT};">Net change&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;${sign(netChange)}${formatMoney(netChange, currency)}</div>`,
          ].join("");
        },
      },
      series: [
        {
          name: "Deposits",
          type: "bar",
          stack: "total",
          itemStyle: { color: CONTRIB_BAR_PALETTE.deposits },
          data: buckets.map((b) => Number(b.deposits)),
        },
        {
          name: "Spendings",
          type: "bar",
          stack: "total",
          itemStyle: { color: CONTRIB_BAR_PALETTE.spendings },
          data: buckets.map((b) => -Number(b.spendings)),
        },
        {
          name: "Realized gains",
          type: "bar",
          stack: "total",
          itemStyle: { color: CONTRIB_BAR_PALETTE.realized },
          data: buckets.map((b) => Number(b.realized_gains)),
        },
        {
          name: "Yield",
          type: "bar",
          stack: "total",
          itemStyle: { color: CONTRIB_BAR_PALETTE.yield },
          data: buckets.map((b) => Number(b.yield_amount)),
        },
      ],
    };
  }, [data, currency, categories]);

  const fromPeriod = categories[0] ?? "";
  const toPeriod = categories[categories.length - 1] ?? "";

  return (
    <div>
      <div className="flex justify-end mb-2">
        <PeriodToggle value={period} onChange={setPeriod} />
      </div>

      {isLoading && <Skeleton className="h-72 md:h-96 w-full rounded-md" />}

      {isError && (
        <div className="flex h-72 flex-col items-center justify-center gap-3 rounded-lg border border-border bg-background text-center text-sm text-destructive md:h-96">
          <p>Could not load contributions chart. {error instanceof Error ? error.message : "Unknown error"}</p>
          <Button variant="ghost" size="sm" onClick={() => refetch()}>
            Retry
          </Button>
        </div>
      )}

      {!isLoading && !isError && (!data || data.buckets.length === 0) && (
        <div className="flex h-72 flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border bg-muted/30 text-center text-sm text-muted-foreground md:h-96">
          <p className="font-semibold">No contributions yet</p>
          <p>Add a transaction to see contributions per period.</p>
          <Button variant="outline" size="sm" asChild>
            <Link href="/activity">Add transaction</Link>
          </Button>
        </div>
      )}

      {!isLoading && !isError && data && data.buckets.length > 0 && (
        <div
          data-testid="contribution-bars"
          className="h-72 w-full md:h-96"
          role="img"
          aria-label={`Contributions per ${period} from ${fromPeriod} to ${toPeriod}`}
        >
          <ReactECharts
            option={option ?? {}}
            notMerge={false}
            lazyUpdate
            style={{ width: "100%", height: "100%" }}
          />
        </div>
      )}
    </div>
  );
}
