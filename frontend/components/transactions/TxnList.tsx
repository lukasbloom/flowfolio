"use client";

import { useLayoutEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useWindowVirtualizer } from "@tanstack/react-virtual";
import { Link as LinkIcon } from "lucide-react";
import { useSearchParams } from "next/navigation";

import { useMediaQuery } from "@/lib/use-media-query";

import { AuditHistoryModal } from "@/components/transactions/AuditHistoryModal";
import { DeleteConfirmDialog } from "@/components/transactions/DeleteConfirmDialog";
import { EditTxnDialog } from "@/components/transactions/EditTxnDialog";
import { ShowDeletedSwitch } from "@/components/transactions/ShowDeletedSwitch";
import { ShowYieldSwitch } from "@/components/transactions/ShowYieldSwitch";
import { TxnRowActions } from "@/components/transactions/TxnRowActions";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { apiFetch } from "@/lib/api-client";
import { decimalsFor, formatMoney, formatQuantity } from "@/lib/format";
import { cn } from "@/lib/utils";

export interface Transaction {
  id: string;
  txn_type: string;
  account_id: string;
  account_name?: string;
  instrument_id: string;
  instrument_symbol: string;
  // Hydrated alongside instrument_symbol on the
  // GET /api/transactions response so the ledger row can format
  // quantity at the right precision.
  instrument_type?: string | null;
  display_decimals?: number | null;
  date: string;
  quantity: string;
  unit_price: string;
  price_currency: "EUR" | "USD" | null;
  fx_rate_to_eur: string | null;
  fee_eur: string | null;
  notes: string | null;
  deleted_at: string | null;
  trade_pair_id: string | null;
  lot_alloc_count?: number;
  cost_basis_eur?: string | null;
}

// Shared grid-template-columns string so the sticky header and every virtual
// row line up perfectly. CSS-grid "fake table" replaces the previous shadcn
// <Table> primitives so we can virtualize without breaking sticky-header
// alignment.
const DESKTOP_GRID_COLS =
  "grid-cols-[110px_minmax(120px,1fr)_minmax(100px,1fr)_120px_minmax(120px,1fr)_minmax(120px,1fr)_90px_minmax(160px,1.5fr)_140px]";

function LoadingRows() {
  return (
    <>
      {/* Desktop skeleton */}
      <div className="hidden md:block space-y-2 py-2">
        {[0, 1, 2, 3, 4].map((i) => (
          <Skeleton key={i} className="h-10 w-full" />
        ))}
      </div>
      {/* Mobile skeleton */}
      <div className="space-y-3 md:hidden py-2">
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-20 w-full rounded-lg" />
        ))}
      </div>
    </>
  );
}

interface TxnListProps {
  /**
   * Programmatic filter override for the instrument detail page.
   * When provided, takes precedence over the URL `?instrument_id` query — the detail
   * page passes the instrument from its route param without rewriting the URL.
   * `/track` keeps using the URL-driven filter (no prop = read from search params).
   */
  instrumentId?: string;
}

export function TxnList({ instrumentId: instrumentIdProp }: TxnListProps = {}) {
  const search = useSearchParams();
  const includeDeleted = search.get("deleted") === "1";
  // hide_yield is a client-side filter only — intentionally NOT part of the
  // React Query cache key below so flipping the switch never refetches.
  // (The backend has no equivalent "exclude yield" param; we just hide
  // already-fetched yield rows in the rendered slice.)
  const hideYield = search.get("hide_yield") === "1";
  const instrumentId = instrumentIdProp ?? search.get("instrument_id") ?? undefined;

  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ["transactions", { includeDeleted, instrumentId }],
    queryFn: () =>
      apiFetch<Transaction[]>(
        `/api/transactions?include_deleted=${includeDeleted}${instrumentId ? `&instrument_id=${instrumentId}` : ""}`
      ),
  });

  // Post-fetch client-side filter. The full `data` array is still used by
  // DeleteConfirmDialog (for paired-trade lookup of hidden partners) and by
  // the empty-state branch (so a user who hides yield but actually has zero
  // transactions still sees the "no transactions yet" card, not "all hidden").
  const visibleRows = (data ?? []).filter(
    (r) => !(hideYield && r.txn_type === "yield")
  );

  const [editTxnId, setEditTxnId] = useState<string | null>(null);
  const [editDrawerOpen, setEditDrawerOpen] = useState(false);
  const [historyTxnId, setHistoryTxnId] = useState<string | null>(null);
  const [deleteTxn, setDeleteTxn] = useState<Transaction | null>(null);

  // Per-row focus return ref — captured when the row's Edit button is clicked
  // so EditTxnDialog can restore focus on close.
  const triggerRef = useRef<HTMLButtonElement | null>(null);

  // Scroll-container refs for the two virtualizer instances. The desktop and
  // mobile branches each own their own scroll container with its own row-height
  // estimate, but both pull from the same `data` array — so filters and edge
  // states stay shared.
  // Window-scrolling virtualizers: the document/window is the scroll host, so
  // the page scrolls naturally (no nested scrollbar trapped on the ledger).
  // Each list element's offsetTop is captured into state via useLayoutEffect
  // and fed in as scrollMargin so the virtualizer knows when the list rolls
  // into the viewport. Reading ref.current directly during render is forbidden
  // by react-hooks/refs, hence the state round-trip.
  const desktopParentRef = useRef<HTMLDivElement | null>(null);
  const mobileParentRef = useRef<HTMLDivElement | null>(null);
  const [desktopScrollMargin, setDesktopScrollMargin] = useState(0);
  const [mobileScrollMargin, setMobileScrollMargin] = useState(0);

  useLayoutEffect(() => {
    if (desktopParentRef.current) {
      setDesktopScrollMargin(desktopParentRef.current.offsetTop);
    }
    if (mobileParentRef.current) {
      setMobileScrollMargin(mobileParentRef.current.offsetTop);
    }
    // visibleRows is included so scroll-margin recomputes when the yield
    // filter flips the list height.
  }, [data, visibleRows]);

  const rowCount = visibleRows.length;

  // TxnList renders both a desktop table and a mobile card
  // stack so CSS media queries can swap them, but on each viewport ONE of the
  // branches sits inside `display:none` (`md:hidden` / `hidden md:block`).
  // `useWindowVirtualizer` measures each rendered row's `offsetHeight`, which
  // is 0 for any element inside a `display:none` ancestor — so the inactive
  // virtualizer keeps mounting more rows trying to fill the viewport and
  // settles at ~190 hidden cards (each one a Radix DropdownMenu). That mount
  // costs ~2.6s on a real ledger. Gating the inactive virtualizer's `count`
  // to 0 prevents the hidden branch from rendering any rows at all.
  // `useMediaQuery` (built on `useSyncExternalStore`) gives the correct value
  // on the first client render, so we never mount the inactive branch.
  const isDesktop = useMediaQuery("(min-width: 768px)");

  const desktopVirtualizer = useWindowVirtualizer({
    count: isDesktop ? rowCount : 0,
    estimateSize: () => 44,
    overscan: 8,
    scrollMargin: desktopScrollMargin,
  });

  const mobileVirtualizer = useWindowVirtualizer({
    count: isDesktop ? 0 : rowCount,
    estimateSize: () => 100,
    overscan: 6,
    scrollMargin: mobileScrollMargin,
  });

  function openEdit(id: string, triggerEl: HTMLButtonElement) {
    triggerRef.current = triggerEl;
    setEditTxnId(id);
    setEditDrawerOpen(true);
  }

  function closeEditDrawer() {
    setEditDrawerOpen(false);
    setEditTxnId(null);
  }

  // Build a lookup map from trade_pair_id → partner symbol.
  // Iterates `visibleRows` so a hidden yield row never matches a pair (paired-
  // trade lookup against hidden rows would surface a dangling pair icon).
  // Single pass: group rows by trade_pair_id, then resolve each row's partner
  // from its group. O(n) instead of the previous O(n²).
  const tradePairSymbols = useMemo<Record<string, string>>(() => {
    const byPair = new Map<string, Transaction[]>();
    for (const row of visibleRows) {
      if (!row.trade_pair_id) continue;
      const group = byPair.get(row.trade_pair_id);
      if (group) group.push(row);
      else byPair.set(row.trade_pair_id, [row]);
    }
    const result: Record<string, string> = {};
    for (const group of byPair.values()) {
      if (group.length < 2) continue;
      for (const row of group) {
        const partner = group.find((other) => other.id !== row.id);
        if (partner) result[row.id] = partner.instrument_symbol;
      }
    }
    return result;
  }, [visibleRows]);

  if (isLoading) return <LoadingRows />;

  if (isError) {
    return (
      <div className="py-4 space-y-2">
        <p className="text-sm text-destructive">
          Could not load transactions.{" "}
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
        <div className="mt-6 flex items-center gap-4 flex-wrap">
          <ShowDeletedSwitch />
          <ShowYieldSwitch />
        </div>
        <div className="mt-8 rounded-lg border border-border bg-card p-8 text-center">
          <h2 className="text-base font-semibold">No transactions yet</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Add a transaction, trade, or spend from the{" "}
            <span className="font-medium text-foreground">+ Add</span> button in
            the header to start your ledger.
          </p>
        </div>
        <EditTxnDialog
          txnId={editTxnId}
          mode="edit"
          open={editDrawerOpen}
          onClose={closeEditDrawer}
          triggerRef={triggerRef}
        />
      </>
    );
  }

  // Filter-emptied branch: rows exist, but the active client-side filters
  // (Show yield off / Show deleted off) have hidden every one. Surface which
  // filters did the hiding so the user can flip them back on without leaving
  // the page. Counts are computed from the unfiltered `data` array.
  if (visibleRows.length === 0) {
    const hiddenYield = data.filter((r) => r.txn_type === "yield").length;
    const hiddenDeleted = data.filter((r) => r.deleted_at != null).length;
    const yieldExplains = hideYield && hiddenYield > 0;
    const deletedExplains = !includeDeleted && hiddenDeleted > 0;

    const clauses: string[] = [];
    if (yieldExplains) {
      clauses.push(
        `Show yield is off — ${hiddenYield} yield row${hiddenYield === 1 ? "" : "s"} hidden.`
      );
    }
    if (deletedExplains) {
      clauses.push(
        `Show deleted is off — ${hiddenDeleted} deleted row${hiddenDeleted === 1 ? "" : "s"} hidden.`
      );
    }
    const explanation =
      clauses.length > 0
        ? clauses.join(" · ")
        : "All transactions are hidden by your current filters.";

    return (
      <>
        <div className="mt-6 flex items-center gap-4 flex-wrap">
          <ShowDeletedSwitch />
          <ShowYieldSwitch />
        </div>
        <div className="mt-8 rounded-lg border border-border bg-card p-8 text-center">
          <h2 className="text-base font-semibold">All transactions filtered out</h2>
          <p className="mt-1 text-sm text-muted-foreground">{explanation}</p>
        </div>
        <EditTxnDialog
          txnId={editTxnId}
          mode="edit"
          open={editDrawerOpen}
          onClose={closeEditDrawer}
          triggerRef={triggerRef}
        />
        <AuditHistoryModal
          txnId={historyTxnId}
          onClose={() => setHistoryTxnId(null)}
        />
        <DeleteConfirmDialog
          txn={deleteTxn}
          allTransactions={data}
          onClose={() => setDeleteTxn(null)}
        />
      </>
    );
  }

  return (
    <TooltipProvider>
      <div className="mt-6 flex items-center gap-4 flex-wrap">
        <ShowDeletedSwitch />
        <ShowYieldSwitch />
      </div>

      {/* Desktop virtualized table (CSS-grid "fake table"). Window-scrolling
          virtualizer: the page scrolls naturally; the table header sticks to
          top-14 so it sits just below the 56px-tall AppHeader (which is itself
          sticky-top-0). */}
      <div
        ref={desktopParentRef}
        className="hidden md:block mt-4 rounded-md border border-border"
        role="table"
        aria-label="Transactions"
      >
        {/* Sticky header row */}
        <div
          role="row"
          className={cn(
            "sticky top-14 z-10 grid gap-3 bg-background border-b border-border px-3 py-2 text-xs font-medium text-muted-foreground",
            DESKTOP_GRID_COLS
          )}
        >
          <div role="columnheader">Date</div>
          <div role="columnheader">Account</div>
          <div role="columnheader">Instrument</div>
          <div role="columnheader">Type</div>
          <div role="columnheader" className="text-right">
            Qty
          </div>
          <div role="columnheader" className="text-right">
            Price
          </div>
          <div role="columnheader" className="text-right">
            FX
          </div>
          <div role="columnheader">Notes</div>
          <div role="columnheader">Actions</div>
        </div>

        {/* Virtualized body */}
        <div
          role="rowgroup"
          style={{
            height: desktopVirtualizer.getTotalSize(),
            position: "relative",
          }}
        >
          {desktopVirtualizer.getVirtualItems().map((vi) => {
            const row = visibleRows[vi.index];
            const deleted = row.deleted_at != null;
            const pairedSymbol = row.trade_pair_id
              ? tradePairSymbols[row.id]
              : null;
            const isAutoAccrual =
              row.txn_type === "yield" &&
              row.notes != null &&
              /^auto-accrual\s+/.test(row.notes);

            return (
              <div
                key={row.id}
                role="row"
                data-index={vi.index}
                ref={desktopVirtualizer.measureElement}
                // Subtract scrollMargin so item positions are local to the
                // spacer wrapper (vi.start is document-relative in window mode).
                style={{
                  position: "absolute",
                  top: 0,
                  left: 0,
                  width: "100%",
                  transform: `translateY(${vi.start - desktopScrollMargin}px)`,
                }}
                className={cn(
                  "grid gap-3 items-center px-3 py-2 border-b border-border text-sm",
                  DESKTOP_GRID_COLS,
                  deleted && "opacity-60"
                )}
              >
                <div
                  role="cell"
                  className={cn("relative", deleted && "line-through")}
                >
                  {deleted && (
                    <span className="absolute inset-y-0 left-0 w-0.5 bg-destructive" />
                  )}
                  {row.date}
                </div>
                <div role="cell" className={cn(deleted && "line-through")}>
                  {row.account_name ?? row.account_id}
                </div>
                <div role="cell" className={cn(deleted && "line-through")}>
                  {row.instrument_symbol}
                </div>
                <div role="cell" className={cn(deleted && "line-through")}>
                  <span className="flex items-center gap-1">
                    <span className="capitalize">{row.txn_type}</span>
                    {pairedSymbol && (
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <span className="inline-flex cursor-default">
                            <LinkIcon
                              className="size-3 text-muted-foreground"
                              aria-hidden="true"
                            />
                          </span>
                        </TooltipTrigger>
                        <TooltipContent>
                          Linked trade — paired with {pairedSymbol}
                        </TooltipContent>
                      </Tooltip>
                    )}
                  </span>
                </div>
                <div
                  role="cell"
                  className={cn(
                    "text-right tabular-nums",
                    deleted && "line-through"
                  )}
                >
                  {formatQuantity(
                    row.quantity,
                    decimalsFor({
                      instrumentType: row.instrument_type,
                      displayDecimals: row.display_decimals,
                    })
                  )}
                </div>
                <div
                  role="cell"
                  className={cn(
                    "text-right tabular-nums",
                    deleted && "line-through"
                  )}
                >
                  {row.price_currency
                    ? formatMoney(row.unit_price, row.price_currency)
                    : "—"}
                </div>
                <div
                  role="cell"
                  className={cn(
                    "text-right tabular-nums",
                    deleted && "line-through"
                  )}
                >
                  {row.fx_rate_to_eur ?? "—"}
                </div>
                <div
                  role="cell"
                  className={cn("min-w-0", deleted && "line-through")}
                >
                  {isAutoAccrual && row.notes ? (
                    <div
                      className="flex items-center gap-2 min-w-0"
                      title={row.notes ?? undefined}
                    >
                      <Badge variant="outline" className="shrink-0">
                        auto-accrual
                      </Badge>
                      <span className="truncate text-muted-foreground">
                        {row.notes.replace(/^auto-accrual\s+/, "")}
                      </span>
                    </div>
                  ) : (
                    <span
                      className="block truncate text-muted-foreground"
                      title={row.notes ?? undefined}
                    >
                      {row.notes ?? ""}
                    </span>
                  )}
                </div>
                <div role="cell">
                  <div className="flex items-center gap-1">
                    {deleted && (
                      <Badge
                        variant="outline"
                        className="border-destructive text-destructive"
                      >
                        Deleted
                      </Badge>
                    )}
                    <TxnRowActions
                      txnId={row.id}
                      txnType={row.txn_type}
                      deleted={deleted}
                      onEdit={(el) => openEdit(row.id, el)}
                      onDelete={() => setDeleteTxn(row)}
                      onHistory={() => setHistoryTxnId(row.id)}
                    />
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Mobile virtualized card stack — window-scrolling, no inner scrollbar. */}
      <div
        ref={mobileParentRef}
        className="md:hidden mt-4"
      >
        <div
          style={{
            height: mobileVirtualizer.getTotalSize(),
            position: "relative",
          }}
        >
          {mobileVirtualizer.getVirtualItems().map((vi) => {
            const row = visibleRows[vi.index];
            const deleted = row.deleted_at != null;
            const pairedSymbol = row.trade_pair_id
              ? tradePairSymbols[row.id]
              : null;

            return (
              <div
                key={row.id}
                data-index={vi.index}
                ref={mobileVirtualizer.measureElement}
                style={{
                  position: "absolute",
                  top: 0,
                  left: 0,
                  width: "100%",
                  transform: `translateY(${vi.start - mobileScrollMargin}px)`,
                  // 12px of vertical gap between cards (matches space-y-3 the
                  // pre-virtualization stack had).
                  paddingBottom: 12,
                }}
              >
                <div
                  className={cn(
                    "relative rounded-lg border border-border bg-card p-4",
                    deleted && "opacity-60 border-l-2 border-l-destructive"
                  )}
                >
                  <div className="flex items-baseline justify-between gap-2">
                    <span
                      className={cn(
                        "font-semibold",
                        deleted && "line-through"
                      )}
                    >
                      {row.instrument_symbol}
                    </span>
                    <div className="flex items-center gap-1">
                      {deleted && (
                        <Badge
                          variant="outline"
                          className="border-destructive text-destructive"
                        >
                          Deleted
                        </Badge>
                      )}
                      <TxnRowActions
                        txnId={row.id}
                        txnType={row.txn_type}
                        deleted={deleted}
                        onEdit={(el) => openEdit(row.id, el)}
                        onDelete={() => setDeleteTxn(row)}
                        onHistory={() => setHistoryTxnId(row.id)}
                      />
                    </div>
                  </div>
                  <div
                    className={cn(
                      "mt-1 text-xs text-muted-foreground flex items-center gap-1",
                      deleted && "line-through"
                    )}
                  >
                    <span className="capitalize">{row.txn_type}</span>
                    {pairedSymbol && (
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <span className="inline-flex cursor-default">
                            <LinkIcon className="size-3" aria-hidden="true" />
                          </span>
                        </TooltipTrigger>
                        <TooltipContent>
                          Linked trade — paired with {pairedSymbol}
                        </TooltipContent>
                      </Tooltip>
                    )}
                    <span>·</span>
                    <span>{row.date}</span>
                    <span>·</span>
                    <span>{row.account_name ?? row.account_id}</span>
                  </div>
                  <div
                    className={cn(
                      "mt-1 text-sm tabular-nums",
                      deleted && "line-through"
                    )}
                  >
                    {formatQuantity(
                      row.quantity,
                      decimalsFor({
                        instrumentType: row.instrument_type,
                        displayDecimals: row.display_decimals,
                      })
                    )}
                    {row.price_currency
                      ? ` @ ${formatMoney(row.unit_price, row.price_currency)}`
                      : ""}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      <EditTxnDialog
        txnId={editTxnId}
        mode="edit"
        open={editDrawerOpen}
        onClose={closeEditDrawer}
        triggerRef={triggerRef}
      />
      <AuditHistoryModal
        txnId={historyTxnId}
        onClose={() => setHistoryTxnId(null)}
      />
      {/* allTransactions is intentionally the FULL unfiltered data array (not
          visibleRows) so the delete-confirm sheet can still see paired-trade
          partners even when the yield filter has hidden one side. */}
      <DeleteConfirmDialog
        txn={deleteTxn}
        allTransactions={data}
        onClose={() => setDeleteTxn(null)}
      />
    </TooltipProvider>
  );
}
