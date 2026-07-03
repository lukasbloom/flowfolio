"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { invalidatePortfolioCache } from "@/lib/invalidate-cache";
import {
  postReconciliationEvent,
  type ReconciliationCreate,
  type ReconciliationResponse,
} from "@/lib/reconciliation-api";

interface UseSaveReconciliationArgs {
  onSuccess?: (data: ReconciliationResponse) => void;
}

/**
 * useSaveReconciliation — POST /api/reconciliation/events with the canonical
 * 12-key invalidation set on success (the 9-key analytical surface
 * + ["reconciliation"], ["holdings"], ["accounts"]).
 *
 * The optional `onSuccess` is invoked AFTER the cache invalidations so the
 * caller can perform local state cleanup (resetting form state, etc.).
 *
 * On error, a destructive toast is surfaced. The mutation does NOT toast on
 * success. Callers (the ReconciliationForm that may also fan out
 * pending Reject txns) own the user-facing success message so it
 * fires after the full save sequence completes.
 */
export function useSaveReconciliation(args?: UseSaveReconciliationArgs) {
  const qc = useQueryClient();
  return useMutation<ReconciliationResponse, Error, ReconciliationCreate>({
    mutationFn: (payload) => postReconciliationEvent(payload),
    onSuccess: (data) => {
      // Canonical portfolio-superset invalidation (includes holdings).
      invalidatePortfolioCache(qc);
      // Reconciliation-specific extension keys: the recon page itself and the
      // accounts list (last_reconciled_date badge refresh).
      qc.invalidateQueries({ queryKey: ["reconciliation"] });
      qc.invalidateQueries({ queryKey: ["accounts"] });

      args?.onSuccess?.(data);
    },
    onError: (err) => {
      toast.error(`Could not save reconciliation. ${err.message}`, {
        duration: 6000,
      });
    },
  });
}
