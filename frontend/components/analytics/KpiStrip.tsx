"use client";

import { useQuery } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api-client";
import { useCurrency } from "@/lib/currency";
import { useTagFilter } from "@/lib/tag-filter";
import { directionalColor, formatSignedMoney } from "@/lib/format";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

interface RealizedTotals {
  currency: string;
  lifetime: string | null;
  this_year: string | null;
}

interface RealizedResponse {
  totals: RealizedTotals;
  per_holding: { instrument_id: string; instrument_symbol: string; realized_eur: string }[];
}

interface KpiCardProps {
  label: string;
  value: string | null;
  currency: "EUR" | "USD";
}

function KpiCard({ label, value, currency }: KpiCardProps) {
  const colorClass = directionalColor(value);
  const formattedValue = value === null ? "—" : formatSignedMoney(value, currency);

  return (
    <div className="min-h-24 md:min-h-24 p-5 bg-card border border-border rounded-lg">
      <dl>
        <dt className="text-xs text-muted-foreground">{label}</dt>
        <dd
          className={cn(
            "mt-2 text-base font-semibold tabular-nums",
            colorClass
          )}
        >
          {formattedValue}
        </dd>
      </dl>
    </div>
  );
}

export function KpiStrip() {
  const { currency } = useCurrency();
  const { tagFilter } = useTagFilter();

  const { data, isLoading, isError } = useQuery<RealizedResponse>({
    queryKey: ["realized", currency, tagFilter],
    queryFn: () =>
      apiFetch<RealizedResponse>(
        `/api/realized?currency=${currency}${
          tagFilter ? `&tag=${encodeURIComponent(tagFilter)}` : ""
        }`
      ),
  });

  if (isLoading) {
    return (
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <Skeleton className="min-h-24 rounded-lg" />
        <Skeleton className="min-h-24 rounded-lg" />
      </div>
    );
  }

  if (isError) {
    return (
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div className="min-h-24 p-5 bg-card border border-border rounded-lg flex items-center justify-center text-sm text-destructive">
          Could not load realized totals.
        </div>
      </div>
    );
  }

  const lifetime = data?.totals?.lifetime ?? null;
  const thisYear = data?.totals?.this_year ?? null;
  const bothNull = lifetime === null && thisYear === null;

  return (
    <>
      {bothNull && (
        <p className="text-sm text-muted-foreground mb-4">
          Realized gains appear here once you record a sell or spend.
        </p>
      )}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <KpiCard
          label="Realized total (lifetime)"
          value={lifetime}
          currency={currency as "EUR" | "USD"}
        />
        <KpiCard
          label="Realized this year"
          value={thisYear}
          currency={currency as "EUR" | "USD"}
        />
      </div>
    </>
  );
}
