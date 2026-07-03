"use client";

// Renders as a DialogContent slot — parent mounts the <Dialog> wrapper (see compare/page.tsx).
// The data-testid="allocation-drill" wrapper stays for visual-regression continuity.

import {
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { PerfTable } from "@/components/perf/PerfTable";
import { cn } from "@/lib/utils";

type Dimension = "type" | "risk" | "account" | "banked";

interface AllocationDrillProps {
  dimension: Dimension;
  sliceLabel: string;
}

export function AllocationDrill({ dimension, sliceLabel }: AllocationDrillProps) {
  return (
    <DialogContent className="sm:max-w-4xl">
      <DialogHeader>
        <DialogTitle>Holdings — {sliceLabel}</DialogTitle>
        <DialogDescription>
          Showing holdings for {dimension}
        </DialogDescription>
      </DialogHeader>
      <div
        data-testid="allocation-drill"
        className={cn(
          "max-h-[70vh] overflow-y-auto -mx-6 px-6 -mb-6 pb-6",
          // always-visible scrollbar — overrides macOS
          // "auto-hide" default so users can tell content overflows without
          // needing to scroll first. Styling lives on the scrollbar pseudo-
          // element (not on overflow), so when content fits within max-h
          // the gutter is still hidden — preserves the tight look for small
          // slices. WebKit (Chrome / Safari / Edge / Brave):
          "[&::-webkit-scrollbar]:w-2",
          "[&::-webkit-scrollbar-thumb]:rounded-full",
          "[&::-webkit-scrollbar-thumb]:bg-border",
          "[&::-webkit-scrollbar-track]:bg-muted/30",
          // Firefox (no pseudo-element support; thin scrollbar via standard property):
          "[scrollbar-width:thin]"
        )}
      >
        <PerfTable
          filterBy={{ dimension, value: sliceLabel }}
          respectExcludeClosed
          showTotals
        />
      </div>
    </DialogContent>
  );
}
