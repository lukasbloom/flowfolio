/**
 * Shared chart helpers, promoted from byte-identical copies in
 * NetWorthChart and InstrumentPriceChart.
 */

import { ACCENT, BORDER, MUTED } from "../components/charts/palette";

/**
 * Escape a string for safe interpolation into an ECharts tooltip's innerHTML.
 * ECharts tooltip formatters emit raw HTML, so any
 * user-controlled text (instrument symbols/names) must be entity-escaped.
 */
export function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/**
 * Format a Date as a local-calendar YYYY-MM-DD string. Uses the local
 * getFullYear/getMonth/getDate (NOT toISOString, which would shift to UTC and
 * can land on the wrong calendar day) so it matches backend calendar dates.
 */
export function toIsoDate(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

/**
 * Shared tooltip container (background, border, text style, padding) common
 * to every chart. Callers spread this and add their own `axisPointer` /
 * `formatter`: `tooltip: { ...tooltipShell("axis"), axisPointer: {...}, formatter }`.
 */
export function tooltipShell(trigger: "axis" | "item"): Record<string, unknown> {
  return {
    trigger,
    backgroundColor: "#FFFFFF",
    borderColor: BORDER,
    borderWidth: 1,
    textStyle: {
      color: ACCENT,
      fontSize: 14,
      fontFamily: "Inter, system-ui, sans-serif",
    },
    padding: [8, 12],
  };
}

/**
 * Shared muted axisLabel base (color + size) used by every axis. Pass a
 * formatter when the axis needs one, omit it for a plain label.
 */
export function mutedAxisLabel(formatter?: (val: number) => string): Record<string, unknown> {
  return {
    color: MUTED,
    fontSize: 12,
    ...(formatter ? { formatter } : {}),
  };
}

/**
 * Shared value yAxis: no axis line/ticks, muted label, dashed split line.
 * Callers needing `scale: true` or `position: "left"` spread and add:
 * `yAxis: { ...valueYAxis(fmt), scale: true, position: "left" }`.
 */
export function valueYAxis(formatter: (val: number) => string): Record<string, unknown> {
  return {
    type: "value",
    axisLine: { show: false },
    axisTick: { show: false },
    axisLabel: mutedAxisLabel(formatter),
    splitLine: { lineStyle: { color: BORDER, type: "dashed" } },
  };
}

/**
 * Shared time xAxis: BORDER axis line, no ticks, muted hideOverlap label,
 * no split line. `minInterval` is optional (only "all" timeframe sets one).
 */
export function timeXAxis({
  minInterval,
  formatter,
}: {
  minInterval?: number;
  formatter: (val: number) => string;
}): Record<string, unknown> {
  return {
    type: "time",
    minInterval,
    axisLine: { lineStyle: { color: BORDER } },
    axisTick: { show: false },
    axisLabel: { ...mutedAxisLabel(formatter), hideOverlap: true, interval: "auto" },
    splitLine: { show: false },
  };
}
