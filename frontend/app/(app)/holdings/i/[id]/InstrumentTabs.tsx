"use client";

import { useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api-client";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { canHaveApy, canHaveManualNav } from "@/lib/instrument-eligibility";
import { OverviewTab } from "./OverviewTab";
import { NavHistoryTab } from "./NavHistoryTab";
import { ApyConfigTab } from "./ApyConfigTab";

// Local shape — same key as OverviewTab so the fetch is deduped by TanStack.
interface Instrument {
  id: string;
  symbol: string;
  name: string;
  instrument_type: string;
  base_currency: "EUR" | "USD";
  price_source: string;
  ticker_override: string | null;
  backfill_status?: "pending" | "running" | "complete" | "failed" | null;
}

export function InstrumentTabs({ id }: { id: string }) {
  const searchParams = useSearchParams();
  const { data } = useQuery({
    queryKey: ["instrument", id],
    queryFn: () => apiFetch<Instrument>(`/api/instruments/${id}`),
  });

  // While loading or on error, only Overview renders — intentional per the
  // UAT expectation ("hidden, not disabled"). OverviewTab owns its own
  // skeleton and error states.
  const showNav = data ? canHaveManualNav(data) : false;
  const showApy = data ? canHaveApy(data) : false;

  // UX-M9: when no other tabs are eligible, drop the lone "Overview" pill
  // and inline the OverviewTab content directly. While `data` is loading
  // both flags are false → Overview inlines; once data resolves and a
  // flag flips true, the component re-renders into the Tabs branch and
  // TanStack Query keeps the OverviewTab fetch deduped.
  if (!showNav && !showApy) {
    return <OverviewTab instrumentId={id} />;
  }

  // Deep-link tab support:
  //   /holdings/i/<id>?tab=apy[&account=<id>]  → APY config tab
  //   /holdings/i/<id>?tab=nav-history         → NAV history tab
  // Param maps to TabsTrigger value: `apy` → `apy-config`. Unknown / missing
  // param defaults to Overview. Falls through silently if the targeted tab
  // is not eligible for this instrument (silent no-match invariant).
  const tabParam = searchParams.get("tab");
  const requestedTab =
    tabParam === "apy" && showApy
      ? "apy-config"
      : tabParam === "nav-history" && showNav
        ? "nav-history"
        : "overview";

  return (
    <Tabs defaultValue={requestedTab}>
      <TabsList>
        <TabsTrigger value="overview">Overview</TabsTrigger>
        {showNav && <TabsTrigger value="nav-history">NAV history</TabsTrigger>}
        {showApy && <TabsTrigger value="apy-config">APY config</TabsTrigger>}
      </TabsList>
      <TabsContent value="overview" className="mt-6">
        <OverviewTab instrumentId={id} />
      </TabsContent>
      {showNav && (
        <TabsContent value="nav-history" className="mt-6">
          <NavHistoryTab instrumentId={id} />
        </TabsContent>
      )}
      {showApy && (
        <TabsContent value="apy-config" className="mt-6">
          <ApyConfigTab instrumentId={id} />
        </TabsContent>
      )}
    </Tabs>
  );
}
