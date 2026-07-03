/**
 * Pure row → "is this row actionable/unresolved" predicate for the
 * reconciliation footer count.
 *
 * Extracted out of the inline `unresolvedCount` useMemo in
 * ReconciliationForm.tsx so it can be exercised by the standalone
 * `node --test` runner (no JSX), locking the matched-row exclusion behavior:
 * the footer counter — which gates the Save button — must count ONLY
 * actionable rows (drift / missing / phantom) and never matched rows.
 *
 * Matched-row exclusion uses decimalStringsEqual (NEVER Number() coercion),
 * so the server's trailing-zero quantity form (e.g. "15.500000000000000000")
 * compares equal to a user's "15.5" without ever round-tripping through
 * IEEE-754 — per CLAUDE.md "NEVER float for money".
 */
// Explicit `.ts` extension: this module is imported both by the Next.js
// bundle (which accepts it via allowImportingTsExtensions + bundler
// resolution) and by the standalone `node --test --experimental-strip-types`
// runner, whose ESM resolver REQUIRES the extension on relative imports. A
// bare "./decimal-strings" resolves under Next but throws ERR_MODULE_NOT_FOUND
// under node:test (Rule 3 — blocking the unit-test deliverable).
import { decimalStringsEqual } from "./decimal-strings.ts";

export interface DriftRowState {
  /** App-derived quantity for the row (Decimal-as-string). */
  appQty: string;
  /** The snapshot quantity the user typed ("" if untouched). */
  snapQty: string;
  /** Whether a decision (accept/reject/dismiss) is already staged for the row. */
  hasDecision: boolean;
}

/**
 * Returns true iff the row is an unresolved, actionable drift that must block
 * Save: the user has typed a snapshot qty that differs from the app qty AND no
 * decision is staged yet.
 *
 * Excluded (returns false):
 *   - empty input (user hasn't typed a snapshot qty yet)
 *   - matched rows (snap canonically equals app_qty, including server
 *     trailing-zero form vs user shorthand)
 *   - rows with a staged decision
 */
export function isRowUnresolved({
  appQty,
  snapQty,
  hasDecision,
}: DriftRowState): boolean {
  // Empty input = user hasn't typed anything yet; not unresolved drift.
  // Matched per Decimal-string normalization (never Number()): the server
  // stringifies app qty with trailing zeros (e.g. "15.500000000000000000")
  // while user input is "15.5", so a naive `snap === appQty` would over-count
  // matched rows and wrongly block Save.
  if (snapQty === "" || decimalStringsEqual(snapQty, appQty)) return false;
  return !hasDecision;
}
