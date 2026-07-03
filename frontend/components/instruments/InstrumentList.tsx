"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { Eye, MoreVertical, RefreshCw, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { BackfillConfirmDialog } from "@/components/instruments/BackfillConfirmDialog";
import { CreateInstrumentDialog } from "@/components/instruments/CreateInstrumentDialog";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { ApiError, apiFetch } from "@/lib/api-client";
import { priceSourceLabel } from "@/lib/format";

interface Instrument {
  id: string;
  symbol: string;
  name: string;
  instrument_type: string;
  base_currency: string;
  price_source: string;
  ticker_override: string | null;
  display_decimals: number | null;
}

// Mirrors the gating used on the detail page's BackfillButton — these
// price sources require manual NAV entries and have no automatic
// backfill endpoint to call.
const MANUAL_PRICE_SOURCES = new Set(["ft", "manual", "na"]);

// UX-H9: hide test-fixture instruments by default. Symbol regex is
// ordered longest-prefix-first so TEST-AUTO-… matches before TEST-…
// and DBG2 before DBG. Combined with `price_source === "na"` the
// classifier is conservative — a real holding named "INSTACART" with a
// real price source (finnhub/coingecko) won't match.
const TEST_FIXTURE_SYMBOL_RE = /^(TEST-AUTO|AUTO|DBG2|DBG|RST|INST|MNT|CNT|TG|TIM|TEST)-/i;

function parseDetail(raw: string): string | null {
  try {
    const parsed = JSON.parse(raw);
    return typeof parsed?.detail === "string" ? parsed.detail : null;
  } catch {
    return null;
  }
}

function LoadingRows() {
  return (
    <>
      <div className="hidden md:block space-y-2 py-2">
        {[0, 1, 2, 3, 4].map((i) => (
          <Skeleton key={i} className="h-10 w-full" />
        ))}
      </div>
      <div className="space-y-3 md:hidden py-2">
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-20 w-full rounded-lg" />
        ))}
      </div>
    </>
  );
}

export function InstrumentList() {
  const qc = useQueryClient();
  const [createOpen, setCreateOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<Instrument | null>(null);
  const [backfillTarget, setBackfillTarget] = useState<Instrument | null>(null);
  const [showHidden, setShowHidden] = useState(false);

  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ["instruments"],
    queryFn: () => apiFetch<Instrument[]>("/api/instruments"),
    staleTime: 30_000,
  });

  // UX-H9: filter out test-fixture instruments unless the user opts in.
  // Filtering is purely client-side — DB rows are untouched, and the toggle
  // restores them. Combining the symbol-regex AND `price_source === "na"`
  // is the precision gate that prevents false-positives on real holdings.
  const { visibleInstruments, hiddenCount } = useMemo(() => {
    if (!data) return { visibleInstruments: [] as Instrument[], hiddenCount: 0 };
    const isHidden = (row: Instrument) =>
      TEST_FIXTURE_SYMBOL_RE.test(row.symbol) && row.price_source === "na";
    const hiddenCount = data.filter(isHidden).length;
    const visibleInstruments = showHidden ? data : data.filter((r) => !isHidden(r));
    return { visibleInstruments, hiddenCount };
  }, [data, showHidden]);

  const backfillMutation = useMutation({
    mutationFn: (instrumentId: string) =>
      apiFetch<{ status: string; inserted_prices?: number; inserted_fx_rates?: number }>(
        `/api/instruments/${instrumentId}/backfill`,
        { method: "POST" }
      ),
    onSuccess: (resp, instrumentId) => {
      if (resp.status === "no_transactions") {
        toast.info("Nothing to backfill — record a transaction first.");
        return;
      }
      if (resp.status === "manual_history_required") {
        toast.info("This price source requires manual NAV entries.");
        return;
      }
      const inserted = resp.inserted_prices ?? 0;
      const fx = resp.inserted_fx_rates ?? 0;
      toast.success(
        inserted > 0
          ? `Backfilled ${inserted} price${inserted === 1 ? "" : "s"}` +
              (fx > 0 ? ` and ${fx} FX rate${fx === 1 ? "" : "s"}.` : ".")
          : "No new prices — history was already complete."
      );
      qc.invalidateQueries({ queryKey: ["instrument", instrumentId] });
      qc.invalidateQueries({ queryKey: ["networth"] });
      qc.invalidateQueries({ queryKey: ["holdings"] });
    },
    onError: (err: Error) => {
      if (err instanceof ApiError) {
        const friendly = parseDetail(err.detail);
        toast.error(friendly ?? `Backfill failed. ${err.message}`);
        return;
      }
      toast.error(`Backfill failed. ${err.message}`);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (instrumentId: string) =>
      apiFetch<void>(`/api/instruments/${instrumentId}`, { method: "DELETE" }),
    onSuccess: (_void, instrumentId) => {
      toast.success("Instrument deleted.");
      qc.setQueryData<Instrument[]>(["instruments"], (old) =>
        old ? old.filter((i) => i.id !== instrumentId) : old
      );
      qc.invalidateQueries({ queryKey: ["instruments"] });
      setDeleteTarget(null);
    },
    onError: (err: Error) => {
      if (err instanceof ApiError) {
        const friendly = parseDetail(err.detail);
        // Backend returns 4xx with a friendly detail when transactions
        // still reference the instrument.
        toast.error(friendly ?? `Delete failed. ${err.message}`);
        return;
      }
      toast.error(`Delete failed. ${err.message}`);
    },
  });

  const ctaButton = (
    <Button onClick={() => setCreateOpen(true)}>Add instrument</Button>
  );

  const toolbar = (
    <div className="mt-6 flex items-center justify-between gap-4 flex-wrap">
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Switch
          id="show-hidden-instruments"
          checked={showHidden}
          onCheckedChange={setShowHidden}
        />
        <Label htmlFor="show-hidden-instruments" className="cursor-pointer">
          Show hidden test instruments
        </Label>
        {hiddenCount > 0 && !showHidden && (
          <span className="text-xs">({hiddenCount} hidden)</span>
        )}
      </div>
      {ctaButton}
    </div>
  );

  const createDialog = (
    <CreateInstrumentDialog
      open={createOpen}
      onOpenChange={setCreateOpen}
      onCreated={() => {
        qc.invalidateQueries({ queryKey: ["instruments"] });
      }}
    />
  );

  const deleteDialog = (
    <Dialog
      open={deleteTarget != null}
      onOpenChange={(open) => {
        if (!open) setDeleteTarget(null);
      }}
    >
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>Delete instrument?</DialogTitle>
          <DialogDescription>
            {deleteTarget ? (
              <>
                This permanently removes{" "}
                <span className="font-medium text-foreground">
                  {deleteTarget.symbol}
                </span>{" "}
                ({deleteTarget.name}). Deletion is blocked if any transactions
                still reference this instrument — delete those transactions
                first.
              </>
            ) : null}
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => setDeleteTarget(null)}
            disabled={deleteMutation.isPending}
          >
            Cancel
          </Button>
          <Button
            variant="destructive"
            onClick={() => {
              if (deleteTarget) deleteMutation.mutate(deleteTarget.id);
            }}
            disabled={deleteMutation.isPending}
          >
            {deleteMutation.isPending ? "Deleting…" : "Delete"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );

  if (isLoading) return <LoadingRows />;

  if (isError) {
    return (
      <div className="py-4 space-y-2">
        <p className="text-sm text-destructive">
          Could not load instruments.{" "}
          {error instanceof Error ? error.message : String(error)}
        </p>
        <Button variant="outline" size="sm" onClick={() => refetch()}>
          Retry
        </Button>
      </div>
    );
  }

  if (!data || data.length === 0) {
    return (
      <>
        {toolbar}
        <div className="mt-8 rounded-lg border border-border bg-card p-8 text-center">
          <h2 className="text-base font-semibold">No instruments yet</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Add the stocks, ETFs, funds, crypto, or stablecoins you hold so you
            can record transactions against them.
          </p>
          <div className="mt-4 flex justify-center">
            <Button onClick={() => setCreateOpen(true)}>Add instrument</Button>
          </div>
        </div>
        {createDialog}
      </>
    );
  }

  return (
    <>
      {toolbar}

      {/* Desktop table */}
      <div className="hidden md:block mt-4">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Symbol</TableHead>
              <TableHead>Name</TableHead>
              <TableHead>Type</TableHead>
              <TableHead>Base ccy</TableHead>
              <TableHead>Price source</TableHead>
              <TableHead className="w-[80px]">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {visibleInstruments.map((row) => {
              const isManual = MANUAL_PRICE_SOURCES.has(row.price_source);
              return (
                <TableRow key={row.id}>
                  <TableCell className="font-medium">
                    <Link
                      href={`/holdings/i/${row.id}`}
                      className="underline-offset-2 hover:underline"
                    >
                      {row.symbol}
                    </Link>
                  </TableCell>
                  <TableCell>{row.name}</TableCell>
                  <TableCell className="capitalize">
                    {row.instrument_type}
                  </TableCell>
                  <TableCell>{row.base_currency}</TableCell>
                  <TableCell>{priceSourceLabel(row.price_source)}</TableCell>
                  <TableCell>
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button
                          variant="ghost"
                          size="icon"
                          aria-label="Row actions"
                        >
                          <MoreVertical
                            className="size-4"
                            aria-hidden="true"
                          />
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end">
                        <DropdownMenuItem asChild>
                          <Link href={`/holdings/i/${row.id}`}>
                            <Eye className="size-4" />
                            <span>View detail</span>
                          </Link>
                        </DropdownMenuItem>
                        {!isManual && (
                          <DropdownMenuItem
                            onClick={() => setBackfillTarget(row)}
                            disabled={backfillMutation.isPending}
                          >
                            <RefreshCw className="size-4" />
                            <span>Backfill prices</span>
                          </DropdownMenuItem>
                        )}
                        <DropdownMenuItem
                          variant="destructive"
                          onClick={() => setDeleteTarget(row)}
                        >
                          <Trash2 className="size-4" />
                          <span>Delete instrument</span>
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </div>

      {/* Mobile card stack */}
      <div className="space-y-3 md:hidden mt-4">
        {visibleInstruments.map((row) => (
          <Link
            key={row.id}
            href={`/holdings/i/${row.id}`}
            className="block rounded-lg border border-border bg-card p-4 hover:bg-muted/40"
          >
            <div className="flex items-baseline justify-between gap-2">
              <span className="font-semibold">{row.symbol}</span>
              <span className="text-xs text-muted-foreground capitalize">
                {row.instrument_type}
              </span>
            </div>
            <div className="mt-1 text-sm text-muted-foreground truncate">
              {row.name}
            </div>
            <div className="mt-1 text-xs text-muted-foreground">
              {row.base_currency} · {priceSourceLabel(row.price_source)}
            </div>
          </Link>
        ))}
      </div>

      {createDialog}
      {deleteDialog}
      <BackfillConfirmDialog
        mode="single"
        open={backfillTarget != null}
        onOpenChange={(open) => {
          if (!open) setBackfillTarget(null);
        }}
        onConfirm={() => {
          if (backfillTarget) {
            const target = backfillTarget;
            setBackfillTarget(null);
            backfillMutation.mutate(target.id);
          }
        }}
        isPending={backfillMutation.isPending}
        symbol={backfillTarget?.symbol ?? ""}
        earliestFirstTxnDate={null}
        estimatedApiCalls={1}
      />
    </>
  );
}
