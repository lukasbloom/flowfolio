"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, ArrowUpDown } from "lucide-react";
import Link from "next/link";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { PercentCell } from "@/components/perf/PercentCell";
import { TwrrCell } from "@/components/perf/TwrrCell";
import { LastClosePriceCell } from "@/components/closed/LastClosePriceCell";
import { apiFetch } from "@/lib/api-client";
import { useCurrency } from "@/lib/currency";
import { useTagFilter } from "@/lib/tag-filter";
import { decimalsFor, formatMoney, formatQuantity } from "@/lib/format";
import { cn } from "@/lib/utils";

interface ClosedPosition {
  account_id: string;
  account_name: string;
  instrument_id: string;
  instrument_symbol: string;
  instrument_name: string;
  // ClosedPositionRow gained instrument_type + display_decimals
  // server-side so the table can render quantity at the correct
  // per-type precision.
  instrument_type: string;
  display_decimals?: number | null;
  quantity: string;
  avg_cost: string | null;
  last_close: string | null;
  last_close_date: string | null;
  percent_return: string | null;
  realized_eur: string | null;
  twrr: string | null;
  twrr_annualized: boolean;
  twrr_window_days: number | null;
}

type SortKey =
  | "instrument"
  | "quantity"
  | "avg_cost"
  | "last_close"
  | "percent_return"
  | "realized_eur"
  | "twrr";
type SortDir = "asc" | "desc";

const SORT_LABELS: Record<SortKey, string> = {
  instrument: "Instrument",
  quantity: "Qty",
  avg_cost: "Avg cost",
  last_close: "Last close",
  percent_return: "% return",
  realized_eur: "Realized",
  twrr: "TWRR",
};

function numericValue(row: ClosedPosition, key: SortKey): number | null {
  if (key === "instrument") return null;
  const raw = row[key as keyof ClosedPosition];
  return raw === null || raw === undefined ? null : Number(raw);
}

function compareRows(
  a: ClosedPosition,
  b: ClosedPosition,
  sortKey: SortKey,
  sortDir: SortDir
): number {
  if (sortKey === "instrument") {
    const result = a.instrument_symbol.localeCompare(b.instrument_symbol, "en");
    return sortDir === "asc" ? result : -result;
  }
  const av = numericValue(a, sortKey);
  const bv = numericValue(b, sortKey);
  if (av === null && bv === null) return 0;
  if (av === null) return 1;
  if (bv === null) return -1;
  const result = av - bv;
  return sortDir === "asc" ? result : -result;
}

function SortIcon({ active, dir }: { active: boolean; dir: SortDir }) {
  if (active) {
    const Icon = dir === "asc" ? ArrowUp : ArrowDown;
    return <Icon className="size-3 text-foreground" aria-hidden="true" />;
  }
  return (
    <ArrowUpDown className="size-3 text-muted-foreground opacity-0 group-hover/header:opacity-100" aria-hidden="true" />
  );
}

function HeaderButton({
  sortKey,
  activeKey,
  sortDir,
  onSort,
  align = "left",
  children,
}: {
  sortKey: SortKey;
  activeKey: SortKey;
  sortDir: SortDir;
  onSort: (key: SortKey) => void;
  align?: "left" | "right";
  children: React.ReactNode;
}) {
  const active = sortKey === activeKey;
  return (
    <button
      type="button"
      className={cn(
        "group/header inline-flex w-full items-center gap-1 text-xs font-medium",
        align === "right" && "justify-end"
      )}
      onClick={() => onSort(sortKey)}
      aria-pressed={active}
    >
      <span>{children}</span>
      <SortIcon active={active} dir={sortDir} />
    </button>
  );
}

function LoadingRows() {
  return (
    <div className="space-y-2">
      {[0, 1, 2].map((i) => (
        <Skeleton key={i} className="h-10 w-full" />
      ))}
    </div>
  );
}

export function ClosedPositionsTable() {
  const { currency } = useCurrency();
  const { tagFilter } = useTagFilter();
  const [sortKey, setSortKey] = useState<SortKey>("twrr");
  const [sortDir, setSortDir] = useState<SortDir>("desc");

  const { data, isLoading, isError, error, refetch } = useQuery<ClosedPosition[]>({
    queryKey: ["closed", currency, tagFilter],
    queryFn: () =>
      apiFetch<ClosedPosition[]>(
        `/api/closed?currency=${currency}${tagFilter ? `&tag=${encodeURIComponent(tagFilter)}` : ""}`
      ),
    staleTime: 30_000,
  });

  const sortedRows = useMemo(
    () => [...(data ?? [])].sort((a, b) => compareRows(a, b, sortKey, sortDir)),
    [data, sortDir, sortKey]
  );

  function toggleSort(nextKey: SortKey) {
    if (nextKey === sortKey) {
      setSortDir((dir) => (dir === "asc" ? "desc" : "asc"));
      return;
    }
    setSortKey(nextKey);
    setSortDir(nextKey === "instrument" ? "asc" : "desc");
  }

  if (isLoading) return <LoadingRows />;

  if (isError) {
    const message = error instanceof Error ? error.message : "Unknown error";
    return (
      <div className="flex flex-col items-center justify-center gap-3 rounded-lg border border-border bg-background p-8 text-center text-sm text-destructive">
        <p>Could not load closed positions. {message}</p>
        <Button variant="ghost" size="sm" onClick={() => refetch()}>
          Retry
        </Button>
      </div>
    );
  }

  if (sortedRows.length === 0) {
    return (
      <div className="rounded-lg border border-border bg-card p-6 text-center">
        <h2 className="text-base font-semibold">No closed positions yet</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Holdings stay here once their quantity drops to zero.
        </p>
      </div>
    );
  }

  // Closed-mode TWRR header: all rows with twrr_window_days >= 365 → annualized
  const allAnnualized = sortedRows.every(
    (r) => r.twrr_window_days != null && r.twrr_window_days >= 365
  );
  const twrrHeader = allAnnualized ? "TWRR (annualized)" : "TWRR (hold window)";

  return (
    <>
      <div className="hidden md:block">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>
                <HeaderButton
                  sortKey="instrument"
                  activeKey={sortKey}
                  sortDir={sortDir}
                  onSort={toggleSort}
                >
                  Instrument
                </HeaderButton>
              </TableHead>
              <TableHead className="text-right">
                <HeaderButton
                  sortKey="quantity"
                  activeKey={sortKey}
                  sortDir={sortDir}
                  onSort={toggleSort}
                  align="right"
                >
                  Qty
                </HeaderButton>
              </TableHead>
              <TableHead className="text-right">
                <HeaderButton
                  sortKey="avg_cost"
                  activeKey={sortKey}
                  sortDir={sortDir}
                  onSort={toggleSort}
                  align="right"
                >
                  Avg cost
                </HeaderButton>
              </TableHead>
              <TableHead className="text-right">
                <HeaderButton
                  sortKey="last_close"
                  activeKey={sortKey}
                  sortDir={sortDir}
                  onSort={toggleSort}
                  align="right"
                >
                  Last close
                </HeaderButton>
              </TableHead>
              <TableHead className="text-right">
                <HeaderButton
                  sortKey="percent_return"
                  activeKey={sortKey}
                  sortDir={sortDir}
                  onSort={toggleSort}
                  align="right"
                >
                  % return
                </HeaderButton>
              </TableHead>
              <TableHead className="text-right">
                <HeaderButton
                  sortKey="realized_eur"
                  activeKey={sortKey}
                  sortDir={sortDir}
                  onSort={toggleSort}
                  align="right"
                >
                  Realized
                </HeaderButton>
              </TableHead>
              <TableHead className="text-right">
                <HeaderButton
                  sortKey="twrr"
                  activeKey={sortKey}
                  sortDir={sortDir}
                  onSort={toggleSort}
                  align="right"
                >
                  {twrrHeader}
                </HeaderButton>
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {sortedRows.map((row) => (
              <TableRow key={`${row.account_id}::${row.instrument_id}`}>
                <TableCell>
                  <Link
                    href={`/holdings/i/${row.instrument_id}`}
                    className="font-semibold hover:underline"
                  >
                    {row.instrument_symbol}
                  </Link>
                  <div className="max-w-[18rem] truncate text-xs text-muted-foreground">
                    {row.instrument_name}
                  </div>
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {formatQuantity(row.quantity, decimalsFor({ instrumentType: row.instrument_type, displayDecimals: row.display_decimals }))}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {row.avg_cost === null ? "—" : formatMoney(row.avg_cost, currency)}
                </TableCell>
                <TableCell className="text-right">
                  <LastClosePriceCell
                    value={row.last_close}
                    closedAt={row.last_close_date}
                    currency={currency}
                  />
                </TableCell>
                <TableCell className="text-right">
                  <PercentCell value={row.percent_return} />
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {row.realized_eur === null ? "—" : formatMoney(row.realized_eur, currency)}
                </TableCell>
                <TableCell className="text-right">
                  <TwrrCell value={row.twrr} />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      <div className="space-y-3 md:hidden">
        <label
          className="block text-xs font-medium text-muted-foreground"
          htmlFor="closed-mobile-sort"
        >
          Sort
        </label>
        <select
          id="closed-mobile-sort"
          className="min-h-11 w-full rounded-md border border-border bg-background px-3 text-sm"
          value={`${sortKey}:${sortDir}`}
          onChange={(event) => {
            const [nextKey, nextDir] = event.target.value.split(":") as [SortKey, SortDir];
            setSortKey(nextKey);
            setSortDir(nextDir);
          }}
        >
          <option value="twrr:desc">Sort: {SORT_LABELS.twrr} ↓</option>
          <option value="twrr:asc">Sort: {SORT_LABELS.twrr} ↑</option>
          <option value="percent_return:desc">Sort: {SORT_LABELS.percent_return} ↓</option>
          <option value="avg_cost:desc">Sort: {SORT_LABELS.avg_cost} ↓</option>
          <option value="instrument:asc">Sort: Name A-Z</option>
        </select>

        {sortedRows.map((row) => (
          <div
            key={`${row.account_id}::${row.instrument_id}`}
            className="rounded-lg border border-border bg-card p-4"
          >
            <div className="flex min-w-0 items-baseline justify-between gap-3 text-base font-semibold">
              <Link
                href={`/holdings/i/${row.instrument_id}`}
                className="min-w-0 truncate hover:underline"
              >
                {row.instrument_symbol}
              </Link>
              <PercentCell value={row.percent_return} className="shrink-0" />
            </div>
            <div className="mt-1 truncate text-xs text-muted-foreground">
              {row.instrument_name}
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-x-1 gap-y-1 text-xs text-muted-foreground">
              <span className="tabular-nums">{formatQuantity(row.quantity, decimalsFor({ instrumentType: row.instrument_type, displayDecimals: row.display_decimals }))}</span>
              <span>@</span>
              <span className="tabular-nums">
                {row.avg_cost === null ? "—" : formatMoney(row.avg_cost, currency)}
              </span>
            </div>
            <div className="mt-2">
              <LastClosePriceCell
                value={row.last_close}
                closedAt={row.last_close_date}
                currency={currency}
              />
            </div>
            <div className="mt-2 text-sm">
              <span className="text-muted-foreground">Realized: </span>
              <span className="tabular-nums">
                {row.realized_eur === null ? "—" : formatMoney(row.realized_eur, currency)}
              </span>
            </div>
            <div className="mt-1 text-sm">
              <span className="text-muted-foreground">{twrrHeader}: </span>
              <TwrrCell value={row.twrr} />
            </div>
          </div>
        ))}
      </div>
    </>
  );
}
