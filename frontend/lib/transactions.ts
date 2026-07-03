/**
 * Predicates for transaction data shapes.
 *
 * Used by `EditTxnDialog` for read-only-banner branching, by `TxnList` for any
 * future row-level badge logic, and by integration tests for the
 * multi-account fixture. Centralized to keep the `notes LIKE 'auto-accrual %'`
 * detection criterion in one place. Manual yield rows
 * are distinguished from auto-accrual yield rows by the ABSENCE of the prefix; no
 * separate `source` column, no flag, no schema migration.
 */

/**
 * Returns true if the transaction was created by the daily APY-accrual job.
 *
 * Detection criterion: `txn_type === "yield"` AND `notes` starts with the
 * literal prefix `"auto-accrual "` (including the trailing space).
 *
 * Manual yield rows (entered via YieldForm) leave the prefix off and return false.
 */
export function isAutoAccrualYield(
  txn: { txn_type: string; notes: string | null },
): boolean {
  return txn.txn_type === "yield" && (txn.notes ?? "").startsWith("auto-accrual ");
}
