import type { QueryClient } from "@tanstack/react-query";

/**
 * Canonical superset of portfolio query keys to invalidate after any mutation
 * that changes holdings-derived analytical state (transactions, trades, yields,
 * spends, deletes, manual NAV overrides, reconciliations, backfills, tag edits).
 *
 * Consolidates the copy-pasted invalidation blocks that drifted across ~13
 * mutation sites — some omitted `realized` / `holdings` / `allocation`. Unifying
 * on the SUPERSET is safe: invalidating a key that a site didn't strictly need
 * only triggers an extra background refetch of already-correct data; it never
 * surfaces wrong data.
 *
 * Site-specific keys NOT in this portfolio set — e.g. `["nav-history", id]`,
 * `["instruments"]`, `["instrument"]`, `["reconciliation"]`, `["accounts"]` —
 * are invalidated by the call site IN ADDITION to calling this helper.
 */
export const PORTFOLIO_CACHE_KEYS = [
  "transactions",
  "holdings",
  "perf",
  "networth",
  "realized",
  "concentration",
  "allocation",
  "contributions-bars",
  "contributions-overlay",
  "closed",
] as const;

export function invalidatePortfolioCache(qc: QueryClient): void {
  for (const key of PORTFOLIO_CACHE_KEYS) {
    qc.invalidateQueries({ queryKey: [key] });
  }
}
