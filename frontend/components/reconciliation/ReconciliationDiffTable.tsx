"use client";

import { useMemo } from "react";
import {
  AlertTriangle,
  Check,
  Plus,
  Undo2,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { decimalStringsEqual } from "@/lib/decimal-strings";
import { decimalsFor, formatMoney, formatQuantity } from "@/lib/format";
import { cn } from "@/lib/utils";

import {
  AddInstrumentRow,
  type AddedInstrumentRow,
} from "./AddInstrumentRow";
import type { DriftDecision } from "@/lib/reconciliation-api";

export interface DriftRow {
  instrument_id: string;
  instrument_symbol: string;
  instrument_name: string;
  // ReconciliationPreviewRow now carries
  // instrument_type (required) + display_decimals (optional override)
  // so the diff table can render quantity at the correct precision.
  instrument_type: string;
  display_decimals?: number | null;
  price_currency: string | null;
  app_qty: string; // Decimal-as-string
  app_value_eur?: string | null;
  has_price?: boolean;
}

type DriftState =
  | "matched"
  | "qty_drift"
  | "missing"
  | "phantom"
  | "pending_input";

interface Props {
  rows: DriftRow[];
  snapshotQtys: Map<string, string>;
  decisions: Map<string, DriftDecision>;
  onSnapshotQtyChange: (instrumentId: string, value: string) => void;
  onAccept: (row: DriftRow) => void;
  onReject: (row: DriftRow) => void;
  onDismiss: (row: DriftRow) => void;
  onUndo: (instrumentId: string) => void;
  onAddInstrument: (row: AddedInstrumentRow) => void;
  excludeIds: string[];
}

function parseDecimal(value: string | undefined | null): number {
  // Used only for DriftCaption display math (delta text like "+0.5 BTC")
  // and for the missing/phantom classification below. Matched/drift
  // classification uses decimalStringsEqual instead — see deriveDriftState.
  if (value === undefined || value === null || value === "") return Number.NaN;
  const n = Number(value);
  return Number.isFinite(n) ? n : Number.NaN;
}

function deriveDriftState(appQtyStr: string, snapStr: string): DriftState {
  // If user hasn't typed yet, treat empty as undecided (no decision required).
  if (snapStr === "" || snapStr === undefined) return "pending_input";

  // Decimal-string equality for matched check — avoids Number() coercion.
  // A sub-satoshi difference will fail this check and fall
  // through to the numeric branch below, which will classify it as qty_drift.
  if (decimalStringsEqual(appQtyStr, snapStr)) return "matched";

  // Missing/phantom classification must also use decimal-string
  // equality. parseDecimal(snap) === 0 returns false for "0.000000000000001"
  // (Number() preserves it), so a value the user perceives as
  // essentially-zero in the app would be classified as qty_drift and the
  // Accept button shown — but accepting that papers over what is really a
  // missing buy. Use decimalStringsEqual against "0" instead.
  const isAppZero = decimalStringsEqual(appQtyStr, "0");
  const isSnapZero = decimalStringsEqual(snapStr, "0");
  // Snapshot string must still be parseable (reject e.g. "abc" → pending_input).
  const snap = parseDecimal(snapStr);
  if (Number.isNaN(snap)) return "pending_input";
  if (isAppZero && !isSnapZero) return "missing";
  if (!isAppZero && isSnapZero) return "phantom";
  return "qty_drift";
}

function StatusIcon({ state }: { state: DriftState }) {
  if (state === "matched") {
    return (
      <Check
        className="size-4 text-emerald-600 dark:text-emerald-500"
        aria-hidden
      />
    );
  }
  if (state === "phantom") {
    return (
      <AlertTriangle className="size-4 text-destructive" aria-hidden />
    );
  }
  if (state === "missing") {
    return (
      <Plus className="size-4 text-amber-600 dark:text-amber-500" aria-hidden />
    );
  }
  if (state === "qty_drift") {
    return (
      <AlertTriangle
        className="size-4 text-amber-600 dark:text-amber-500"
        aria-hidden
      />
    );
  }
  // pending_input — no icon yet (user hasn't entered snapshot qty)
  return null;
}

function DriftCaption({
  state,
  appQty,
  snapStr,
  symbol,
}: {
  state: DriftState;
  appQty: string;
  snapStr: string;
  symbol: string;
}) {
  if (state === "matched") {
    return <span className="text-xs text-muted-foreground">Match</span>;
  }
  const app = parseDecimal(appQty);
  const snap = parseDecimal(snapStr);
  if (state === "qty_drift") {
    const delta = snap - app;
    const sign = delta >= 0 ? "+" : "";
    return (
      <span className="text-xs text-amber-700 dark:text-amber-400">
        Drift: {sign}
        {delta} {symbol}
      </span>
    );
  }
  if (state === "missing") {
    return (
      <span className="text-xs text-amber-700 dark:text-amber-400">
        Missing in app: +{snap} {symbol}
      </span>
    );
  }
  if (state === "phantom") {
    return (
      <span className="text-xs text-destructive">
        Phantom: app says {appQty} {symbol}, broker says 0
      </span>
    );
  }
  return null;
}

function ResolvedCaption({ action }: { action: DriftDecision["action"] }) {
  let label = "Will accept";
  if (action === "reject") label = "Will reject via new txn";
  else if (action === "dismiss") label = "Will dismiss";
  return (
    <span className="text-xs text-emerald-700 dark:text-emerald-400">
      {label}
    </span>
  );
}

function valueCell(row: DriftRow) {
  if (!row.app_value_eur) {
    return (
      <span className="text-xs text-muted-foreground">— no price</span>
    );
  }
  const formatted = formatMoney(row.app_value_eur, "EUR");
  if (row.price_currency && row.price_currency !== "EUR") {
    // Compact uppercase currency-code chip in place of the prior per-row
    // parenthetical suffix. Uses row.price_currency directly so future
    // GBP/CHF/etc. picks up automatically. title attribute carries the longer
    // hover explanation.
    return (
      <span className="inline-flex items-baseline gap-1.5">
        <span>{formatted}</span>
        <span
          className="rounded border border-border px-1 text-[10px] font-medium uppercase tracking-wide text-muted-foreground"
          title={`Priced in ${row.price_currency}, displayed as EUR equivalent at locked FX`}
        >
          {row.price_currency}
        </span>
      </span>
    );
  }
  return <span>{formatted}</span>;
}

interface RowActionsProps {
  state: DriftState;
  row: DriftRow;
  decision: DriftDecision | undefined;
  onAccept: (row: DriftRow) => void;
  onReject: (row: DriftRow) => void;
  onDismiss: (row: DriftRow) => void;
  onUndo: (instrumentId: string) => void;
}

function RowActions({
  state,
  row,
  decision,
  onAccept,
  onReject,
  onDismiss,
  onUndo,
}: RowActionsProps) {
  if (decision) {
    return (
      <div className="flex items-center justify-end gap-2">
        <Button
          variant="ghost"
          size="sm"
          onClick={() => onUndo(row.instrument_id)}
          className="gap-1"
        >
          <Undo2 className="size-3" aria-hidden />
          Undo
        </Button>
        <ResolvedCaption action={decision.action} />
      </div>
    );
  }

  if (state === "matched" || state === "pending_input") {
    return null;
  }

  if (state === "phantom") {
    return (
      <div className="flex items-center justify-end gap-2">
        <TooltipProvider>
          <Tooltip>
            <TooltipTrigger asChild>
              {/* Disabled buttons swallow pointer events, so the tooltip would
                  never open. Wrap the disabled Accept in a focusable span so
                  hover/focus still reaches the Tooltip. */}
              <span tabIndex={0} className="inline-flex">
                <Button
                  size="sm"
                  disabled
                  aria-disabled
                  aria-label="Accept disabled — see explanation"
                  className="pointer-events-none"
                >
                  Accept
                </Button>
              </span>
            </TooltipTrigger>
            <TooltipContent>
              Accepting a phantom would skip realized-gain accounting. Use
              Reject to record the missing sell.
            </TooltipContent>
          </Tooltip>
        </TooltipProvider>
        <Button variant="outline" size="sm" onClick={() => onReject(row)}>
          Reject
        </Button>
        <Button variant="ghost" size="sm" onClick={() => onDismiss(row)}>
          Dismiss
        </Button>
      </div>
    );
  }

  // qty_drift or missing — full triple. No flex-wrap: the buttons stay on one
  // row at desktop so they cannot wrap under the Value column at intermediate
  // widths. The Actions <TableCell> below pins a min-width so
  // the column widens the table instead of clipping.
  return (
    <div className="flex items-center justify-end gap-2">
      <Button size="sm" onClick={() => onAccept(row)}>
        Accept
      </Button>
      <Button variant="outline" size="sm" onClick={() => onReject(row)}>
        Reject
      </Button>
      <Button variant="ghost" size="sm" onClick={() => onDismiss(row)}>
        Dismiss
      </Button>
    </div>
  );
}

export function ReconciliationDiffTable({
  rows,
  snapshotQtys,
  decisions,
  onSnapshotQtyChange,
  onAccept,
  onReject,
  onDismiss,
  onUndo,
  onAddInstrument,
  excludeIds,
}: Props) {
  const enriched = useMemo(
    () =>
      rows.map((row) => {
        const snap = snapshotQtys.get(row.instrument_id) ?? "";
        const state = deriveDriftState(row.app_qty, snap);
        const decision = decisions.get(row.instrument_id);
        return { row, snap, state, decision };
      }),
    [rows, snapshotQtys, decisions]
  );

  const isEmpty = enriched.length === 0;

  return (
    <div className="space-y-4">
      {/* Desktop table */}
      <div className="hidden sm:block">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-12">Status</TableHead>
              <TableHead>Instrument</TableHead>
              <TableHead className="text-right">App qty</TableHead>
              <TableHead className="text-right">Snapshot qty</TableHead>
              <TableHead className="text-right">Value (€)</TableHead>
              <TableHead className="text-right whitespace-nowrap w-[240px] min-w-[240px]">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {isEmpty && (
              <TableRow>
                <TableCell colSpan={6} className="text-center text-sm text-muted-foreground py-8">
                  This account has no recorded holdings. Add a transaction
                  first, or click &quot;+ Add a holding…&quot; to start a
                  snapshot.
                </TableCell>
              </TableRow>
            )}
            {enriched.map(({ row, snap, state, decision }) => (
              <TableRow key={row.instrument_id}>
                <TableCell className="align-top">
                  <StatusIcon state={state} />
                </TableCell>
                <TableCell className="align-top">
                  <div className="flex flex-col">
                    <span className="font-medium">{row.instrument_symbol}</span>
                    <span className="text-xs text-muted-foreground">
                      {row.instrument_name}
                    </span>
                    <DriftCaption
                      state={state}
                      appQty={row.app_qty}
                      snapStr={snap}
                      symbol={row.instrument_symbol}
                    />
                  </div>
                </TableCell>
                <TableCell className={cn("text-right tabular-nums align-top")}>
                  {formatQuantity(row.app_qty, decimalsFor({ instrumentType: row.instrument_type, displayDecimals: row.display_decimals }))}
                </TableCell>
                <TableCell className="text-right align-top">
                  <Input
                    type="text"
                    inputMode="decimal"
                    value={snap}
                    placeholder="0.00"
                    onChange={(e) =>
                      onSnapshotQtyChange(row.instrument_id, e.target.value)
                    }
                    aria-label={`Snapshot qty for ${row.instrument_symbol}`}
                    className="text-right tabular-nums"
                  />
                </TableCell>
                <TableCell className="text-right tabular-nums align-top">
                  {valueCell(row)}
                </TableCell>
                <TableCell className="align-top whitespace-nowrap w-[240px] min-w-[240px]">
                  <RowActions
                    state={state}
                    row={row}
                    decision={decision}
                    onAccept={onAccept}
                    onReject={onReject}
                    onDismiss={onDismiss}
                    onUndo={onUndo}
                  />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      {/* Mobile stacked cards */}
      <div className="space-y-3 sm:hidden">
        {isEmpty && (
          <p className="text-sm text-muted-foreground text-center py-8">
            This account has no recorded holdings. Add a transaction first, or
            tap &quot;+ Add a holding…&quot; to start a snapshot.
          </p>
        )}
        {enriched.map(({ row, snap, state, decision }) => (
          <div
            key={row.instrument_id}
            className="rounded-md border border-border p-3 space-y-2"
          >
            <div className="flex items-center gap-2">
              <StatusIcon state={state} />
              <div className="flex flex-col">
                <span className="font-medium">{row.instrument_symbol}</span>
                <span className="text-xs text-muted-foreground">
                  {row.instrument_name}
                </span>
              </div>
            </div>
            <div className="grid grid-cols-2 gap-y-1 text-sm">
              <span className="text-muted-foreground">App qty</span>
              <span className="text-right tabular-nums">{formatQuantity(row.app_qty, decimalsFor({ instrumentType: row.instrument_type, displayDecimals: row.display_decimals }))}</span>
              <span className="text-muted-foreground">Snapshot qty</span>
              <Input
                type="text"
                inputMode="decimal"
                value={snap}
                placeholder="0.00"
                onChange={(e) =>
                  onSnapshotQtyChange(row.instrument_id, e.target.value)
                }
                aria-label={`Snapshot qty for ${row.instrument_symbol}`}
                className="h-9 text-right tabular-nums min-h-11"
              />
              <span className="text-muted-foreground">Value (€)</span>
              <span className="text-right tabular-nums">{valueCell(row)}</span>
            </div>
            <DriftCaption
              state={state}
              appQty={row.app_qty}
              snapStr={snap}
              symbol={row.instrument_symbol}
            />
            <RowActions
              state={state}
              row={row}
              decision={decision}
              onAccept={onAccept}
              onReject={onReject}
              onDismiss={onDismiss}
              onUndo={onUndo}
            />
          </div>
        ))}
      </div>

      <AddInstrumentRow
        excludeIds={excludeIds}
        onAdd={onAddInstrument}
      />
    </div>
  );
}
