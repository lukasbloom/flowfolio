"use client";

/**
 * useCreateInstrument — mutation hook for POST /api/instruments.
 *
 * Expected mutation states (for future test scaffolding):
 *  - idle:        initial; mutate() not yet called.
 *  - pending:     POST in flight; UI should disable submit + show spinner.
 *  - success:     201 returned; ["instruments"] cache invalidated; data is the new InstrumentResponse.
 *                 Caller is responsible for closing dialog + auto-selecting the new instrument.
 *  - error:       ApiError thrown.
 *                   - status 422 → render error.detail inline (Pydantic validation failure).
 *                   - status 409 → render duplicate-symbol message inline. The backend
 *                                  catches the Instrument
 *                                  UniqueConstraint IntegrityError on create AND rename and
 *                                  returns a real 409 with a human-readable detail — no longer
 *                                  a 500. This is the duplicate-symbol path, surfaced inline.
 *                   - status 5xx / network → toast generic failure; keep dialog open.
 */

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api-client";

export interface InstrumentCreateBody {
  symbol: string;
  name: string;
  instrument_type: string;
  base_currency: string;
  price_source: string;
  // Optional on the wire — backend defaults to "Medium"
  // when omitted on POST. The edit form always sends a concrete value.
  risk_level?: string;
  ticker_override?: string;
  // Optional per-instrument override; omit (undefined)
  // when the user wants to inherit the per-type default.
  display_decimals?: number | null;
}

export interface InstrumentResponse {
  id: string;
  symbol: string;
  name: string;
  instrument_type: string;
  base_currency: string;
  price_source: string;
  // Non-optional — backend always returns a concrete
  // value (column is NOT NULL with DB-level default "Medium").
  risk_level: string;
  ticker_override: string | null;
  display_decimals: number | null;
  created_at: string;
}

export function useCreateInstrument() {
  const queryClient = useQueryClient();

  return useMutation<InstrumentResponse, Error, InstrumentCreateBody>({
    mutationFn: async (body) => {
      // Pydantic v2 cannot coerce "" into Optional[str]; strip empty ticker_override.
      const payload: InstrumentCreateBody = { ...body };
      if (payload.ticker_override === "") {
        delete payload.ticker_override;
      }
      // Omit display_decimals when the user left it
      // blank — sending null would explicitly clear the column, which is
      // not what an empty input field should mean on a CREATE flow.
      if (payload.display_decimals == null) {
        delete payload.display_decimals;
      }
      return apiFetch<InstrumentResponse>("/api/instruments", {
        method: "POST",
        body: JSON.stringify(payload),
      });
    },
    onSuccess: (newInst) => {
      // Optimistically inject the new instrument into the cache
      // BEFORE the invalidation-triggered refetch returns. Without this, callers
      // that immediately `form.setValue("instrument_id", newInst.id)` on the
      // onCreated callback render their <Select value=newInst.id> against a
      // SelectContent that doesn't yet contain a matching SelectItem — Radix
      // shows the placeholder and the auto-select effectively fails. With the
      // optimistic prepend, the option is in the list synchronously, so the
      // Radix Select binds value→label on the very next render.
      queryClient.setQueryData<InstrumentResponse[]>(["instruments"], (old) => {
        if (!old) return [newInst];
        if (old.some((i) => i.id === newInst.id)) return old;
        return [...old, newInst];
      });
      queryClient.invalidateQueries({ queryKey: ["instruments"] });
    },
  });
}
