"use client";

import { useEffect, useRef, useState } from "react";
import { TrendingUp, TrendingDown, ArrowLeftRight, Banknote, Sparkles } from "lucide-react";
import {
  ResponsiveDialog,
  ResponsiveDialogDescription,
  ResponsiveDialogHeader,
  ResponsiveDialogTitle,
  useResponsiveDialogDesktop,
} from "@/components/ui/responsive-dialog";
import { useAddTxn, type AddTxnFormType } from "@/components/transactions/AddTxnProvider";

interface PickerRow {
  type: AddTxnFormType;
  label: string;
  description: string;
  Icon: React.ComponentType<{ className?: string; "aria-hidden"?: boolean }>;
}

// Order: Buy → Sell → Trade → Spend → Yield.
// Yield is last by frequency-of-use ordering: Buy first (common case), Yield last (most yield rows come from APY job, not manual entry).
const ROWS: readonly PickerRow[] = [
  { type: "buy",   label: "Buy",   description: "Purchased shares, units, or coins.",                          Icon: TrendingUp     },
  { type: "sell",  label: "Sell",  description: "Sold shares, units, or coins. FIFO matches lots.",            Icon: TrendingDown   },
  { type: "trade", label: "Trade", description: "Swapped one holding for another in a single move.",           Icon: ArrowLeftRight },
  { type: "spend", label: "Spend", description: "Used a holding to pay for something (real-world outflow).",   Icon: Banknote       },
  { type: "yield", label: "Yield", description: "Received yield outside the APY-accrual job (manual entry).", Icon: Sparkles       },
] as const;

export function AddTxnPicker() {
  const { state, openForm, close, triggerRef } = useAddTxn();
  const open = state.mode === "picker";
  // Responsive Dialog (≥md) / Drawer (<md). useMediaQuery is built on
  // `useSyncExternalStore`, so the first client render already has the
  // correct value (no swap-after-mount flash on SPA navs); SSR still uses
  // the deterministic `false` default to keep hydration safe.
  const isDesktop = useResponsiveDialogDesktop();

  const rowRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const [focusedIndex, setFocusedIndex] = useState(0);
  // Distinguish "picker dismissed by user" from "picker handed off to form".
  // Set true in the row-click handler before dispatching OPEN_FORM so the close that
  // follows skips the AddButton refocus — focus is about to move into the form dialog
  // instead. Without this, focus visibly flashes through the trigger on the way to
  // the form, and screen readers may briefly announce the AddButton.
  const handedOffRef = useRef(false);

  // Reset focus to row 0 each time the picker opens.
  useEffect(() => {
    if (open) {
      handedOffRef.current = false;
      // Defer to allow the Dialog/Drawer mount + focus-trap to settle, then focus row 0.
      const id = window.setTimeout(() => {
        setFocusedIndex(0);
        rowRefs.current[0]?.focus();
      }, 0);
      return () => window.clearTimeout(id);
    }
  }, [open]);

  function handleKeyDown(e: React.KeyboardEvent<HTMLDivElement>) {
    let next = focusedIndex;
    switch (e.key) {
      case "ArrowDown":
        next = (focusedIndex + 1) % ROWS.length;
        break;
      case "ArrowUp":
        next = (focusedIndex - 1 + ROWS.length) % ROWS.length;
        break;
      case "Home":
        next = 0;
        break;
      case "End":
        next = ROWS.length - 1;
        break;
      default:
        return;
    }
    e.preventDefault();
    setFocusedIndex(next);
    rowRefs.current[next]?.focus();
  }

  // Hoisted onCloseAutoFocus so Dialog and Drawer branches reference the same
  // function literal. Focus-return target is provider-owned triggerRef
  // (no more document.getElementById("add-trigger") lookup).
  const onCloseAutoFocus = (e: Event) => {
    e.preventDefault();
    // Only refocus the trigger when the picker was dismissed by the
    // user (Escape, outside click). When handed off to the form, the form's
    // own dialog will manage focus and refocusing the trigger mid-transition
    // causes a visible focus flash.
    if (!handedOffRef.current) {
      triggerRef.current?.focus();
    }
  };

  const handleOpenChange = (o: boolean) => {
    if (!o) close();
  };

  const rowList = (
    <div role="menu" aria-orientation="vertical" onKeyDown={handleKeyDown} className="flex flex-col gap-1">
      {ROWS.map((row, i) => (
        <button
          key={row.type}
          ref={(el) => { rowRefs.current[i] = el; }}
          type="button"
          role="menuitem"
          className="flex items-center gap-3 px-3 py-3 rounded-md text-left w-full hover:bg-muted focus-visible:bg-muted focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
          onClick={() => {
            // Mark the close as a hand-off before dispatching, so
            // onCloseAutoFocus does not race-refocus the trigger during the
            // picker → form transition.
            handedOffRef.current = true;
            openForm(row.type);
          }}
          onFocus={() => setFocusedIndex(i)}
        >
          <row.Icon className="size-5 text-foreground" aria-hidden />
          <div className="flex flex-col">
            <span className="text-sm font-medium text-foreground">{row.label}</span>
            <span className="text-xs text-muted-foreground">{row.description}</span>
          </div>
        </button>
      ))}
    </div>
  );

  return (
    <ResponsiveDialog
      open={open}
      onOpenChange={handleOpenChange}
      dialogClassName="sm:max-w-md"
      onCloseAutoFocus={onCloseAutoFocus}
    >
      <ResponsiveDialogHeader>
        <ResponsiveDialogTitle>Add transaction</ResponsiveDialogTitle>
        <ResponsiveDialogDescription>Pick the type to start.</ResponsiveDialogDescription>
      </ResponsiveDialogHeader>
      {/* Drawer (mobile) wraps the body in px-4 pb-4; the Dialog branch renders it bare. */}
      {isDesktop ? rowList : <div className="px-4 pb-4">{rowList}</div>}
    </ResponsiveDialog>
  );
}
