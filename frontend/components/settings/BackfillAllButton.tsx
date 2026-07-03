"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";

import {
  BackfillConfirmDialog,
  type BackfillPreview,
} from "@/components/instruments/BackfillConfirmDialog";
import { Button } from "@/components/ui/button";
import { ApiError, apiFetch } from "@/lib/api-client";
import { invalidatePortfolioCache } from "@/lib/invalidate-cache";

interface BulkBackfillItem {
  instrument_id: string;
  symbol: string;
  status: string;
  inserted_prices: number;
  skipped_existing: number;
}

interface BulkBackfillResponse {
  items: BulkBackfillItem[];
  total_inserted_prices: number;
  total_inserted_fx_rates: number;
  rate_limited_count: number;
}

export function BackfillAllButton() {
  const qc = useQueryClient();
  const [confirmOpen, setConfirmOpen] = useState(false);

  const previewQuery = useQuery({
    queryKey: ["backfill-preview"],
    queryFn: () =>
      apiFetch<BackfillPreview>("/api/instruments/backfill-preview"),
    enabled: confirmOpen,
    staleTime: 30_000,
  });

  const mutation = useMutation({
    mutationFn: () =>
      apiFetch<BulkBackfillResponse>("/api/instruments/backfill-all", {
        method: "POST",
      }),
    onSuccess: (data) => {
      const inserted = data.total_inserted_prices;
      const fx = data.total_inserted_fx_rates;
      const rl = data.rate_limited_count;
      const head =
        inserted > 0
          ? `Backfilled ${inserted} price${inserted === 1 ? "" : "s"}${
              fx > 0 ? ` and ${fx} FX rate${fx === 1 ? "" : "s"}` : ""
            }.`
          : "No new prices — history was already complete.";
      if (rl > 0) {
        toast.warning(
          `${head} ${rl} instrument${
            rl === 1 ? " was" : "s were"
          } rate-limited — try again in a few minutes.`
        );
      } else {
        toast.success(head);
      }
      // Invalidate everything that reads price-quote / networth.
      invalidatePortfolioCache(qc); // portfolio superset (networth/holdings/perf + the rest)
      // Backfill-specific extension keys (not in the portfolio superset):
      qc.invalidateQueries({ queryKey: ["nav-history"] });
      qc.invalidateQueries({ queryKey: ["instruments"] });
      qc.invalidateQueries({ queryKey: ["instrument"] });
    },
    onError: (err: Error) => {
      // The bulk endpoint returns 200 even when individual instruments
      // fail — so any 4xx/5xx surfaced here is a real outage (auth dropped,
      // DB down, etc.). ApiError vs plain Error doesn't change the copy
      // but the branch is here for future-proofing.
      if (err instanceof ApiError) {
        toast.error(`Bulk backfill failed. ${err.message}`);
        return;
      }
      toast.error(`Bulk backfill failed. ${err.message}`);
    },
  });

  return (
    <>
      <Button
        size="sm"
        onClick={() => setConfirmOpen(true)}
        disabled={mutation.isPending}
        className="shrink-0 min-h-11 sm:min-h-9"
      >
        {mutation.isPending ? (
          <Loader2 className="size-3.5 animate-spin" aria-hidden="true" />
        ) : null}
        <span className={mutation.isPending ? "ml-1.5" : undefined}>
          Backfill all prices
        </span>
      </Button>
      <BackfillConfirmDialog
        mode="bulk"
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        onConfirm={() => {
          setConfirmOpen(false);
          mutation.mutate();
        }}
        isPending={mutation.isPending}
        preview={previewQuery.data ?? null}
        isLoadingPreview={previewQuery.isLoading}
        isErrorPreview={previewQuery.isError}
      />
    </>
  );
}
