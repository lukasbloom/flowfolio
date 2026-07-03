"use client";

import { Calendar } from "lucide-react";

import { Badge } from "@/components/ui/badge";

interface Props {
  lastReconciledDate: string | null | undefined;
}

export function LastReconciledBadge({ lastReconciledDate }: Props) {
  const text = lastReconciledDate
    ? `Reconciled ${lastReconciledDate}`
    : "Never reconciled";
  return (
    <Badge variant="outline" className="h-6 gap-1 rounded-full px-2 text-xs">
      <Calendar className="size-3" aria-hidden />
      <span className="text-muted-foreground">{text}</span>
    </Badge>
  );
}
