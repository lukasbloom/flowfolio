"use client";

import { directionalColor, formatPercent } from "@/lib/format";
import { cn } from "@/lib/utils";

interface PercentCellProps {
  value: string | number | null;
  className?: string;
}

export function PercentCell({ value, className }: PercentCellProps) {
  if (value === null) {
    return <span className={cn("text-muted-foreground tabular-nums", className)}>—</span>;
  }

  const numeric = typeof value === "string" ? Number(value) : value;
  const colorClass = directionalColor(numeric);

  return (
    <span className={cn("tabular-nums", colorClass, className)}>
      {formatPercent(numeric, { signed: true })}
    </span>
  );
}
