"use client";

import { useCallback, useMemo } from "react";

import { usePref } from "@/lib/prefs";

/**
 * Per-chart instrument multi-select filter, persisted as a JSON-encoded
 * string array in a flowfolio.* cookie (was localStorage;
 * moved to cookies with the rest of the persisted prefs so SSR renders the
 * stored selection without a post-hydration flash or a throwaway
 * default-key fetch). Takes the storage key as a parameter so each chart can
 * own an independent slot.
 *
 * Storage keys used in the app:
 *   - `flowfolio.instrumentFilter.networth`
 *
 * (the `flowfolio.instrumentFilter.contributions` slot
 * lived alongside the now-deleted `CostBasisOverlay` on /analytics. The
 * cost-basis line is now layered into the dashboard NetWorth chart, which
 * shares the .networth slot via `NetWorthSection`.)
 */

const EMPTY: string[] = [];

function parseIds(raw: string | null): string[] {
  if (!raw) return EMPTY;
  try {
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return EMPTY;
    // Only keep string ids — defensive against corrupted JSON.
    const ids = parsed.filter((v): v is string => typeof v === "string");
    return ids.length > 0 ? ids : EMPTY;
  } catch {
    return EMPTY;
  }
}

export function useInstrumentFilter(
  storageKey: string
): [string[], (next: string[]) => void] {
  const [raw, setRaw] = usePref(storageKey);

  // usePref snapshots are stable strings, so the parsed array identity only
  // changes when the stored value actually changes.
  const value = useMemo(() => parseIds(raw), [raw]);

  const setValue = useCallback(
    (next: string[]) => {
      // Normalize: drop non-strings + dedupe, preserve order of first
      // occurrence so the caller can rely on stable indexing.
      const seen = new Set<string>();
      const normalized: string[] = [];
      for (const v of next) {
        if (typeof v !== "string") continue;
        if (seen.has(v)) continue;
        seen.add(v);
        normalized.push(v);
      }
      setRaw(normalized.length === 0 ? null : JSON.stringify(normalized));
    },
    [setRaw]
  );

  return [value, setValue];
}
