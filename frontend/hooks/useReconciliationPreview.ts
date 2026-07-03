"use client";

import { useQuery } from "@tanstack/react-query";

import {
  fetchPreview,
  type ReconciliationPreviewResponse,
} from "@/lib/reconciliation-api";

export function useReconciliationPreview(
  accountId: string | null,
  snapshotDate: string | null
) {
  return useQuery<ReconciliationPreviewResponse>({
    queryKey: ["reconciliation", accountId, snapshotDate],
    queryFn: () => fetchPreview(accountId!, snapshotDate!),
    enabled: !!accountId && !!snapshotDate,
  });
}
