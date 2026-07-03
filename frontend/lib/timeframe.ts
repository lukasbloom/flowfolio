// Single source of truth for the chart/table timeframe union and preset list.
// components/perf/timeframe.ts and components/networth/timeframe.ts were
// byte-identical (same union, same presets) under different exported names;
// both now re-export from here so existing call sites keep their imports.

export type Timeframe = "1m" | "3m" | "1y" | "all" | "custom";

export const TIMEFRAME_PRESETS: Array<{
  value: Exclude<Timeframe, "custom">;
  label: string;
}> = [
  { value: "1m", label: "1M" },
  { value: "3m", label: "3M" },
  { value: "1y", label: "1Y" },
  { value: "all", label: "All" },
];
