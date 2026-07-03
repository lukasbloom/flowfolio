"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { type Transaction } from "@/components/transactions/TxnList";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { apiFetch } from "@/lib/api-client";
import {
  DELETE_DIALOG_SHOW_DELETED_LABEL,
  DELETE_DIALOG_SOFT_DELETE_COPY_LEAD,
  DELETE_DIALOG_SOFT_DELETE_COPY_TAIL,
} from "@/components/transactions/delete-dialog-copy";
import { formatMoney } from "@/lib/format";
import { invalidatePortfolioCache } from "@/lib/invalidate-cache";

interface Props {
  txn: Transaction | null;
  allTransactions?: Transaction[];
  onClose: () => void;
}

function fifoPreview(t: Transaction): string {
  const n = t.lot_alloc_count ?? 0;
  const s = n === 1 ? "" : "s";
  if (t.txn_type === "buy") return `Deleting will recompute ${n} sell allocation${s}.`;
  if (t.txn_type === "sell") return `Deleting will release ${n} consumed buy lot${s}.`;
  if (t.txn_type === "spend") return `Deleting will release ${n} consumed buy lot${s}.`;
  if (t.txn_type === "yield" || t.txn_type === "adjustment") {
    // formatMoney is the single source of truth for currency rendering;
    // it returns "€0.00" for a 0 input under en-GB.
    const cb = formatMoney(t.cost_basis_eur ?? 0, "EUR");
    return `Deleting removes ${cb} from cost basis history.`;
  }
  return "";
}

export function DeleteConfirmDialog({ txn, allTransactions, onClose }: Props) {
  const qc = useQueryClient();

  const mutation = useMutation({
    mutationFn: () =>
      apiFetch(`/api/transactions/${txn!.id}`, { method: "DELETE" }),
    onSuccess: () => {
      toast.success("Transaction deleted.");
      invalidatePortfolioCache(qc);
      onClose();
    },
    onError: (err: Error) =>
      toast.error(`Could not delete transaction. ${err.message}.`, {
        duration: 6000,
      }),
  });

  // Resolve partner symbol for linked trades
  let linkedTradeSymbol: string | null = null;
  if (txn?.trade_pair_id && allTransactions) {
    const partner = allTransactions.find(
      (t) => t.id !== txn.id && t.trade_pair_id === txn.trade_pair_id
    );
    if (partner) {
      linkedTradeSymbol = partner.instrument_symbol;
    }
  }

  const preview = txn ? fifoPreview(txn) : "";
  const partnerType =
    txn?.txn_type === "sell" ? "buy" : "sell";

  return (
    <Dialog open={txn !== null} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>Delete this transaction?</DialogTitle>
        </DialogHeader>
        <div className="space-y-2 text-sm">
          <p>This will mark the transaction deleted and recompute FIFO allocations.</p>
          {preview && (
            <p className="text-xs text-muted-foreground">{preview}</p>
          )}
          {linkedTradeSymbol && (
            <p className="text-xs text-muted-foreground">
              Linked trade — the paired {partnerType} of {linkedTradeSymbol}{" "}
              will also be deleted.
            </p>
          )}
          <p className="text-xs text-muted-foreground">
            {DELETE_DIALOG_SOFT_DELETE_COPY_LEAD}{" "}
            <span className="font-medium">
              {DELETE_DIALOG_SHOW_DELETED_LABEL}
            </span>{" "}
            {DELETE_DIALOG_SOFT_DELETE_COPY_TAIL}
          </p>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="destructive"
            disabled={mutation.isPending}
            onClick={() => mutation.mutate()}
          >
            {mutation.isPending ? "Deleting..." : "Delete"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
