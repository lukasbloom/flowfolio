// Re-export shim. The canonical timeframe type + presets now live in
// @/lib/timeframe (deduped with components/networth/timeframe.ts). Kept under
// the historical names so PerfTable / PerformanceSection imports stay unchanged.
import { TIMEFRAME_PRESETS, type Timeframe } from "@/lib/timeframe";

export type PerfTimeframe = Timeframe;
export const PERF_PRESETS = TIMEFRAME_PRESETS;
