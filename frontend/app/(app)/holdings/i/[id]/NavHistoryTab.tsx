"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Trash2 } from "lucide-react";
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
import { ManualNavForm } from "@/components/forms/ManualNavForm";
import { apiFetch } from "@/lib/api-client";
import { formatMoney } from "@/lib/format";
import { toast } from "sonner";

interface Instrument {
  id: string;
  name: string;
  base_currency: "EUR" | "USD";
}
interface PriceQuote {
  id: string;
  date: string;
  price: string;
  currency: "EUR" | "USD";
  source: string;
  fetched_at: string;
}

export function NavHistoryTab({ instrumentId }: { instrumentId: string }) {
  const qc = useQueryClient();
  const { data: instrument } = useQuery({
    queryKey: ["instrument", instrumentId],
    queryFn: () => apiFetch<Instrument>(`/api/instruments/${instrumentId}`),
  });
  const { data: history, isLoading } = useQuery({
    queryKey: ["nav-history", instrumentId],
    queryFn: () =>
      // order=desc keeps the existing "newest 50 manual NAVs" UX since the
      // endpoint's default ordering is now ASC (matches the chart's reading
      // order).
      apiFetch<PriceQuote[]>(
        `/api/prices/${instrumentId}/history?source=manual&limit=50&order=desc`,
      ),
  });

  const deleteMutation = useMutation({
    mutationFn: (quoteId: string) =>
      apiFetch(`/api/prices/manual/${quoteId}`, { method: "DELETE" }),
    onSuccess: () => {
      toast.success("NAV override deleted.");
      qc.invalidateQueries({ queryKey: ["nav-history", instrumentId] });
      qc.invalidateQueries({ queryKey: ["holdings"] });
    },
    onError: (err: Error) => toast.error(`Could not delete. ${err.message}`),
  });

  if (!instrument) return <Skeleton className="h-32 w-full" />;

  return (
    <div className="space-y-8">
      <section>
        <h2 className="text-2xl font-semibold leading-tight">Add NAV override</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Use this when FT.com scraping fails or to enter a fund FT does not cover.
        </p>
        <div className="mt-4">
          <ManualNavForm
            instrumentId={instrumentId}
            instrumentName={instrument.name}
            baseCurrency={instrument.base_currency}
          />
        </div>
      </section>
      <section>
        <h2 className="text-2xl font-semibold leading-tight">History</h2>
        {isLoading ? (
          <Skeleton className="mt-4 h-40 w-full" />
        ) : !history || history.length === 0 ? (
          <p className="mt-4 text-sm text-muted-foreground">
            No manual NAV overrides yet. Add one when FT.com scraping fails for this fund.
          </p>
        ) : (
          <Table className="mt-4">
            <TableHeader>
              <TableRow>
                <TableHead>Date</TableHead>
                <TableHead className="text-right">Price</TableHead>
                <TableHead className="text-right">Saved at</TableHead>
                <TableHead></TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {history.slice(0, 5).map((q) => (
                <TableRow key={q.id}>
                  <TableCell>{q.date}</TableCell>
                  <TableCell className="text-right tabular-nums">
                    {formatMoney(q.price, q.currency)}
                  </TableCell>
                  <TableCell className="text-right text-xs text-muted-foreground">
                    {new Date(q.fetched_at).toLocaleString("en-GB")}
                  </TableCell>
                  <TableCell>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="min-h-11"
                      onClick={() => deleteMutation.mutate(q.id)}
                      aria-label={`Delete NAV override for ${q.date}`}
                    >
                      <Trash2 className="size-4" />
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </section>
    </div>
  );
}
