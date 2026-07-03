"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Pencil, Tag as TagIcon } from "lucide-react";
import { apiFetch } from "@/lib/api-client";
import { instrumentTypeLabel, priceSourceLabel } from "@/lib/format";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { BackfillBadge } from "@/components/instruments/BackfillBadge";
import { BackfillButton } from "@/components/instruments/BackfillButton";
import { InstrumentFormDialog } from "@/components/instruments/InstrumentFormDialog";
import type { InstrumentResponse } from "@/components/instruments/useCreateInstrument";
import { NetWorthSection } from "@/components/networth/NetWorthSection";
import { HoldingTagsSection } from "@/components/tags/HoldingTagsSection";
import { TxnList } from "@/components/transactions/TxnList";
import { Separator } from "@/components/ui/separator";

import { InstrumentKpiBlock } from "./InstrumentKpiBlock";
import { InstrumentAccountsTable } from "./InstrumentAccountsTable";
import { InstrumentPriceChart } from "./InstrumentPriceChart";

// Price sources for which `/api/instruments/{id}/backfill` is a no-op
// (returns `manual_history_required`). The button is hidden for these.
const MANUAL_PRICE_SOURCES = new Set(["ft", "manual"]);

interface Instrument {
  id: string;
  symbol: string;
  name: string;
  instrument_type: string;
  base_currency: "EUR" | "USD";
  price_source: string;
  // Backend now always returns risk_level. We pin to
  // string here (vs. a "High"|"Medium"|"Low"|"Liquid" union) because the
  // editor dialog re-uses InstrumentResponse from useCreateInstrument
  // and the structural type-equality check only needs them to overlap.
  risk_level: string;
  ticker_override: string | null;
  display_decimals: number | null;
  created_at: string;
  // TODO(phase-04): wire `backfill_status` once the backend payload exposes it.
  backfill_status?: "pending" | "running" | "complete" | "failed" | null;
}

export function OverviewTab({ instrumentId }: { instrumentId: string }) {
  const [editOpen, setEditOpen] = useState(false);
  const { data, isLoading, isError } = useQuery({
    queryKey: ["instrument", instrumentId],
    queryFn: () => apiFetch<Instrument>(`/api/instruments/${instrumentId}`),
  });

  // Tag-count for the popover trigger badge. Re-uses the same
  // ["instrument-holdings", instrumentId] cache key that HoldingTagsSection
  // already populates inside the popover — TanStack Query dedupes, so this
  // is a free read once the popover has been opened (or a cheap prefetch
  // otherwise).
  const { data: holdingsForCount } = useQuery<Array<{ tags: Array<{ id: string }> }>>({
    queryKey: ["instrument-holdings", instrumentId],
    queryFn: () =>
      apiFetch<Array<{ tags: Array<{ id: string }> }>>(
        `/api/instruments/${instrumentId}/holdings`
      ),
  });
  // Distinct tag count — a tag attached to N holdings used to count N times.
  const tagCount = new Set(
    (holdingsForCount ?? []).flatMap((p) => p.tags.map((t) => t.id))
  ).size;

  if (isLoading) return <Skeleton className="h-40 w-full" />;
  if (isError || !data)
    return <p className="text-sm text-destructive">Could not load instrument.</p>;

  return (
    <div className="space-y-6">
      {/* Slim two-line header replaces the prior bordered card.
          Drop the card chrome; metadata strip below the title; tags moved into a
          popover keyed by the "Tags (N)" button so the top of the page stays compact
          and the KPI block sits higher above the fold. */}
      <header className="space-y-2">
        {/* Line 1 — title + actions; wraps to multiple rows on narrow viewports
            so the action cluster falls below the title rather than forcing
            horizontal scroll. */}
        <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2">
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <h1 className="text-2xl font-semibold leading-tight">{data.name}</h1>
            <BackfillBadge status={data.backfill_status ?? null} />
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Popover>
              <PopoverTrigger asChild>
                <Button
                  variant="outline"
                  size="sm"
                  aria-label={
                    tagCount > 0
                      ? `Edit tags (${tagCount} attached)`
                      : "Edit tags"
                  }
                >
                  <TagIcon className="size-3.5" aria-hidden="true" />
                  <span className="ml-1.5">Tags</span>
                  {tagCount > 0 ? (
                    <Badge
                      variant="secondary"
                      className="ml-1.5 h-5 px-1.5 text-xs tabular-nums"
                    >
                      {tagCount}
                    </Badge>
                  ) : null}
                </Button>
              </PopoverTrigger>
              <PopoverContent
                align="end"
                className="w-[min(28rem,calc(100vw-2rem))] p-4"
              >
                <HoldingTagsSection instrumentId={instrumentId} />
              </PopoverContent>
            </Popover>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setEditOpen(true)}
              aria-label="Edit instrument"
            >
              <Pencil className="size-3.5" aria-hidden="true" />
              <span className="ml-1.5">Edit</span>
            </Button>
            <BackfillButton
              instrumentId={instrumentId}
              hideForSource={MANUAL_PRICE_SOURCES.has(data.price_source)}
              symbol={data.symbol}
            />
          </div>
        </div>
        {/* Line 2 — metadata strip; same content as before, now without a card. */}
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted-foreground">
          <span className="font-mono text-xs tracking-wide">{data.symbol}</span>
          <Separator orientation="vertical" className="h-4" />
          <span>{instrumentTypeLabel(data.instrument_type)}</span>
          <Separator orientation="vertical" className="h-4" />
          <span className="tabular-nums">{data.base_currency}</span>
          <Separator orientation="vertical" className="h-4" />
          <span>{priceSourceLabel(data.price_source)}</span>
          <Separator orientation="vertical" className="h-4" />
          <span>Risk: {data.risk_level}</span>
          {data.ticker_override ? (
            <>
              <Separator orientation="vertical" className="h-4" />
              <span>
                Ticker: <span className="font-mono">{data.ticker_override}</span>
              </span>
            </>
          ) : null}
        </div>
      </header>

      {/* Headline KPIs — aggregated across all accounts. */}
      <InstrumentKpiBlock
        instrumentId={instrumentId}
        instrument={{
          id: data.id,
          instrument_type: data.instrument_type,
          base_currency: data.base_currency,
          display_decimals: data.display_decimals,
        }}
      />

      {/* Per-account breakdown — auto-hidden for single-account open holdings
          (the KPI block already covers that case). */}
      <InstrumentAccountsTable instrumentId={instrumentId} />

      {/* Net-worth chart with a Holding value / Price per unit toggle.
          Price mode plots `/api/prices/{id}/history` in the instrument's
          native currency and overlays a weighted-avg-cost reference line
          (when the global display currency matches the base currency). */}
      <NetWorthSection
        heading="Performance over time"
        headingId="instrument-net-worth-heading"
        instrumentId={instrumentId}
        priceChartSlot={({ timeframe, from, to, showTransactions, displayCurrency }) => (
          <InstrumentPriceChart
            instrumentId={instrumentId}
            baseCurrency={data.base_currency}
            instrumentType={data.instrument_type}
            displayDecimals={data.display_decimals}
            timeframe={timeframe}
            from={from}
            to={to}
            showTransactions={showTransactions}
            displayCurrency={displayCurrency}
          />
        )}
      />

      {/* Transactions ledger filtered to this instrument. TxnList already
          reads `?instrument_id` from URL search params on `/track`; the
          new `instrumentId` prop lets the detail page filter without
          touching the URL. */}
      <section
        className="space-y-3"
        aria-labelledby="instrument-transactions-heading"
      >
        {/* Heading text says "Activity" to match nav vocabulary; id retained as a stable anchor. */}
        <h2
          id="instrument-transactions-heading"
          className="text-2xl font-semibold leading-tight"
        >
          Activity
        </h2>
        <TxnList instrumentId={instrumentId} />
      </section>

      {/* Conditionally mounted so the InstrumentForm's useState(defaultValues)
          starts fresh every time the dialog opens — avoids state bleed when
          editing different instruments back-to-back in the same session. */}
      {editOpen ? (
        <InstrumentFormDialog
          instrument={data as InstrumentResponse}
          open={editOpen}
          onOpenChange={setEditOpen}
        />
      ) : null}
    </div>
  );
}
