"use client";

import { useQuery } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api-client";
import type { PerfHoldingRow } from "@/components/perf/PerfTable";

/**
 * Shared "all open+closed perf rows in `currency`" query, hand-built identically
 * at three instrument-detail call sites (InstrumentKpiBlock, InstrumentAccountsTable,
 * InstrumentPriceChart). TanStack Query dedupes across them on the matching key.
 *
 * The query key shape is preserved byte-for-byte —
 *   ["perf", "open", "all", currency, null, true]
 * — so existing cache entries continue to match (do NOT reorder/retype elements).
 */
export function useOpenPerf(currency: string) {
  return useQuery({
    queryKey: ["perf", "open", "all", currency, null, true],
    queryFn: () =>
      apiFetch<PerfHoldingRow[]>(
        `/api/perf?currency=${currency}&timeframe=all&include_closed=1`
      ),
    staleTime: 30_000,
  });
}
