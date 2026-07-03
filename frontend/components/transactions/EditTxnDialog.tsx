"use client";

import { type RefObject } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { Info, ArrowRight } from "lucide-react";

import { TxnForm } from "@/components/transactions/TxnForm";
import { SpendForm } from "@/components/transactions/SpendForm";
import { TradeForm } from "@/components/transactions/TradeForm";
import { YieldForm } from "@/components/transactions/YieldForm";
import { ActionBanner } from "@/components/ui/action-banner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DialogClose } from "@/components/ui/dialog";
import { DialogFormFooter } from "@/components/transactions/DialogFormFooter";
import {
  ResponsiveDialog,
  ResponsiveDialogDescription,
  ResponsiveDialogHeader,
  ResponsiveDialogTitle,
} from "@/components/ui/responsive-dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { apiFetch } from "@/lib/api-client";
import { isAutoAccrualYield } from "@/lib/transactions";
import { invalidatePortfolioCache } from "@/lib/invalidate-cache";

// ---------------------------------------------------------------------------
// Local types
// ---------------------------------------------------------------------------

interface Transaction {
  id: string;
  txn_type: string;
  account_id: string;
  instrument_id: string;
  instrument_symbol: string;
  date: string;
  quantity: string;
  unit_price: string;
  price_currency: "EUR" | "USD" | null;
  fx_rate_to_eur: string | null;
  fee_eur: string | null;
  notes: string | null;
  trade_pair_id: string | null; // CRITICAL: first branching predicate
}

interface Account {
  id: string;
  name: string;
}

interface Instrument {
  id: string;
  symbol: string;
  name: string;
}

export interface Props {
  txnId: string | null;
  mode: "edit" | "create";
  open: boolean;
  onClose: () => void;
  /**
   * Element to restore focus to when the dialog closes.
   * Caller (TxnList) captures the row's TxnRowActions trigger button.
   */
  triggerRef: RefObject<HTMLButtonElement | null>;
}

// ---------------------------------------------------------------------------
// Title / description maps. Lowercase type word in dialog titles.
// ---------------------------------------------------------------------------

const TITLE_BY_TYPE_EDIT: Record<string, string> = {
  buy:   "Edit buy",
  sell:  "Edit sell",   // Unreachable in single-leg branch — Trade handles paired sells.
  spend: "Edit spend",
  yield: "Edit yield",  // Manual yield only — auto-accrual overrides to "Yield details".
};

const DESC_BY_TYPE_EDIT: Record<string, string> = {
  buy:   "Edits are recorded in history. Cost basis recalculates automatically.",
  sell:  "Edits are recorded in history. Cost basis recalculates automatically.",
  spend: "Edits are recorded in history. Cost basis recalculates automatically.",
  yield: "Edits are recorded in history.",
};

// Trade constants for the dual-chip and TradeForm body.
const TRADE_TITLE_EDIT = "Edit trade";
const TRADE_DESC_EDIT  = "Edits are recorded in history. FIFO recalculates on the sold leg.";

// ---------------------------------------------------------------------------
// Success payload discriminated union — mirrors AddTxnFormSheet pattern.
// ---------------------------------------------------------------------------

type SuccessPayload =
  | { type: "buy";   qty: string; symbol: string }
  | { type: "spend"; amount: string; currency: string; description?: string }
  | { type: "yield"; qty: string; symbol: string };

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function EditTxnDialog({ txnId, mode, open, onClose, triggerRef }: Props) {
  const qc     = useQueryClient();
  const router = useRouter();

  // Responsive primitive switch. Now owned by ResponsiveDialog
  // (and the ResponsiveDialog* header/title/description primitives), which branch
  // internally at the 768px breakpoint.

  // -------------------------------------------------------------------------
  // Data fetch — verbatim from EditTxnDrawer.tsx:45-49.
  // -------------------------------------------------------------------------

  const { data: txnData, isLoading } = useQuery({
    queryKey: ["transaction", txnId],
    queryFn:  () => apiFetch<Transaction>(`/api/transactions/${txnId}`),
    enabled:  !!txnId && mode === "edit",
  });

  const { data: accounts = [] } = useQuery({
    queryKey: ["accounts"],
    queryFn:  () => apiFetch<Account[]>("/api/accounts"),
  });

  // Instruments power the chip's symbol. The transaction payload's
  // instrument_symbol can come back null (it isn't always denormalised onto the
  // row), which rendered a literal "null" in the header. Resolving by
  // instrument_id against this list — the same source the edit form's dropdown
  // uses — yields the real ticker; the raw field is only a fallback.
  const { data: instruments = [] } = useQuery({
    queryKey: ["instruments"],
    queryFn:  () => apiFetch<Instrument[]>("/api/instruments"),
  });

  const symbolFor = (instrumentId: string, fallback: string | null) =>
    instruments.find((i) => i.id === instrumentId)?.symbol ?? fallback ?? "";

  // -------------------------------------------------------------------------
  // BRANCHING PREDICATES. Order is load-bearing.
  //
  // 1. trade_pair_id != null  → paired-trade leg (Trade branch)
  // 2. yield + auto-accrual notes prefix → read-only ActionBanner
  // 3. yield (manual)         → YieldForm
  // 4. spend                  → SpendForm
  // 5. default                → buy single-leg TxnForm
  //
  // Sells are unreachable here because every sell has a trade_pair_id
  // (sells must be paired). VALID_TXN_TYPES (backend) excludes "trade",
  // so `txn_type === "trade"` would never match. trade_pair_id is the ONLY
  // correct predicate for detecting trade legs.
  // -------------------------------------------------------------------------

  const isTrade       = !!txnData && txnData.trade_pair_id != null;
  const isAutoAccrual = !!txnData && !isTrade && isAutoAccrualYield(txnData);
  const isManualYield = !!txnData && !isTrade && txnData.txn_type === "yield" && !isAutoAccrual;
  const isSpend       = !!txnData && !isTrade && txnData.txn_type === "spend";
  const isBuy         = !!txnData && !isTrade && !isAutoAccrual && !isManualYield && !isSpend;

  // -------------------------------------------------------------------------
  // Trade-leg pairing: when txn is a trade leg, find the partner leg via
  // trade_pair_id. The TanStack Query cache holds the ["transactions"] list
  // (TxnList feeds it). We use getQueriesData with partial key matching to
  // handle the compound cache key ["transactions", { includeDeleted, ... }].
  //
  // Branching is by trade_pair_id, NOT by
  // txn_type === "trade" (no such enum value exists in VALID_TXN_TYPES).
  // -------------------------------------------------------------------------

  const pairedLeg = (() => {
    if (!txnData || !isTrade) return undefined;
    const pairId = txnData.trade_pair_id;
    // getQueriesData with partial key matches all ["transactions", ...] variants.
    const allCacheEntries = qc.getQueriesData<Transaction[]>({ queryKey: ["transactions"] });
    for (const [, txns] of allCacheEntries) {
      if (!txns) continue;
      const partner = txns.find(
        (t) => t.trade_pair_id === pairId && t.id !== txnData.id
      );
      if (partner) return partner;
    }
    return undefined;
  })();

  // Determine sold vs received leg.
  // Primary signal: txn_type ("sell" → sold, "buy" → received)
  // Fallback: quantity sign (backend stores sells with negative quantity)
  const soldLeg = !txnData
    ? undefined
    : txnData.txn_type === "sell"
      ? txnData
      : pairedLeg && pairedLeg.txn_type === "sell"
        ? pairedLeg
        : Number(txnData.quantity) < 0
          ? txnData
          : pairedLeg;

  const receivedLeg = !txnData
    ? undefined
    : txnData.txn_type === "buy" && txnData.trade_pair_id != null
      ? txnData
      : pairedLeg && pairedLeg.txn_type === "buy"
        ? pairedLeg
        : Number(txnData.quantity) > 0
          ? txnData
          : pairedLeg;

  // -------------------------------------------------------------------------
  // Title resolution
  // -------------------------------------------------------------------------

  const title = mode === "create"
    ? "Add buy"
    : isTrade
      ? TRADE_TITLE_EDIT
      : isAutoAccrual
        ? "Yield details"
        : txnData
          ? (TITLE_BY_TYPE_EDIT[txnData.txn_type] ?? "Edit transaction")
          : "Edit transaction";

  const description = mode === "create"
    ? "Edits are recorded in history. Cost basis recalculates automatically."
    : isTrade
      ? TRADE_DESC_EDIT
      : isAutoAccrual
        ? undefined // banner body carries the description
        : txnData
          ? DESC_BY_TYPE_EDIT[txnData.txn_type]
          : undefined;

  // -------------------------------------------------------------------------
  // Single-leg chip text.
  // Format: `{Type} · {Account} · {Symbol} · {YYYY-MM-DD}`
  // Separator is U+00B7 MIDDLE DOT with spaces.
  // Trade dual-chip rendered separately below.
  // -------------------------------------------------------------------------

  const accountName = accounts.find((a) => a.id === txnData?.account_id)?.name ?? txnData?.account_id ?? "";
  const txnTypeCap  = txnData ? txnData.txn_type[0].toUpperCase() + txnData.txn_type.slice(1) : "";
  // filter(Boolean) drops any segment that resolves empty (e.g. an unresolved
  // symbol) so the chip never prints a stray "null"/"undefined" token.
  const chipText    = txnData
    ? [txnTypeCap, accountName, symbolFor(txnData.instrument_id, txnData.instrument_symbol), txnData.date]
        .filter(Boolean)
        .join(" · ")
    : "";

  // -------------------------------------------------------------------------
  // Cache invalidation — 9-key contract verbatim from EditTxnDrawer.tsx:51-63.
  // -------------------------------------------------------------------------

  function handleSuccess(payload: SuccessPayload) {
    // Toast templates.
    // Em-dash is U+2014 with surrounding spaces.
    const verb = mode === "create" ? "created" : "updated";
    let message: string;
    switch (payload.type) {
      case "buy":
        message = `Buy ${verb} — ${payload.qty} ${payload.symbol}`;
        break;
      case "spend":
        message = `Spend ${verb} — ${payload.amount} ${payload.currency}`;
        break;
      case "yield":
        message = `Yield ${verb} — ${payload.qty} ${payload.symbol}`;
        break;
    }
    toast.success(message);

    invalidatePortfolioCache(qc);
    onClose();
  }

  // -------------------------------------------------------------------------
  // Banner navigation handler.
  // URL format: /holdings/i/<instrument_id>?tab=apy&account=<account_id>
  // -------------------------------------------------------------------------

  function navigateToApySource() {
    if (!txnData) return;
    router.push(`/holdings/i/${txnData.instrument_id}?tab=apy&account=${txnData.account_id}`);
    onClose();
  }

  // -------------------------------------------------------------------------
  // Render
  // -------------------------------------------------------------------------

  // Hoisted so both Dialog (desktop) and Drawer (mobile) branches share one
  // handler. CRITICAL: reads the PROPS `triggerRef` (line 63), which the
  // TxnList passes down per row. DO NOT swap to the AddTxnProvider's
  // triggerRef — that points at the FAB / AddButton, not the per-row
  // actions trigger.
  const handleCloseAutoFocus = (e: Event) => {
    e.preventDefault();
    triggerRef.current?.focus(); // per-row trigger restoration (NOT #add-trigger)
  };

  // Header + body content shared across the Dialog and Drawer branches.
  // Extracting as a fragment keeps every behaviour
  // (chip header, ActionBanner, dual-leg Trade edit, YieldForm, SpendForm,
  // TxnForm, 9-key cache invalidation) verbatim inside whichever container.
  // The Header primitive is selected per branch so a11y plumbing
  // (DialogTitle / DrawerTitle, DialogDescription / DrawerDescription)
  // is anchored to the correct Radix-/vaul-owned slot.
  // ResponsiveDialog* internally pick the Dialog vs Drawer primitive at the same
  // 768px breakpoint, so these aliases keep the existing JSX shape unchanged.
  const HeaderWrapper       = ResponsiveDialogHeader;
  const TitleWrapper        = ResponsiveDialogTitle;
  const DescriptionWrapper  = ResponsiveDialogDescription;

  const dialogChildren = (
    <>
      <HeaderWrapper>
        <TitleWrapper>{title}</TitleWrapper>

        {/* Chip group: id wired to aria-describedby above, role="group" with aria-label. */}
        <div
          id="dialog-chip"
          role="group"
          aria-label={
            isTrade && soldLeg && receivedLeg
              ? `Trade: ${symbolFor(soldLeg.instrument_id, soldLeg.instrument_symbol)} from ${accounts.find((a) => a.id === soldLeg.account_id)?.name ?? soldLeg.account_id}, swapped to ${symbolFor(receivedLeg.instrument_id, receivedLeg.instrument_symbol)} on ${accounts.find((a) => a.id === receivedLeg.account_id)?.name ?? receivedLeg.account_id}`
              : "Transaction context"
          }
          className="flex items-center flex-wrap gap-y-1"
        >
          {mode === "create" ? (
            <Badge variant="secondary" className="px-2 py-0.5 text-xs font-medium">
              New buy
            </Badge>
          ) : !txnData ? (
            <Skeleton className="h-5 w-64" />
          ) : isTrade && soldLeg && receivedLeg ? (
            // Dual-chip header for trade legs. Format: <symbol> · <account>
            // Token order: instrument first, account second (emphasises the *what*).
            // Both accounts shown explicitly even when they match (e.g. USDC · Revolut → AAPL · Revolut).
            // Arrow is U+2192, aria-hidden, text-muted-foreground, mx-2.
            <>
              <Badge variant="secondary" className="px-2 py-0.5 text-xs font-medium">
                {symbolFor(soldLeg.instrument_id, soldLeg.instrument_symbol)} · {accounts.find((a) => a.id === soldLeg.account_id)?.name ?? soldLeg.account_id}
              </Badge>
              <span aria-hidden="true" className="mx-2 text-muted-foreground">→</span>
              <Badge variant="secondary" className="px-2 py-0.5 text-xs font-medium">
                {symbolFor(receivedLeg.instrument_id, receivedLeg.instrument_symbol)} · {accounts.find((a) => a.id === receivedLeg.account_id)?.name ?? receivedLeg.account_id}
              </Badge>
            </>
          ) : isTrade ? (
            // Trade detected but paired leg still resolving from the cache
            <Skeleton className="h-5 w-64" />
          ) : (
            <Badge variant="secondary" className="px-2 py-0.5 text-xs font-medium">
              {chipText}
            </Badge>
          )}
        </div>

        {description ? <DescriptionWrapper>{description}</DescriptionWrapper> : null}
      </HeaderWrapper>

      {/* Body */}
      {/* Horizontal/bottom padding is for the mobile Drawer only (DrawerContent
          has no padding of its own). On desktop the Dialog's p-6 is the single
          padding layer, so we zero these out — otherwise fields sit 16px inset
          from the header and the title looks misaligned with the inputs. */}
      <div data-testid="dialog-body" className="mt-6 px-4 pb-4 md:px-0 md:pb-0">
        {mode === "edit" && isLoading ? (
            // Loading skeletons — matches EditTxnDrawer.tsx:157-163 verbatim.
            <div className="space-y-4">
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
            </div>
          ) : mode === "create" ? (
            // Create mode — TxnForm with no txnId / initialValues renders a blank buy form.
            <TxnForm
              suppressInnerToast
              onSuccess={({ qty, symbol }) => handleSuccess({ type: "buy", qty, symbol })}
              onCancel={onClose}
            />
          ) : !txnData ? null : isTrade ? (
            !soldLeg || !receivedLeg ? (
              // Trade detected but paired leg still resolving — show skeletons.
              <div className="space-y-4">
                <Skeleton className="h-10 w-full" />
                <Skeleton className="h-10 w-full" />
                <Skeleton className="h-10 w-full" />
              </div>
            ) : (
              // Trade edit. TradeForm in edit mode.
              // suppressInnerToast: TradeForm emits the per-leg toast via its own onSuccess handler.
              // TradeForm closes the dialog via onClose() itself; no onSuccess needed here.
              <TradeForm
                open
                chrome="inline"
                suppressInnerToast={false}
                onClose={onClose}
                txnId={soldLeg.id}
                initialValues={{
                  sold: {
                    account_id: soldLeg.account_id,
                    instrument_id: soldLeg.instrument_id,
                    quantity: String(Math.abs(Number(soldLeg.quantity))), // display positive; backend re-signs
                    unit_price: soldLeg.unit_price ?? "",
                    price_currency: soldLeg.price_currency ?? "EUR",
                    fx_rate_to_eur: soldLeg.fx_rate_to_eur ?? "",
                  },
                  received: {
                    account_id: receivedLeg.account_id,
                    instrument_id: receivedLeg.instrument_id,
                    quantity: String(Math.abs(Number(receivedLeg.quantity))),
                    unit_price: receivedLeg.unit_price ?? "",
                    price_currency: receivedLeg.price_currency ?? "EUR",
                    fx_rate_to_eur: receivedLeg.fx_rate_to_eur ?? "",
                  },
                  date: soldLeg.date,
                  fee_eur: soldLeg.fee_eur ?? "0", // fee lives on the sold leg
                  notes: soldLeg.notes ?? "",
                  soldLegId: soldLeg.id,
                  receivedLegId: receivedLeg.id,
                }}
              />
            )
          ) : isAutoAccrual ? (
            // Auto-accrual yield → read-only ActionBanner (no form, no submit).
            <>
              <ActionBanner
                role="status"
                icon={<Info aria-hidden />}
                title="Auto-generated by daily APY accrual"
                body="This transaction was created by the daily APY-accrual job from your APY config. Edit the rate at the source — changes propagate forward, not retroactively."
                action={
                  <Button variant="default" onClick={navigateToApySource}>
                    Edit APY source <ArrowRight className="ml-1 size-3.5" aria-hidden />
                  </Button>
                }
              />
              <DialogFormFooter>
                <DialogClose asChild>
                  <Button variant="outline">Close</Button>
                </DialogClose>
              </DialogFormFooter>
            </>
          ) : isManualYield ? (
            // Manual yield. YieldForm.
            <YieldForm
              txnId={txnId ?? undefined}
              initialValues={{
                account_id:    txnData.account_id,
                instrument_id: txnData.instrument_id,
                date:          txnData.date,
                quantity:      txnData.quantity,
                notes:         txnData.notes ?? "",
              }}
              suppressInnerToast
              onSuccess={({ qty, symbol }) => handleSuccess({ type: "yield", qty, symbol })}
              onCancel={onClose}
            />
          ) : isSpend ? (
            // Spend. SpendForm (edit-mode footer already reads "Save changes").
            <SpendForm
              chrome="inline"
              open
              onClose={onClose}
              txnId={txnId ?? undefined}
              initialValues={{
                account_id:     txnData.account_id,
                instrument_id:  txnData.instrument_id,
                date:           txnData.date,
                quantity:       txnData.quantity,
                unit_price:     txnData.unit_price,
                price_currency: txnData.price_currency ?? "EUR",
                fx_rate_to_eur: txnData.fx_rate_to_eur ?? "",
                notes:          txnData.notes ?? "",
              }}
              suppressInnerToast
              onSuccess={({ amount, currency, description }) =>
                handleSuccess({ type: "spend", amount, currency, description })
              }
            />
          ) : isBuy ? (
            // Buy single-leg — TxnForm with hideTypeSelect so the type dropdown is hidden.
            // Sells are unreachable here: every sell has a trade_pair_id and routes to isTrade above.
            <TxnForm
              txnId={txnId ?? undefined}
              initialValues={{
                account_id:     txnData.account_id,
                instrument_id:  txnData.instrument_id,
                txn_type:       "buy",
                date:           txnData.date,
                quantity:       txnData.quantity,
                unit_price:     txnData.unit_price,
                price_currency: txnData.price_currency ?? "EUR",
                fx_rate_to_eur: txnData.fx_rate_to_eur ?? "",
                fee_eur:        txnData.fee_eur ?? "0",
                notes:          txnData.notes ?? "",
              }}
              hideTypeSelect
              suppressInnerToast
              onSuccess={({ qty, symbol }) => handleSuccess({ type: "buy", qty, symbol })}
              onCancel={onClose}
            />
          ) : null}
        </div>
    </>
  );

  // Responsive branch. ResponsiveDialog renders Dialog (≥768) or Drawer
  // (<768) with the same per-branch content classes and aria/focus plumbing the
  // hand-written branches used.
  return (
    <ResponsiveDialog
      open={open}
      onOpenChange={(o) => { if (!o) onClose(); }}
      dialogClassName="md:max-w-2xl max-h-[85vh] overflow-y-auto"
      drawerClassName="max-h-[92dvh] overflow-y-auto"
      aria-describedby="dialog-chip"
      onCloseAutoFocus={handleCloseAutoFocus}
    >
      {dialogChildren}
    </ResponsiveDialog>
  );
}
