import { apiFetch } from "@/lib/api-client";

export interface ReconciliationPreviewRow {
  instrument_id: string;
  instrument_symbol: string;
  instrument_name: string;
  // Backend now returns instrument_type (required) +
  // display_decimals (optional override) on every preview row.
  instrument_type: string;
  display_decimals?: number | null;
  price_currency: string | null;
  app_qty: string; // Decimal-as-string (CLAUDE.md: NEVER float for money)
  app_value_eur: string | null;
  has_price: boolean;
}

export interface ReconciliationPreviewResponse {
  account_id: string;
  snapshot_date: string; // YYYY-MM-DD
  rows: ReconciliationPreviewRow[];
  last_reconciled_date: string | null;
}

export type DriftAction = "accept" | "reject" | "dismiss" | "matched";

export interface DriftDecision {
  instrument_id: string;
  action: DriftAction;
  // No delta_qty here — server derives it from holdings[i].snapshot_qty − app_qty (Decimal).
  // The client may compute a UI-only display delta (e.g., to render "+0.5 BTC" badges),
  // but that value is NEVER sent in the payload and is NEVER persisted.
  dismiss_reason?: string | null;
  rejected_txn_id?: string | null;
}

export interface HoldingSnapshotEntry {
  instrument_id: string;
  snapshot_qty: string;
}

/**
 * Reject-txn payload. quantity is intentionally absent — the backend
 * derives abs(snapshot_qty − app_qty) using Python Decimal (CLAUDE.md invariant).
 */
export interface RejectedTxnPayload {
  instrument_id: string;
  txn_type: "buy" | "sell" | "spend";
  txn_date?: string | null; // YYYY-MM-DD; null = use snapshot_date
  unit_price: string; // Decimal-as-string
  price_currency: "EUR" | "USD";
  fx_rate_to_eur?: string | null;
  fee_eur: string; // Decimal-as-string
  notes?: string | null;
}

export interface ReconciliationCreate {
  account_id: string;
  snapshot_date: string; // YYYY-MM-DD
  notes?: string | null;
  holdings: HoldingSnapshotEntry[];
  decisions: DriftDecision[];
  rejected_txns?: RejectedTxnPayload[]; // carry reject payloads server-side
}

export interface ReconciliationResponse {
  id: string;
  account_id: string;
  snapshot_date: string;
  created_at: string;
  notes?: string | null;
  holdings_snapshot: Array<Record<string, unknown>>;
  rejected_txn_ids?: string[]; // IDs of reject txns written server-side
}

export function fetchPreview(
  accountId: string,
  snapshotDate: string
): Promise<ReconciliationPreviewResponse> {
  const qs = new URLSearchParams({
    account_id: accountId,
    snapshot_date: snapshotDate,
  });
  return apiFetch<ReconciliationPreviewResponse>(
    `/api/reconciliation/preview?${qs.toString()}`
  );
}

export function postReconciliationEvent(
  payload: ReconciliationCreate
): Promise<ReconciliationResponse> {
  return apiFetch<ReconciliationResponse>("/api/reconciliation/events", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
}
