import { formatMoney } from "@/lib/format";

interface Props {
  value: string | null;
  closedAt: string | null; // ISO date YYYY-MM-DD or null
  currency: "EUR" | "USD";
}

function formatClosedDate(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  return new Intl.DateTimeFormat("en-GB", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  }).format(d);
}

export function LastClosePriceCell({ value, closedAt, currency }: Props) {
  return (
    <div className="text-right">
      <div className="tabular-nums">{value ? formatMoney(value, currency) : "—"}</div>
      {closedAt && (
        <div className="text-xs text-muted-foreground">Closed {formatClosedDate(closedAt)}</div>
      )}
    </div>
  );
}
