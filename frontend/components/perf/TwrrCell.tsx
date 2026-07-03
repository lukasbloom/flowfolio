"use client";

import { PercentCell } from "@/components/perf/PercentCell";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

interface TwrrCellProps {
  value: string | number | null;
  className?: string;
}

export function TwrrCell({ value, className }: TwrrCellProps) {
  if (value === null) {
    return (
      <Tooltip delayDuration={0}>
        <TooltipTrigger asChild>
          <span
            className={cn("cursor-help text-muted-foreground tabular-nums", className)}
            aria-label="TWRR needs at least 7 days of data within the selected timeframe."
          >
            —
          </span>
        </TooltipTrigger>
        <TooltipContent>TWRR needs ≥7 days of data within the selected timeframe.</TooltipContent>
      </Tooltip>
    );
  }

  return <PercentCell value={value} className={className} />;
}
