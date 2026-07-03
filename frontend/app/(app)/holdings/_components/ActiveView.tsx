"use client";

import { Suspense } from "react";
import { PerfTable } from "@/components/perf/PerfTable";
import { Skeleton } from "@/components/ui/skeleton";

export function ActiveView() {
  return (
    <Suspense fallback={<Skeleton className="h-40 w-full" />}>
      {/* PerfTable aggregates by instrument_id client-side
          (see frontend/lib/holdings-aggregation.ts). The per-account split
          lives on /holdings/i/[id] via InstrumentAccountsTable. */}
      <PerfTable mode="open" />
    </Suspense>
  );
}
