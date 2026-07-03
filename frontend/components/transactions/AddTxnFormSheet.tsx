"use client";

import { toast } from "sonner";
import {
  ResponsiveDialog,
  ResponsiveDialogDescription,
  ResponsiveDialogHeader,
  ResponsiveDialogTitle,
  useResponsiveDialogDesktop,
} from "@/components/ui/responsive-dialog";
import { useAddTxn, type AddTxnFormType } from "@/components/transactions/AddTxnProvider";
import { TxnForm } from "@/components/transactions/TxnForm";
import { TradeForm } from "@/components/transactions/TradeForm";
import { SpendForm } from "@/components/transactions/SpendForm";
import { YieldForm } from "@/components/transactions/YieldForm";

// Dialog titles by transaction type.
const TITLE_BY_TYPE: Record<AddTxnFormType, string> = {
  buy:   "Add buy",
  sell:  "Add sell",
  trade: "Add trade",
  spend: "Add spend",
  yield: "Add yield",   // lowercase 'yield'
};

const DESCRIPTION_BY_TYPE: Record<AddTxnFormType, string> = {
  buy:   "Record a buy. Cost basis updates automatically.",
  sell:  "Record a sell. FIFO matches against your lots.",
  trade: "Swap one holding for another. Fee is stored in EUR.",
  spend: "Use a holding to pay for something. Cost basis is consumed FIFO.",
  yield: "Record yield received outside the APY-accrual job.",  // 'Record' inside descriptions intentional
};

// Cache invalidation is owned by each inner form's mutation.onSuccess. The wrapper
// only produces the canonical toast and dismisses the dialog — it does NOT re-invalidate
// to avoid silent drift when the inner sets change. TxnForm/TradeForm/SpendForm all
// invalidate the same core set (transactions, perf, networth, realized, concentration,
// allocation, contributions-bars, contributions-overlay, closed) plus holdings.

// Payload union for the onSuccess widening. The wrapper switches on type to
// produce the rich toast template. Adding `yield`
// will require extending this union AND the switch in handleSuccess — the
// exhaustive `_exhaustive: never` will trip the compiler, surfacing the new case.
type SuccessPayload =
  | { type: "buy"; qty: string; symbol: string }
  | { type: "sell"; qty: string; symbol: string }
  | { type: "trade"; sold_qty: string; sold_symbol: string; received_qty: string; received_symbol: string }
  | { type: "spend"; amount: string; currency: string; description?: string }
  | { type: "yield"; qty: string; symbol: string };   // Yield create path

export function AddTxnFormSheet() {
  const { state, close, triggerRef } = useAddTxn();
  const open = state.mode === "form";
  // Responsive Dialog (≥md) / Drawer (<md). See use-media-query.ts —
  // the hook is built on `useSyncExternalStore` so the first client render
  // already picks the right branch.
  const isDesktop = useResponsiveDialogDesktop();

  function handleSuccess(payload: SuccessPayload) {
    // Rich toast wording. Literal em-dash —. Capitalize the type. No trailing period.
    let message: string;
    switch (payload.type) {
      case "buy":
        message = `Buy added — ${payload.qty} ${payload.symbol}`;
        break;
      case "sell":
        message = `Sell added — ${payload.qty} ${payload.symbol}`;
        break;
      case "trade":
        message = `Trade added — ${payload.sold_qty} ${payload.sold_symbol} → ${payload.received_qty} ${payload.received_symbol}`;
        break;
      case "spend":
        // Spend has no instrument identity — use amount + currency.
        message = `Spend added — ${payload.amount} ${payload.currency}`;
        break;
      case "yield":
        // Literal em-dash U+2014.
        message = `Yield added — ${payload.qty} ${payload.symbol}`;
        break;
      default: {
        const _exhaustive: never = payload;
        throw new Error(`Unhandled success payload: ${JSON.stringify(_exhaustive)}`);
      }
    }
    toast.success(message);

    // Cache invalidation is owned by the inner forms — do not duplicate here.
    // (Inner forms call queryClient.invalidateQueries on the canonical 9-key
    // set.)

    // Only close from handleSuccess for buy/sell/yield. TradeForm/SpendForm call
    // their own onClose prop after onSuccess (TradeForm.tsx:375, SpendForm.tsx:183),
    // which already routes to close(). Calling close() twice is functionally a no-op
    // (CLOSE from idle stays idle) but masks a real bug if the reducer ever gains side
    // effects. YieldForm follows TxnForm's shape (no internal onClose after success)
    // so it belongs in this set.
    if (payload.type === "buy" || payload.type === "sell" || payload.type === "yield") {
      close();
    }
  }

  function renderForm() {
    if (state.mode !== "form") return null;
    switch (state.type) {
      case "buy":
      case "sell": {
        const txnType = state.type; // narrow for the closure
        return (
          <TxnForm
            initialValues={{ txn_type: txnType }}
            hideTypeSelect
            suppressInnerToast
            onSuccess={({ qty, symbol }) => handleSuccess({ type: txnType, qty, symbol })}
            onCancel={close}
          />
        );
      }
      case "trade":
        return (
          <TradeForm
            open
            chrome="inline"
            suppressInnerToast
            onClose={close}
            onSuccess={({ sold_qty, sold_symbol, received_qty, received_symbol }) =>
              handleSuccess({ type: "trade", sold_qty, sold_symbol, received_qty, received_symbol })
            }
          />
        );
      case "spend":
        return (
          <SpendForm
            open
            chrome="inline"
            suppressInnerToast
            onClose={close}
            onSuccess={({ amount, currency, description }) =>
              handleSuccess({ type: "spend", amount, currency, description })
            }
          />
        );
      case "yield":
        return (
          <YieldForm
            hideTypeSelect
            suppressInnerToast
            onSuccess={({ qty, symbol }) => handleSuccess({ type: "yield", qty, symbol })}
            onCancel={close}
          />
        );
      default: {
        const _exhaustive: never = state.type;
        return _exhaustive;
      }
    }
  }

  // Resolve title/description ONLY when state.mode === "form" — otherwise the Dialog is closed and content is unmounted by Radix.
  const title = state.mode === "form" ? TITLE_BY_TYPE[state.type] : "";
  const description = state.mode === "form" ? DESCRIPTION_BY_TYPE[state.type] : "";

  // Hoisted onCloseAutoFocus so Dialog and Drawer branches reference the same
  // function literal. Focus-return target is provider-owned triggerRef
  // (no more document.getElementById("add-trigger") lookup).
  const onCloseAutoFocus = (e: Event) => {
    e.preventDefault();
    triggerRef.current?.focus();
  };

  const handleOpenChange = (o: boolean) => {
    if (!o) close();
  };

  return (
    <ResponsiveDialog
      open={open}
      onOpenChange={handleOpenChange}
      dialogClassName="md:max-w-2xl max-h-[85vh] overflow-y-auto"
      drawerClassName="max-h-[92dvh] overflow-y-auto"
      onCloseAutoFocus={onCloseAutoFocus}
    >
      <ResponsiveDialogHeader>
        <ResponsiveDialogTitle>{title}</ResponsiveDialogTitle>
        <ResponsiveDialogDescription>{description}</ResponsiveDialogDescription>
      </ResponsiveDialogHeader>
      {/* Drawer (mobile) wraps the body in px-4 pb-4; the Dialog branch renders it bare. */}
      {isDesktop ? renderForm() : <div className="px-4 pb-4">{renderForm()}</div>}
    </ResponsiveDialog>
  );
}
