"use client";

/**
 * useUpdateInstrument — mutation hook for PUT /api/instruments/{id}.
 *
 * Sibling to useCreateInstrument. The backend PUT route reuses the
 * InstrumentCreate body schema (not a partial), so callers must send
 * the full instrument shape — InstrumentForm assembles it.
 *
 * Cache invalidation: ["instruments"] (list cache used by selectors +
 * UnclassifiedHint) AND ["instrument", id] (per-instrument detail
 * query used by OverviewTab). Both keys must be invalidated or the
 * detail page will keep showing the stale value until a hard refresh.
 */

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api-client";
import type {
  InstrumentCreateBody,
  InstrumentResponse,
} from "./useCreateInstrument";

export interface UpdateInstrumentArgs {
  id: string;
  input: InstrumentCreateBody;
}

export function useUpdateInstrument() {
  const queryClient = useQueryClient();

  return useMutation<InstrumentResponse, Error, UpdateInstrumentArgs>({
    mutationFn: async ({ id, input }) => {
      // Mirror useCreateInstrument: strip empty ticker_override and
      // null/undefined display_decimals so the backend doesn't receive
      // wire-shape garbage that its Pydantic validator can't coerce.
      const payload: InstrumentCreateBody = { ...input };
      if (payload.ticker_override === "") {
        delete payload.ticker_override;
      }
      if (payload.display_decimals == null) {
        delete payload.display_decimals;
      }
      return apiFetch<InstrumentResponse>(
        `/api/instruments/${encodeURIComponent(id)}`,
        { method: "PUT", body: JSON.stringify(payload) },
      );
    },
    onSuccess: (updated) => {
      // Patch the list cache in place (cheap; avoids a flash of stale data
      // before the invalidation refetch returns).
      queryClient.setQueryData<InstrumentResponse[]>(
        ["instruments"],
        (old) => (old ? old.map((i) => (i.id === updated.id ? updated : i)) : old),
      );
      // Patch the detail cache too so OverviewTab re-renders immediately.
      queryClient.setQueryData<InstrumentResponse>(
        ["instrument", updated.id],
        updated,
      );
      queryClient.invalidateQueries({ queryKey: ["instruments"] });
      queryClient.invalidateQueries({ queryKey: ["instrument", updated.id] });
    },
  });
}
