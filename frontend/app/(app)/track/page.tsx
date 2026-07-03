"use client";

import { ConcentrationBanner } from "@/components/concentration/ConcentrationBanner";
import { NetWorthSection } from "@/components/networth/NetWorthSection";
import { PerformanceSection } from "@/components/perf/PerformanceSection";

/**
 * Dashboard page — root surface combining the Net worth time-series chart
 * and the Performance comparison table. PerformanceSection wraps the inline
 * Perf heading, selector, and PerfTable into a single component that mirrors
 * NetWorthSection.
 */
export default function Dashboard() {
  return (
    <main className="mx-auto max-w-7xl px-4 py-6 md:px-6 md:py-8">
      <h1 className="text-2xl font-semibold leading-tight">Dashboard</h1>

      <ConcentrationBanner />

      <NetWorthSection headingId="net-worth-heading" />

      <PerformanceSection />
    </main>
  );
}
