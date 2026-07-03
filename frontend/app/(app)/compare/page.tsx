"use client";

import { Suspense, useState } from "react";

import { KpiStrip } from "@/components/analytics/KpiStrip";
import { AllocationPie } from "@/components/analytics/AllocationPie";
import { AllocationDrill } from "@/components/analytics/AllocationDrill";
import { ContributionBars } from "@/components/contributions/ContributionBars";
import { Separator } from "@/components/ui/separator";
import { Dialog } from "@/components/ui/dialog";
import { ExcludeClosedToggle } from "@/components/compare/ExcludeClosedToggle";

type Dimension = "type" | "risk" | "account" | "banked";

interface PieSpec {
  dimension: Dimension;
  title: string;
}

const PIES: PieSpec[] = [
  { dimension: "type", title: "By type" },
  { dimension: "risk", title: "By risk" },
  { dimension: "account", title: "By account" },
  { dimension: "banked", title: "By banked / non-banked" },
];

export default function AnalyticsPage() {
  // Inline drill state: one slice can be expanded at a time per pie.
  const [drilledSlice, setDrilledSlice] = useState<{
    dimension: Dimension;
    sliceLabel: string;
  } | null>(null);

  function handleSliceClick(dim: Dimension, label: string) {
    // Click same slice again → close
    if (
      drilledSlice &&
      drilledSlice.dimension === dim &&
      drilledSlice.sliceLabel === label
    ) {
      setDrilledSlice(null);
    } else {
      setDrilledSlice({ dimension: dim, sliceLabel: label });
    }
  }

  // The standalone Cost Basis vs. Value card was unified
  // into the dashboard NetWorth chart (one toggle away). Its timeframe state,
  // overlay component, and NwTimeframeSelect block previously lived here —
  // all removed. The /api/contributions endpoint stays (ContributionBars
  // still uses it for buckets).

  return (
    <main className="mx-auto max-w-7xl px-4 py-6 md:px-6 md:py-8">
      <h1 className="text-2xl font-semibold leading-tight">Analytics</h1>

      {/* KPI strip — Realized totals */}
      <section className="mt-6" aria-labelledby="analytics-kpi">
        <h2 id="analytics-kpi" className="sr-only">
          Realized totals
        </h2>
        <KpiStrip />
      </section>

      <Separator className="my-8" />

      {/* Allocation 2x2 grid */}
      <section aria-labelledby="analytics-allocation">
        <div className="mb-4 flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <h2
              id="analytics-allocation"
              className="text-2xl font-semibold leading-tight"
            >
              Allocation
            </h2>
            <p className="mt-1 text-sm text-muted-foreground">
              Click any slice to see the holdings inside it.
            </p>
          </div>
          <Suspense fallback={null}>
            <ExcludeClosedToggle />
          </Suspense>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 md:gap-6">
          {PIES.map((p) => (
            <div key={p.dimension} className="flex flex-col gap-4">
              <AllocationPie
                dimension={p.dimension}
                title={p.title}
                onSliceClick={(slice) =>
                  handleSliceClick(p.dimension, slice.label)
                }
              />
            </div>
          ))}
        </div>
      </section>

      <Separator className="my-8" />

      {/* Stacked bars (Month/Year toggle is internal to ContributionBars) */}
      <section aria-labelledby="analytics-contributions">
        <h2
          id="analytics-contributions"
          className="text-2xl font-semibold leading-tight mb-4"
        >
          Contributions per period
        </h2>
        <ContributionBars />
      </section>

      {/* Single page-level drill modal — driven by drilledSlice. Radix portals
          this to document.body, so the surrounding sections never shift when
          it opens. */}
      <Dialog
        open={drilledSlice !== null}
        onOpenChange={(open) => {
          if (!open) setDrilledSlice(null);
        }}
      >
        {drilledSlice && (
          <AllocationDrill
            dimension={drilledSlice.dimension}
            sliceLabel={drilledSlice.sliceLabel}
          />
        )}
      </Dialog>
    </main>
  );
}
