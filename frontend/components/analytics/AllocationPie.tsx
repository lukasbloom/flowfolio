"use client";

import { useCallback, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import * as echarts from "echarts/core";
import { PieChart } from "echarts/charts";
import { TooltipComponent, LegendComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import ReactECharts from "echarts-for-react";
import type { EChartsInstance } from "echarts-for-react";
import type { ECElementEvent } from "echarts";

import { apiFetch } from "@/lib/api-client";
import { useCurrency } from "@/lib/currency";
import { useTagFilter } from "@/lib/tag-filter";
import { formatMoney, instrumentTypeLabel } from "@/lib/format";
import { ChartSkeleton } from "@/components/charts/ChartSkeleton";
import { Button } from "@/components/ui/button";
import { PIE_PALETTE } from "@/components/charts/palette";

echarts.use([PieChart, TooltipComponent, LegendComponent, CanvasRenderer]);

type Dimension = "type" | "risk" | "account" | "banked";

interface AllocationSlice {
  label: string;
  value: string;
  percent: string;
}

interface AllocationResponse {
  dimension: Dimension;
  currency: string;
  total: string;
  slices: AllocationSlice[];
}

interface AllocationPieProps {
  dimension: Dimension;
  title: string;
  onSliceClick?: (slice: { label: string }) => void;
}

export function AllocationPie({ dimension, title, onSliceClick }: AllocationPieProps) {
  const { currency } = useCurrency();
  const { tagFilter } = useTagFilter();

  const { data, isLoading, isError, error, refetch } = useQuery<AllocationResponse>({
    queryKey: ["allocation", dimension, currency, tagFilter],
    queryFn: () =>
      apiFetch<AllocationResponse>(
        `/api/allocation?dimension=${dimension}&currency=${currency}${
          tagFilter ? `&tag=${encodeURIComponent(tagFilter)}` : ""
        }`
      ),
  });

  const option = useMemo(() => {
    if (!data || data.slices.length === 0) return null;

    const topSlice = data.slices[0];
    const topPct = topSlice ? topSlice.percent : "0";

    // Aria label computed here for use in the wrapper below
    void topSlice;
    void topPct;

    return {
      backgroundColor: "transparent",
      animation: true,
      textStyle: {
        fontFamily: "Inter, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif",
        fontSize: 12,
        color: "#737373",
      },
      color: [...PIE_PALETTE],
      legend: {
        type: "scroll",
        orient: "vertical",
        right: 8,
        top: "middle",
        icon: "circle",
        textStyle: {
          fontFamily: "Inter, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif",
          fontSize: 12,
          color: "#737373",
        },
      },
      tooltip: {
        trigger: "item",
        backgroundColor: "#FFFFFF",
        borderColor: "#E5E5E5",
        borderWidth: 1,
        textStyle: {
          color: "#262626",
          fontSize: 14,
          fontFamily: "Inter, system-ui, sans-serif",
        },
        padding: [8, 12],
        formatter: (params: { name: string; value: number; percent: number }) => {
          const val = formatMoney(params.value, currency as "EUR" | "USD");
          return `${params.name}: ${val} (${params.percent}%)`;
        },
      },
      series: [
        {
          type: "pie",
          // Stable series identity across option updates — without this, ECharts can
          // discard previously bound listeners when the data array rebuilds on refetch.
          name: "allocation",
          radius: ["40%", "70%"],
          center: ["35%", "50%"],
          label: {
            position: "outside",
            formatter: "{d}%",
            fontSize: 12,
            color: "#737373",
          },
          labelLine: {
            lineStyle: { color: "#262626" },
          },
          data: data.slices.map((s: AllocationSlice) => {
            // UX-M3: humanize raw lowercase enum labels for the by-type donut
            // (legend, tooltip, slice labels). Risk / account / banked render
            // their backend-supplied label unchanged.
            const displayName =
              dimension === "type" ? instrumentTypeLabel(s.label) : s.label;
            return {
              name: displayName,
              value: Number(s.value),
              itemStyle:
                s.label === "Sin clasificar" ? { opacity: 0.6 } : undefined,
            };
          }),
        },
      ],
      // UX-M14: fill the empty donut hole with a centered total + label.
      // Anchored at the SAME ["35%", "50%"] percentages as the pie series
      // `center` so the text stays glued to the donut hole when the chart
      // resizes.
      //
      // The non-obvious part is the group wrapper: ECharts positions
      // graphic elements by aligning their bounding-rect LEFT EDGE to
      // `left`, not their visual center. For a centered text that shifts
      // it half-text-width to the right of the donut center. The fix is a
      // zero-size group with `bounding: "raw"` and explicit width/height
      // of 0 — the layout treats it as a 0×0 rect, so the group's origin
      // (children's local 0,0) lands exactly at (35%, 50%). Children then
      // render around that origin with their own textAlign/textVerticalAlign,
      // putting the text's visible center on the donut center.
      //
      // Hex colors (not Tailwind tokens / CSS vars) because
      // `graphic.style.fill` does not resolve CSS variables.
      graphic: {
        type: "group",
        bounding: "raw",
        left: "35%",
        top: "50%",
        width: 0,
        height: 0,
        children: [
          {
            type: "text",
            top: -12,
            style: {
              text: formatMoney(data.total, currency as "EUR" | "USD"),
              font: "600 18px Inter, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif",
              fill: "#171717",
              textAlign: "center",
              textVerticalAlign: "middle",
            },
          },
          {
            type: "text",
            top: 10,
            style: {
              text: "Total",
              font: "12px Inter, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif",
              fill: "#737373",
              textAlign: "center",
              textVerticalAlign: "middle",
            },
          },
        ],
      },
    };
  }, [data, currency, dimension]);

  // Click registration via onChartReady:
  // - Inline onEvents={{ click: ... }} re-creates the handler every render and, combined
  //   with lazyUpdate + notMerge: false + a series whose identity churned on each refetch,
  //   silently dropped the binding. Binding once via chart.on('click') survives all future
  //   option updates.
  // - chart.off('click') guards against React StrictMode dev-mode double-invoke re-binds.
  // - Filtering on componentType === 'series' preserves ECharts' default legend-click
  //   toggle-visibility UX (legend clicks must NOT open the drill).
  const handleClick = useCallback(
    (params: ECElementEvent) => {
      if (params.componentType !== "series") return;
      onSliceClick?.({ label: params.name });
    },
    [onSliceClick]
  );

  const handleChartReady = useCallback(
    (chart: EChartsInstance) => {
      chart.off("click");
      chart.on("click", handleClick);
    },
    [handleClick]
  );

  const topSlice = data?.slices[0];
  const ariaLabel = data
    ? `Allocation by ${dimension}: ${topSlice?.label ?? ""} ${topSlice?.percent ?? "0"}% leading`
    : `Allocation by ${dimension} loading`;

  if (isLoading) {
    return (
      <div>
        <h3 className="text-base font-semibold mb-2">{title}</h3>
        <ChartSkeleton variant="donut" className="h-72 w-full md:h-80" />
      </div>
    );
  }

  if (isError) {
    const message = error instanceof Error ? error.message : "Unknown error";
    return (
      <div>
        <h3 className="text-base font-semibold mb-2">{title}</h3>
        <div className="flex h-72 flex-col items-center justify-center gap-3 rounded-lg border border-border bg-background text-center text-sm text-destructive md:h-80">
          <p>Could not load allocation data. {message}</p>
          <Button variant="ghost" size="sm" onClick={() => refetch()}>
            Retry
          </Button>
        </div>
      </div>
    );
  }

  if (!data || data.slices.length === 0) {
    return (
      <div>
        <h3 className="text-base font-semibold mb-2">{title}</h3>
        <div className="flex h-72 flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border bg-muted/30 text-center text-sm text-muted-foreground md:h-80">
          <p className="font-semibold">No allocation data yet</p>
          <p>Add a transaction to see how your portfolio is distributed.</p>
        </div>
      </div>
    );
  }

  return (
    <div>
      <h3 className="text-base font-semibold mb-2">{title}</h3>
      <div
        data-testid="allocation-pie"
        className="h-72 w-full md:h-80"
        role="img"
        aria-label={ariaLabel}
      >
        <ReactECharts
          option={option ?? {}}
          notMerge={false}
          lazyUpdate
          style={{ width: "100%", height: "100%" }}
          onChartReady={handleChartReady}
        />
      </div>
    </div>
  );
}
