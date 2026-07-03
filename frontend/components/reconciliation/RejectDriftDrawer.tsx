"use client";

import { useState } from "react";

import { decimalStringsEqual } from "@/lib/decimal-strings";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";

/**
 * PendingRejectTxn — a staged transaction body queued in parent state until
 * the user clicks Save reconciliation. The reconciliation form no longer
 * POSTs each one to /api/transactions; instead it sends the array as
 * `rejected_txns` in the Stage-1 reconciliation create body. The backend
 * derives the txn quantity from abs(snapshot_qty − app_qty) using Python
 * Decimal — this closes the CLAUDE.md "NEVER float for money" gap that the
 * old Stage-2 flow violated via Number() coercion in this component.
 *
 * txn_type is constrained to "buy" | "sell" | "spend":
 *  - "spend" = phantom branch (snapshot_qty=0 AND app_qty>0).
 *  - "buy"   = snapshot > app (broker shows more than Flowfolio knows about).
 *  - "sell"  = snapshot < app (both >0). NOTE: the existing schema validator at
 *    backend/app/schemas/transaction.py forbids manual sell creation through
 *    /api/transactions (sells must go through /api/trades). The
 *    server-derive path uses ORM directly so this constraint no longer
 *    blocks reconciliation rejects, but the inferredType remains a documented
 *    stub for the sell branch.
 */
export interface PendingRejectTxn {
  account_id: string;
  instrument_id: string;
  txn_type: "buy" | "sell" | "spend";
  date: string;
  // quantity intentionally absent: derived server-side from
  // abs(snapshot_qty − app_qty) using Python Decimal (CLAUDE.md invariant).
  unit_price: string;
  price_currency: "EUR" | "USD";
  fx_rate_to_eur: string | null;
  fee_eur: string;
  notes: string;
}

interface Props {
  open: boolean;
  onClose: () => void;
  accountId: string;
  accountName: string;
  instrumentId: string;
  instrumentSymbol: string;
  snapshotDate: string;
  appQty: string; // Decimal-as-string
  snapshotQty: string; // Decimal-as-string (user-entered)
  onStage: (pending: PendingRejectTxn) => void;
}

export function RejectDriftDrawer({
  open,
  onClose,
  accountId,
  accountName,
  instrumentId,
  instrumentSymbol,
  snapshotDate,
  appQty,
  snapshotQty,
  onStage,
}: Props) {
  // UI-only display delta — renders the "+0.5 BTC" badge. NEVER sent to the
  // API. The persisted txn quantity is derived server-side from
  // abs(snapshot_qty − app_qty) using Python Decimal
  // (CLAUDE.md: "NEVER float for money").
  const snapNum = Number(snapshotQty);
  const appNum = Number(appQty);
  const absDelta = Math.abs(snapNum - appNum);

  // Gate the Stage button on decimal-string equality, NOT
  // Math.abs(Number()-derived delta) which can mis-enable for sub-satoshi
  // values or values exceeding 17 significant digits. The button-enabled
  // state must agree with the server's concept of drift; if snapshot equals
  // app per Decimal-string normalization there is nothing to record.
  const hasDriftForButton = !decimalStringsEqual(snapshotQty, appQty);

  // Classify the inferred txn_type using decimal-string comparison
  // against "0" instead of Number()-cast. snap='0.0000000000000001' (15+
  // decimals) is non-zero per Decimal but Number() preserves it as 1e-16,
  // and `snapNum === 0` returned false → the type would have flipped from
  // "spend" to "buy" for a value the server treats as effectively zero.
  // Server-side derivation is a deeper
  // refactor; this guards the immediate float-coercion correctness gap.
  const isSnapZero = decimalStringsEqual(snapshotQty, "0");
  const isAppZero = decimalStringsEqual(appQty, "0");

  // Phantom branch: snapshot=0 AND app>0 → "spend".
  // Otherwise: snapshot > app → "buy"; snapshot < app (both >0) → "sell".
  const inferredType: "buy" | "sell" | "spend" =
    isSnapZero && !isAppZero
      ? "spend"
      : snapNum > appNum
        ? "buy"
        : "sell";

  const [unitPrice, setUnitPrice] = useState("");
  const [priceCurrency, setPriceCurrency] = useState<"EUR" | "USD">("EUR");
  const [fxRate, setFxRate] = useState("");
  const [feeEur, setFeeEur] = useState("0");
  const [notes, setNotes] = useState("");

  function resetLocalState() {
    setUnitPrice("");
    setPriceCurrency("EUR");
    setFxRate("");
    setFeeEur("0");
    setNotes("");
  }

  function handleStage() {
    onStage({
      account_id: accountId,
      instrument_id: instrumentId,
      txn_type: inferredType,
      date: snapshotDate,
      // quantity intentionally absent — server derives it from
      // abs(snapshot_qty − app_qty) using Python Decimal.
      unit_price: unitPrice,
      price_currency: priceCurrency,
      fx_rate_to_eur: priceCurrency === "USD" ? fxRate || null : null,
      fee_eur: feeEur,
      notes,
    });
    resetLocalState();
    onClose();
  }

  function handleCancel() {
    resetLocalState();
    onClose();
  }

  return (
    <Sheet open={open} onOpenChange={(o) => !o && handleCancel()}>
      <SheetContent
        side="right"
        className="w-full sm:max-w-md flex flex-col overflow-y-auto"
      >
        <SheetHeader>
          <SheetTitle>Add missing transaction</SheetTitle>
          <SheetDescription>
            For {instrumentSymbol} in {accountName} on {snapshotDate}.
          </SheetDescription>
        </SheetHeader>
        <div className="mt-6 space-y-4 px-4 pb-4">
          <p className="text-xs text-muted-foreground">
            Type: <span className="font-medium">{inferredType}</span> · Quantity:{" "}
            <span className="font-medium">{absDelta}</span> · Date:{" "}
            <span className="font-medium">{snapshotDate}</span>
          </p>

          <div className="space-y-2">
            <Label htmlFor="rd-unit-price">Unit price</Label>
            <Input
              id="rd-unit-price"
              inputMode="decimal"
              value={unitPrice}
              onChange={(e) => setUnitPrice(e.target.value)}
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="rd-currency">Currency</Label>
            <select
              id="rd-currency"
              value={priceCurrency}
              onChange={(e) =>
                setPriceCurrency(e.target.value as "EUR" | "USD")
              }
              className="flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm"
            >
              <option value="EUR">EUR</option>
              <option value="USD">USD</option>
            </select>
          </div>

          {priceCurrency === "USD" && (
            <div className="space-y-2">
              <Label htmlFor="rd-fx">
                FX rate (USD per 1 EUR; leave blank to fetch)
              </Label>
              <Input
                id="rd-fx"
                inputMode="decimal"
                value={fxRate}
                onChange={(e) => setFxRate(e.target.value)}
              />
            </div>
          )}

          <div className="space-y-2">
            <Label htmlFor="rd-fee">Fee (€)</Label>
            <Input
              id="rd-fee"
              inputMode="decimal"
              value={feeEur}
              onChange={(e) => setFeeEur(e.target.value)}
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="rd-notes">Note</Label>
            <Input
              id="rd-notes"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
            />
          </div>

          <p className="text-xs text-muted-foreground">
            This transaction will be linked to this reconciliation event for
            audit history when you save.
          </p>
        </div>
        <div className="mt-auto border-t border-border pt-4 px-4 pb-4 flex justify-end gap-2">
          <Button variant="outline" onClick={handleCancel}>
            Cancel
          </Button>
          <Button
            onClick={handleStage}
            // Gate on Decimal-string drift, NOT a Number()-derived
            // absDelta. The persisted quantity is server-derived; this gate
            // must agree with the server's concept of "is there drift?".
            disabled={!unitPrice || !hasDriftForButton}
          >
            Stage txn
          </Button>
        </div>
      </SheetContent>
    </Sheet>
  );
}
