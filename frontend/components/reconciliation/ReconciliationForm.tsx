"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronLeft } from "lucide-react";
import { toast } from "sonner";

import { apiFetch } from "@/lib/api-client";
import { invalidatePortfolioCache } from "@/lib/invalidate-cache";
import { isRowUnresolved } from "@/lib/reconciliation-drift";
import type { RejectedTxnPayload } from "@/lib/reconciliation-api";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Calendar } from "@/components/ui/calendar";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";

import { useReconciliationPreview } from "@/hooks/useReconciliationPreview";
import { useSaveReconciliation } from "@/hooks/useSaveReconciliation";
import {
  type DriftAction,
  type DriftDecision,
} from "@/lib/reconciliation-api";

import {
  DismissDriftDialog,
} from "./DismissDriftDialog";
import { LastReconciledBadge } from "./LastReconciledBadge";
import {
  RejectDriftDrawer,
  type PendingRejectTxn,
} from "./RejectDriftDrawer";
import {
  ReconciliationDiffTable,
  type DriftRow,
} from "./ReconciliationDiffTable";

interface Account {
  id: string;
  name: string;
  last_reconciled_date: string | null;
}

export interface RejectContext {
  instrumentId: string;
  instrumentSymbol: string;
  accountId: string;
  snapshotDate: string;
  appQty: string;
  snapshotQty: string;
}

export interface DismissContext {
  instrumentId: string;
  instrumentSymbol: string;
  snapshotDate: string;
}

interface Props {
  accountId: string;
  /**
   * Open the RejectDriftDrawer. The drawer collects a
   * transaction-create payload, posts it via /api/transactions with
   * reconciliation_id, then calls back into the form via `onRejectStaged`
   * (or stages the decision through a parent ref) to record the
   * `rejected_txn_id` on the row decision.
   */
  onReject?: (ctx: RejectContext) => void;
  /**
   * Open the DismissDriftDialog. The dialog collects an
   * optional reason, then stages a "dismiss" decision through the parent.
   */
  onDismiss?: (ctx: DismissContext) => void;
}

function todayISO(): string {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

export function ReconciliationForm({
  accountId,
  onReject,
  onDismiss,
}: Props) {
  const qc = useQueryClient();

  const [snapshotDate, setSnapshotDate] = useState<string>(todayISO());
  const [snapshotQtys, setSnapshotQtys] = useState<Map<string, string>>(
    new Map()
  );
  const [decisions, setDecisions] = useState<Map<string, DriftDecision>>(
    new Map()
  );
  const [notes, setNotes] = useState<string>("");
  const [addedRows, setAddedRows] = useState<DriftRow[]>([]);

  // Local state: which row owns the open drawer/dialog, and the
  // queue of staged Reject transactions. The queue
  // is sent as `rejected_txns` in the SAME body as the reconciliation event;
  // the backend writes the event + adjustments + reject txns inside one
  // SQLAlchemy session. The queue exists in client state only — nothing is
  // persisted until the user clicks Save reconciliation.
  const [rejectCtx, setRejectCtx] = useState<RejectContext | null>(null);
  const [dismissCtx, setDismissCtx] = useState<DismissContext | null>(null);
  const [pendingRejectTxns, setPendingRejectTxns] = useState<
    PendingRejectTxn[]
  >([]);

  const { data: accounts = [] } = useQuery({
    queryKey: ["accounts"],
    queryFn: () => apiFetch<Account[]>("/api/accounts"),
  });
  const account = accounts.find((a) => a.id === accountId);

  const previewQuery = useReconciliationPreview(accountId, snapshotDate);

  // The hook still runs the 12-key invalidation set on success. The form
  // below ALSO runs the 12-key set after the atomic save returns — duplicate
  // invalidations are idempotent, but the form copy is what the success toast
  // and the per-row reconciliation cache key depend on. The hook's `onSuccess`
  // callback fires AFTER its invalidations, so it's safe to use it for nothing
  // here (the form owns reset). Two-pass invalidation is intentional but
  // pre-existing.
  const saveMutation = useSaveReconciliation();

  const previewRows: DriftRow[] = useMemo(
    () => previewQuery.data?.rows ?? [],
    [previewQuery.data?.rows]
  );
  const lastReconciled = previewQuery.data?.last_reconciled_date ?? null;

  const allRows: DriftRow[] = useMemo(
    () => [...previewRows, ...addedRows],
    [previewRows, addedRows]
  );
  const excludeIds = useMemo(
    () => allRows.map((r) => r.instrument_id),
    [allRows]
  );

  // Unresolved-drift count: any row where the user typed a snapshot qty that
  // differs from the app qty AND there is no staged decision yet. The
  // row-level predicate lives in lib/reconciliation-drift.ts so it can be
  // locked by a pure-logic node:test regression. The matched
  // exclusion uses decimalStringsEqual (never Number()) so the server's
  // trailing-zero qty form (e.g. "15.500000000000000000") matches user "15.5"
  // and never blocks Save.
  const unresolvedCount = useMemo(() => {
    return allRows.filter((row) =>
      isRowUnresolved({
        appQty: row.app_qty,
        snapQty: snapshotQtys.get(row.instrument_id) ?? "",
        hasDecision: decisions.has(row.instrument_id),
      })
    ).length;
  }, [allRows, snapshotQtys, decisions]);

  function setDecisionFor(
    instrumentId: string,
    action: DriftAction,
    extras: Partial<DriftDecision> = {}
  ) {
    const next = new Map(decisions);
    next.set(instrumentId, {
      instrument_id: instrumentId,
      action,
      ...extras,
    });
    setDecisions(next);
  }

  function clearDecision(instrumentId: string) {
    const next = new Map(decisions);
    next.delete(instrumentId);
    setDecisions(next);
  }

  function handleAccept(row: DriftRow) {
    // No client-side delta math. The server derives delta_qty = snapshot_qty − app_qty
    // using Python Decimal in services/reconciliation.save_event from the holdings
    // array. The client only marks the row as "accept"; snapshot_qty is already in
    // snapshotQtys → holdings on submit.
    setDecisionFor(row.instrument_id, "accept");
  }

  function handleSnapshotDateChange(d: Date | undefined) {
    if (!d) return;
    // Build the ISO date from LOCAL components — d.toISOString() shifts
    // to UTC and slices the date, which off-by-ones to "yesterday" for any
    // timezone east of UTC (Madrid CET/CEST is the documented user locale,
    // see CLAUDE.md). Mirrors the todayISO() helper above.
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, "0");
    const day = String(d.getDate()).padStart(2, "0");
    const iso = `${y}-${m}-${day}`;
    setSnapshotDate(iso);
    // A different snapshot date is a different reconciliation: clear staged
    // decisions and snapshot inputs.
    setDecisions(new Map());
    setSnapshotQtys(new Map());
    setAddedRows([]);
  }

  async function handleSave() {
    // Single atomic POST. The backend writes the event,
    // adjustment txns, and reject txns inside one SQLAlchemy session. There is
    // no Stage-2 anymore — partial-failure leaves nothing behind.
    const rejected_txns: RejectedTxnPayload[] = pendingRejectTxns.map((p) => ({
      instrument_id: p.instrument_id,
      txn_type: p.txn_type,
      txn_date: p.date,
      // quantity is intentionally NOT sent — server derives from
      // abs(snapshot_qty − app_qty) using Python Decimal.
      unit_price: p.unit_price,
      price_currency: p.price_currency,
      fx_rate_to_eur: p.fx_rate_to_eur,
      fee_eur: p.fee_eur,
      notes: p.notes || null,
    }));

    const payload = {
      account_id: accountId,
      snapshot_date: snapshotDate,
      notes: notes || null,
      holdings: allRows.map((r) => ({
        instrument_id: r.instrument_id,
        snapshot_qty: snapshotQtys.get(r.instrument_id) ?? r.app_qty,
      })),
      decisions: Array.from(decisions.values()),
      rejected_txns,
    };

    try {
      await saveMutation.mutateAsync(payload);

      // All writes (event + adjustments + reject txns) are atomic; nothing
      // else to POST. Re-run the canonical invalidation set so freshly-written
      // reject txns appear in dependent caches. The hook already invalidated
      // once after the mutation; the second pass is idempotent.
      invalidatePortfolioCache(qc); // portfolio superset (includes holdings)
      // Reconciliation-specific extension keys (not in the portfolio superset):
      qc.invalidateQueries({ queryKey: ["reconciliation"] });
      qc.invalidateQueries({ queryKey: ["accounts"] });

      const acceptCount = Array.from(decisions.values()).filter(
        (d) => d.action === "accept"
      ).length;
      const dismissCount = Array.from(decisions.values()).filter(
        (d) => d.action === "dismiss"
      ).length;
      const rejectCount = pendingRejectTxns.length;
      toast.success(
        `Reconciliation saved. ${acceptCount} adjustments, ${rejectCount} new transactions, ${dismissCount} dismissed.`,
        { duration: 5000 }
      );

      // Clear local form state.
      setSnapshotQtys(new Map());
      setDecisions(new Map());
      setAddedRows([]);
      setNotes("");
      setPendingRejectTxns([]);
    } catch (e) {
      // Atomic save failed — nothing was committed. The hook already fired
      // an error toast; surface a friendlier form-level message too.
      const msg = e instanceof Error ? e.message : String(e);
      toast.error(`Save failed: ${msg}`, { duration: 8000 });
    }
  }

  if (!account) {
    return <Skeleton className="h-32 w-full" />;
  }

  return (
    <div className="space-y-6">
      <Link
        href="/settings"
        className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <ChevronLeft className="size-4" aria-hidden />
        Back to accounts
      </Link>

      <header className="space-y-2">
        <h1 className="text-xl font-semibold leading-tight sm:text-2xl">
          Reconcile {account.name}
        </h1>
        <p className="text-sm text-muted-foreground">
          Compare your broker&apos;s positions against Flowfolio&apos;s
          calculated state.
        </p>
      </header>

      <div className="flex flex-col items-start gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-2">
          <span className="text-sm">Snapshot date</span>
          <Popover>
            <PopoverTrigger asChild>
              <Button variant="outline" size="sm">
                {snapshotDate}
              </Button>
            </PopoverTrigger>
            <PopoverContent align="start" className="w-auto p-0">
              <Calendar
                mode="single"
                selected={new Date(snapshotDate)}
                onSelect={handleSnapshotDateChange}
                disabled={(d) => d > new Date()}
              />
            </PopoverContent>
          </Popover>
        </div>
        <LastReconciledBadge lastReconciledDate={lastReconciled} />
      </div>

      {previewQuery.isLoading && (
        <div className="space-y-2">
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-14 w-full" />
          ))}
        </div>
      )}

      {previewQuery.isError && (
        <Alert variant="destructive">
          <AlertDescription>
            Could not load reconciliation preview.{" "}
            {(previewQuery.error as Error).message}
          </AlertDescription>
        </Alert>
      )}

      {previewQuery.data && (
        <ReconciliationDiffTable
          rows={allRows}
          snapshotQtys={snapshotQtys}
          decisions={decisions}
          onSnapshotQtyChange={(id, val) => {
            const next = new Map(snapshotQtys);
            next.set(id, val);
            setSnapshotQtys(next);
          }}
          onAccept={handleAccept}
          onReject={(row) => {
            const ctx: RejectContext = {
              instrumentId: row.instrument_id,
              instrumentSymbol: row.instrument_symbol,
              accountId,
              snapshotDate,
              appQty: row.app_qty,
              snapshotQty: snapshotQtys.get(row.instrument_id) ?? "0",
            };
            setRejectCtx(ctx);
            // Forward to optional external observer (kept for backwards
            // compatibility with the external callback seam).
            onReject?.(ctx);
          }}
          onDismiss={(row) => {
            const ctx: DismissContext = {
              instrumentId: row.instrument_id,
              instrumentSymbol: row.instrument_symbol,
              snapshotDate,
            };
            setDismissCtx(ctx);
            onDismiss?.(ctx);
          }}
          onUndo={clearDecision}
          onAddInstrument={(instrument) => {
            setAddedRows([...addedRows, instrument]);
          }}
          excludeIds={excludeIds}
        />
      )}

      <div className="space-y-2">
        <label className="text-sm" htmlFor="recon-notes">
          Notes (optional)
        </label>
        <Textarea
          id="recon-notes"
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          maxLength={2000}
          placeholder="Free-form notes for this reconciliation event…"
          rows={3}
        />
      </div>

      {unresolvedCount > 0 && (
        <Alert variant="destructive">
          <AlertDescription>
            {unresolvedCount} drift {unresolvedCount === 1 ? "row" : "rows"} still
            need{unresolvedCount === 1 ? "s" : ""} a decision before you can save.
          </AlertDescription>
        </Alert>
      )}

      <div className="sticky bottom-0 -mx-4 flex justify-end gap-2 border-t border-border bg-background/95 px-4 py-4 backdrop-blur md:-mx-8 md:px-8">
        <Button variant="outline" asChild>
          <Link href="/settings">Cancel</Link>
        </Button>
        <Button
          onClick={handleSave}
          disabled={unresolvedCount > 0 || saveMutation.isPending}
        >
          {saveMutation.isPending ? "Saving…" : "Save reconciliation"}
        </Button>
      </div>

      {rejectCtx && (
        <RejectDriftDrawer
          open={rejectCtx !== null}
          onClose={() => setRejectCtx(null)}
          accountId={rejectCtx.accountId}
          accountName={account.name}
          instrumentId={rejectCtx.instrumentId}
          instrumentSymbol={rejectCtx.instrumentSymbol}
          snapshotDate={rejectCtx.snapshotDate}
          appQty={rejectCtx.appQty}
          snapshotQty={rejectCtx.snapshotQty}
          onStage={(pending) => {
            // Queue the txn body for the atomic save (sent as
            // rejected_txns in the same body as the event). Immediately stage
            // a `reject` decision so the row no longer counts as unresolved.
            // rejected_txn_id stays null because the audit trail comes from
            // Transaction.reconciliation_id FK alone;
            // the new server-derive path also returns rejected_txn_ids on the
            // response if a downstream consumer wants them.
            setPendingRejectTxns((q) => [...q, pending]);
            setDecisionFor(rejectCtx.instrumentId, "reject", {
              rejected_txn_id: null,
            });
          }}
        />
      )}

      {dismissCtx && (
        <DismissDriftDialog
          open={dismissCtx !== null}
          onClose={() => setDismissCtx(null)}
          instrumentSymbol={dismissCtx.instrumentSymbol}
          snapshotDate={dismissCtx.snapshotDate}
          onConfirm={(reason) => {
            setDecisionFor(dismissCtx.instrumentId, "dismiss", {
              dismiss_reason: reason,
            });
          }}
        />
      )}
    </div>
  );
}
