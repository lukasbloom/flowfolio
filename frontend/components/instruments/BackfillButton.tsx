"use client";

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2, RefreshCw } from "lucide-react";
import { toast } from "sonner";

import { BackfillConfirmDialog } from "@/components/instruments/BackfillConfirmDialog";
import { Button } from "@/components/ui/button";
import { ApiError, apiFetch } from "@/lib/api-client";

interface BackfillResponse {
  instrument_id: string;
  status: string;
  inserted_prices: number;
  skipped_existing: number;
  inserted_fx_rates?: number;
}

// `apiFetch` stuffs the raw response body into `ApiError.detail` (a string).
// When the backend returns `{"detail": "..."}` JSON we want just the string,
// not the surrounding braces and quotes.
function parseDetail(raw: string): string | null {
  try {
    const parsed = JSON.parse(raw);
    return typeof parsed?.detail === "string" ? parsed.detail : null;
  } catch {
    return null;
  }
}

interface BackfillButtonProps {
  instrumentId: string;
  /** Set when the underlying instrument can't be backfilled programmatically
   *  (price_source ∈ {ft, manual}). The button is hidden — not disabled —
   *  in those cases (no DOM rendered). */
  hideForSource?: boolean;
  /** Shown verbatim in the confirmation dialog so the user knows which
   *  instrument they're about to backfill. When omitted we still render
   *  "this instrument" as a fallback. */
  symbol?: string;
  /** ISO date string of the earliest transaction on this instrument. When
   *  undefined the dialog falls back to the generic "your first transaction"
   *  phrasing (avoids requiring a new per-id preview endpoint). */
  earliestFirstTxnDate?: string | null;
  /** Defaults to 1 — one upstream call per instrument is the floor. */
  estimatedApiCalls?: number;
}

export function BackfillButton({
  instrumentId,
  hideForSource,
  symbol,
  earliestFirstTxnDate,
  estimatedApiCalls,
}: BackfillButtonProps) {
  const qc = useQueryClient();
  const [confirmOpen, setConfirmOpen] = useState(false);

  const mutation = useMutation({
    mutationFn: () =>
      apiFetch<BackfillResponse>(
        `/api/instruments/${instrumentId}/backfill`,
        { method: "POST" }
      ),
    onSuccess: (data) => {
      if (data.status === "no_transactions") {
        toast.info("Nothing to backfill — record a transaction first.");
        return;
      }
      if (data.status === "manual_history_required") {
        toast.info("This price source requires manual NAV entries.");
        return;
      }
      const inserted = data.inserted_prices ?? 0;
      const fx = data.inserted_fx_rates ?? 0;
      toast.success(
        inserted > 0
          ? `Backfilled ${inserted} price${inserted === 1 ? "" : "s"}` +
              (fx > 0 ? ` and ${fx} FX rate${fx === 1 ? "" : "s"}.` : ".")
          : "No new prices — history was already complete."
      );
      // Refresh anything that reads the price-quote / networth surface.
      qc.invalidateQueries({ queryKey: ["networth"] });
      qc.invalidateQueries({ queryKey: ["nav-history", instrumentId] });
      qc.invalidateQueries({ queryKey: ["holdings"] });
      qc.invalidateQueries({ queryKey: ["instrument", instrumentId] });
    },
    onError: (err: Error) => {
      // The backend returns 429 with a friendly `detail` when the upstream
      // price provider is rate-limited; surface that directly. Other errors
      // get the generic message.
      if (err instanceof ApiError && err.status === 429) {
        const friendly = parseDetail(err.detail) ?? "Rate limited — try again later.";
        toast.error(friendly);
        return;
      }
      toast.error(`Backfill failed. ${err.message}`);
    },
  });

  if (hideForSource) return null;

  const dialogSymbol = symbol ?? "this instrument";
  const dialogEarliest =
    earliestFirstTxnDate === undefined ? null : earliestFirstTxnDate;
  const dialogCalls = estimatedApiCalls ?? 1;

  return (
    <>
      <Button
        variant="outline"
        size="sm"
        onClick={() => setConfirmOpen(true)}
        disabled={mutation.isPending}
      >
        {mutation.isPending ? (
          <Loader2 className="size-3.5 animate-spin" aria-hidden="true" />
        ) : (
          <RefreshCw className="size-3.5" aria-hidden="true" />
        )}
        <span className="ml-1.5">Backfill prices</span>
      </Button>
      <BackfillConfirmDialog
        mode="single"
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        onConfirm={() => {
          setConfirmOpen(false);
          mutation.mutate();
        }}
        isPending={mutation.isPending}
        symbol={dialogSymbol}
        earliestFirstTxnDate={dialogEarliest}
        estimatedApiCalls={dialogCalls}
      />
    </>
  );
}
