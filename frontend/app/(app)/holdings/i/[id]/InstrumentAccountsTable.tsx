"use client";

import { useMemo } from "react";

import { useCurrency } from "@/lib/currency";
import { toDisplayNumber } from "@/lib/decimal-strings";
import { decimalsFor, formatMoney, formatQuantity } from "@/lib/format";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { PercentCell } from "@/components/perf/PercentCell";
import { RealizedCell } from "@/components/perf/RealizedCell";
import { TwrrCell } from "@/components/perf/TwrrCell";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { cn } from "@/lib/utils";
import { useOpenPerf } from "@/hooks/useOpenPerf";

interface InstrumentAccountsTableProps {
  instrumentId: string;
}

/**
 * Per-(account, instrument) breakdown table directly under the KPI block.
 * One row per account that ever held the instrument (open or closed). Replaces
 * the need to flip back to /track to ask "how much BTC do I have on Bit2Me
 * specifically".
 *
 * Re-uses the same `/api/perf?include_closed=1` query as InstrumentKpiBlock —
 * TanStack Query dedupes on the matching cache key.
 */
export function InstrumentAccountsTable({ instrumentId }: InstrumentAccountsTableProps) {
  const { currency } = useCurrency();

  const { data: perfRows, isLoading, isError } = useOpenPerf(currency);

  const rows = useMemo(() => {
    if (!perfRows) return [];
    const scoped = perfRows.filter((r) => r.instrument_id === instrumentId);
    // Open first (descending market value), closed after (alphabetical).
    return scoped.sort((a, b) => {
      const aOpen = a.status !== "closed";
      const bOpen = b.status !== "closed";
      if (aOpen !== bOpen) return aOpen ? -1 : 1;
      if (aOpen) {
        const aVal = a.current_price !== null ? toDisplayNumber(a.quantity) * toDisplayNumber(a.current_price) : 0;
        const bVal = b.current_price !== null ? toDisplayNumber(b.quantity) * toDisplayNumber(b.current_price) : 0;
        return bVal - aVal;
      }
      return a.account_name.localeCompare(b.account_name, "en");
    });
  }, [perfRows, instrumentId]);

  if (isLoading) {
    return <Skeleton className="h-24 w-full" />;
  }
  if (isError) {
    return (
      <p className="text-sm text-destructive">
        Could not load per-account breakdown.
      </p>
    );
  }
  if (rows.length === 0) {
    return null;
  }

  // Single-account holding doesn't need a per-account table — the KPI block
  // already shows the entire picture. Skip the table to keep the page compact.
  if (rows.length === 1 && rows[0].status !== "closed") {
    return null;
  }

  return (
    <section className="space-y-3" aria-labelledby="per-account-heading">
      <h2 id="per-account-heading" className="text-sm font-semibold text-muted-foreground">
        Per-account breakdown
      </h2>

      {/* Desktop: real table */}
      <div className="hidden rounded-lg border border-border bg-card md:block">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Account</TableHead>
              <TableHead className="text-right">Qty</TableHead>
              <TableHead className="text-right">Avg cost</TableHead>
              <TableHead className="text-right">Market value</TableHead>
              <TableHead className="text-right">Unrealized %</TableHead>
              <TableHead className="text-right">Realized</TableHead>
              <TableHead className="text-right">TWRR</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row) => {
              const closed = row.status === "closed";
              const qtyNum = toDisplayNumber(row.quantity);
              const priceNum = row.current_price !== null ? toDisplayNumber(row.current_price) : null;
              const marketValue = priceNum !== null && !closed ? qtyNum * priceNum : null;
              const decimals = decimalsFor({
                instrumentType: row.instrument_type,
                displayDecimals: row.display_decimals,
              });
              return (
                <TableRow
                  key={`${row.account_id}::${row.instrument_id}`}
                  className={cn(closed && "text-muted-foreground")}
                >
                  <TableCell>
                    <div className="flex items-center gap-2">
                      <span className="font-medium">{row.account_name}</span>
                      {closed && <Badge variant="secondary">Closed</Badge>}
                    </div>
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {closed ? "—" : formatQuantity(row.quantity, decimals)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {row.avg_cost === null
                      ? "—"
                      : formatMoney(row.avg_cost, currency)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {marketValue === null ? "—" : formatMoney(marketValue, currency)}
                  </TableCell>
                  <TableCell className="text-right">
                    {closed ? (
                      <span className="text-muted-foreground tabular-nums">—</span>
                    ) : (
                      <PercentCell value={row.percent_return} />
                    )}
                  </TableCell>
                  <TableCell className="text-right">
                    <RealizedCell value={row.realized_eur} currency={currency} />
                  </TableCell>
                  <TableCell className="text-right">
                    {closed ? (
                      <span className="text-muted-foreground tabular-nums">—</span>
                    ) : (
                      <TwrrCell value={row.twrr} />
                    )}
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </div>

      {/* Mobile: stacked cards */}
      <div className="space-y-3 md:hidden">
        {rows.map((row) => {
          const closed = row.status === "closed";
          const qtyNum = toDisplayNumber(row.quantity);
          const priceNum = row.current_price !== null ? toDisplayNumber(row.current_price) : null;
          const marketValue = priceNum !== null && !closed ? qtyNum * priceNum : null;
          const decimals = decimalsFor({
            instrumentType: row.instrument_type,
            displayDecimals: row.display_decimals,
          });
          return (
            <div
              key={`${row.account_id}::${row.instrument_id}`}
              className={cn(
                "rounded-lg border border-border bg-card p-4",
                closed && "text-muted-foreground"
              )}
            >
              <div className="flex items-baseline justify-between gap-2">
                <div className="flex items-center gap-2">
                  <span className="font-semibold">{row.account_name}</span>
                  {closed && <Badge variant="secondary">Closed</Badge>}
                </div>
                {!closed && <PercentCell value={row.percent_return} />}
              </div>
              <div className="mt-1 text-xs">
                <span className="text-muted-foreground">Qty: </span>
                <span className="tabular-nums">
                  {closed ? "—" : formatQuantity(row.quantity, decimals)}
                </span>
                <span className="ml-2 text-muted-foreground">Avg: </span>
                <span className="tabular-nums">
                  {row.avg_cost === null ? "—" : formatMoney(row.avg_cost, currency)}
                </span>
              </div>
              <div className="mt-1 text-sm">
                <span className="text-muted-foreground">Market value: </span>
                <span className="tabular-nums">
                  {marketValue === null ? "—" : formatMoney(marketValue, currency)}
                </span>
              </div>
              <div className="mt-1 text-sm">
                <span className="text-muted-foreground">Realized: </span>
                <RealizedCell value={row.realized_eur} currency={currency} />
              </div>
              {!closed && (
                <div className="mt-1 text-sm">
                  <span className="text-muted-foreground">TWRR: </span>
                  <TwrrCell value={row.twrr} />
                </div>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}
