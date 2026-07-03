"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";

interface Props {
  open: boolean;
  onClose: () => void;
  instrumentSymbol: string;
  snapshotDate: string;
  /**
   * Stages a `dismiss` decision in parent state. The dismiss row is written
   * server-side as a zero-quantity adjustment txn for the audit trail (see
   * services/reconciliation.save_event). NO API call is made
   * from this dialog.
   */
  onConfirm: (reason: string | null) => void;
}

export function DismissDriftDialog({
  open,
  onClose,
  instrumentSymbol,
  snapshotDate,
  onConfirm,
}: Props) {
  const [reason, setReason] = useState("");

  function handleConfirm() {
    onConfirm(reason.trim() || null);
    setReason("");
    onClose();
  }

  function handleCancel() {
    setReason("");
    onClose();
  }

  return (
    <Dialog open={open} onOpenChange={(o) => !o && handleCancel()}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>Dismiss this drift?</DialogTitle>
          <DialogDescription>
            Dismissing leaves a zero-quantity adjustment record so the audit
            trail shows you reviewed this {instrumentSymbol} drift on{" "}
            {snapshotDate}.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-2">
          <label htmlFor="dd-reason" className="text-sm">
            Reason (optional)
          </label>
          <Textarea
            id="dd-reason"
            rows={3}
            maxLength={500}
            placeholder="Why are you dismissing this drift?"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={handleCancel}>
            Cancel
          </Button>
          <Button variant="destructive" onClick={handleConfirm}>
            Dismiss drift
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
