"use client";

import type { ReactNode } from "react";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { directionalColor, formatSignedMoney } from "@/lib/format";
import { cn } from "@/lib/utils";

interface RealizedCellProps {
  value: string | null;
  currency: "EUR" | "USD";
  className?: string;
}

/**
 * Renders a lifetime realized gain value with directional color.
 * Positive → text-positive (green)
 * Negative → text-negative (red)
 * Zero or null → text-muted-foreground
 *
 * Tooltip: "Lifetime realized gain. Computed from FIFO-matched disposals (sells and spends)."
 */
export function RealizedCell({ value, currency, className }: RealizedCellProps): ReactNode {
  if (value === null || value === undefined) {
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <span className={cn("tabular-nums text-muted-foreground", className)}>—</span>
        </TooltipTrigger>
        <TooltipContent>
          Lifetime realized gain. Computed from FIFO-matched disposals (sells and spends).
        </TooltipContent>
      </Tooltip>
    );
  }

  // directionalColor unifies the negative token on "text-negative" (was
  // "text-destructive" here) — the one intentional visual change in this dedupe.
  const colorClass = directionalColor(value);
  const signed = formatSignedMoney(value, currency);

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className={cn("tabular-nums", colorClass, className)}>{signed}</span>
      </TooltipTrigger>
      <TooltipContent>
        Lifetime realized gain. Computed from FIFO-matched disposals (sells and spends).
      </TooltipContent>
    </Tooltip>
  );
}
