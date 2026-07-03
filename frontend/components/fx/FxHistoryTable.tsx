"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Skeleton } from "@/components/ui/skeleton";
import { FxTimeframeSelect, type Timeframe } from "@/components/fx/FxTimeframeSelect";
import { apiFetch } from "@/lib/api-client";
import { priceSourceLabel, STALE_MS } from "@/lib/format";

interface FxRate {
  id: string;
  date: string;
  base_currency: "EUR" | "USD";
  quote_currency: "EUR" | "USD";
  rate: string; // Decimal-as-string
  source: "frankfurter" | "manual";
  fetched_at: string;
}

const PAGE_SIZE = 50;

// Hours-to-days boundary, derived from the shared STALE_MS so it cannot drift
// from the staleness threshold (both are the same 48h numeric boundary).
const STALE_HOURS = STALE_MS / (3600 * 1000);

/**
 * Relative-age label used in the FX history table ("just now" / "Xm ago" /
 * "Xh ago" / "Xd ago"). Intentionally distinct from `formatRelativeHours`
 * in `lib/format.ts`, which returns the compact "Xh Ym" form used by the
 * staleness badges.
 *
 * The 48h hours-to-days boundary uses `<` so exactly 48 hours
 * displays as "2d ago" (not "48h ago"). The boundary references STALE_MS
 * (via STALE_HOURS) so it stays in lock-step with the staleness threshold.
 */
function formatRelativeAgo(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime();
  if (ms < 60_000) return "just now";
  const minutes = Math.floor(ms / 60_000);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < STALE_HOURS) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

export function FxHistoryTable() {
  const [timeframe, setTimeframe] = useState<Timeframe>("3m");
  const [page, setPage] = useState(0);

  const { data, isLoading, isError } = useQuery({
    queryKey: ["fx-history", timeframe, page],
    queryFn: () =>
      apiFetch<FxRate[]>(
        `/api/fx?timeframe=${timeframe}&limit=${PAGE_SIZE}&offset=${page * PAGE_SIZE}`,
      ),
  });

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <FxTimeframeSelect
          value={timeframe}
          onChange={(tf) => {
            setTimeframe(tf);
            setPage(0);
          }}
        />
        <div className="text-xs text-muted-foreground">
          Page {page + 1} · {PAGE_SIZE} rows per page
        </div>
      </div>

      {isLoading ? (
        <Skeleton className="h-96 w-full" />
      ) : isError ? (
        <p className="text-sm text-destructive">
          Could not load FX history. Check the backend connection and try again.
        </p>
      ) : !data || data.length === 0 ? (
        <div className="rounded-lg border border-border bg-card p-6 text-center">
          <h2 className="text-base font-semibold">No FX rates yet</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            The daily refresh runs at 17:00 UTC. Rates will appear here once the cron has executed.
          </p>
        </div>
      ) : (
        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Date</TableHead>
                <TableHead className="text-right">Rate (USD per EUR)</TableHead>
                <TableHead>Source</TableHead>
                <TableHead className="text-right">Fetched at</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.map((row) => (
                <TableRow key={row.id}>
                  <TableCell className="tabular-nums">{row.date}</TableCell>
                  <TableCell className="text-right tabular-nums">{row.rate}</TableCell>
                  <TableCell>{priceSourceLabel(row.source)}</TableCell>
                  <TableCell className="text-right text-xs text-muted-foreground">
                    {formatRelativeAgo(row.fetched_at)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}

      <div className="flex items-center justify-end gap-2">
        <Button
          variant="ghost"
          size="sm"
          className="min-h-11"
          onClick={() => setPage((p) => Math.max(0, p - 1))}
          disabled={page === 0 || isLoading}
        >
          Previous
        </Button>
        <Button
          variant="ghost"
          size="sm"
          className="min-h-11"
          onClick={() => setPage((p) => p + 1)}
          disabled={!data || data.length < PAGE_SIZE || isLoading}
        >
          Next
        </Button>
      </div>
    </div>
  );
}
