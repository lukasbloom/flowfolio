"use client";

import { useState } from "react";

import { PerfTable } from "@/components/perf/PerfTable";
import { PERF_PRESETS, type PerfTimeframe } from "@/components/perf/timeframe";
import { TimeframeToggle } from "@/components/ui/timeframe-toggle";
import { formatDateRange } from "@/lib/format";

/**
 * Performance dashboard wrapper. Mirrors NetWorthSection's
 * state pattern: owns `timeframe` + `from`/`to` locally and forwards them to
 * PerfTable as props. The previous `?perf=` URL persistence is intentionally
 * dropped, so bookmarked /track?perf=3m links revert to
 * the default 1y.
 */
export function PerformanceSection() {
  const [timeframe, setTimeframe] = useState<PerfTimeframe>("1y");
  const [from, setFrom] = useState<Date | null>(null);
  const [to, setTo] = useState<Date | null>(null);

  return (
    <section
      className="mt-8 space-y-4"
      aria-labelledby="performance-heading"
      data-testid="performance-table"
    >
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-baseline gap-2">
          <h2
            id="performance-heading"
            className="text-2xl font-semibold leading-tight"
          >
            Performance
          </h2>
          {timeframe === "custom" && from && to && (
            <span className="text-sm font-normal text-muted-foreground">
              {formatDateRange(from, to)}
            </span>
          )}
        </div>
        <TimeframeToggle
          presets={PERF_PRESETS}
          value={timeframe}
          onChange={(next) => setTimeframe(next as PerfTimeframe)}
          ariaLabel="Performance timeframe"
          customRange={{
            from,
            to,
            onChange: ({ from: f, to: t }) => {
              setFrom(f);
              setTo(t);
            },
          }}
        />
      </div>
      <PerfTable timeframe={timeframe} from={from} to={to} />
    </section>
  );
}
