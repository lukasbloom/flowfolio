"use client";

import { Loader2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

interface BackfillBadgeProps {
  /**
   * Backfill state for the instrument. When omitted or anything other than
   * "pending"/"running", the badge renders nothing.
   *
   * TODO(phase-04): consume `instrument.backfill_status` from the backend
   * `/api/instruments/{id}` payload once the field is exposed. Until then this
   * component remains ready to be wired but renders nothing by default.
   */
  status?: "pending" | "running" | "complete" | "failed" | null;
  className?: string;
}

export function BackfillBadge({ status, className }: BackfillBadgeProps) {
  if (status !== "pending" && status !== "running") return null;
  return (
    <Badge
      variant="outline"
      className={cn(
        "inline-flex items-center gap-1 rounded-sm border-border bg-muted text-muted-foreground",
        className
      )}
      aria-live="polite"
    >
      <Loader2 className="size-3 animate-spin" aria-hidden="true" />
      <span className="text-xs">Backfilling history…</span>
    </Badge>
  );
}
