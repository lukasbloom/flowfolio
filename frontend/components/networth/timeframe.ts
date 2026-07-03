// Re-export shim. The canonical timeframe type + presets now live in
// @/lib/timeframe (deduped with components/perf/timeframe.ts). Kept under the
// historical names so the Net worth chart, price chart, and NetWorthSection
// imports stay unchanged.
import { TIMEFRAME_PRESETS, type Timeframe } from "@/lib/timeframe";

export type NwTimeframe = Timeframe;
export const NW_PRESETS = TIMEFRAME_PRESETS;
