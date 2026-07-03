"use client";

import { useQuery } from "@tanstack/react-query";
import { type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { apiFetch } from "@/lib/api-client";
import { formatQuantity } from "@/lib/format";
import { cn } from "@/lib/utils";

const FIELD_LABELS: Record<string, string> = {
  quantity: "Qty",
  unit_price: "Unit price",
  price_currency: "Currency",
  fx_rate_to_eur: "FX rate (EUR base)",
  fee_eur: "Fee",
  notes: "Notes",
  date: "Date",
};

interface AuditEvent {
  id: string;
  transaction_id: string;
  event_type: "edit" | "delete";
  changed_at: string;
  changed_fields: Record<string, { old: unknown; new: unknown }>;
}

interface Transaction {
  id: string;
  instrument_symbol: string;
  txn_type: string;
  date: string;
}

interface Props {
  txnId: string | null;
  onClose: () => void;
}

function formatDateTime(iso: string): string {
  try {
    return new Date(iso).toLocaleString("en-GB", {
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString("en-GB", {
      year: "numeric",
      month: "short",
      day: "numeric",
    });
  } catch {
    return iso;
  }
}

export function AuditHistoryModal({ txnId, onClose }: Props) {
  const { data: auditEvents, isLoading: auditLoading } = useQuery({
    queryKey: ["audit", txnId],
    queryFn: () => apiFetch<AuditEvent[]>(`/api/transactions/${txnId}/audit`),
    enabled: !!txnId,
  });

  const { data: txnData } = useQuery({
    queryKey: ["transaction", txnId],
    queryFn: () => apiFetch<Transaction>(`/api/transactions/${txnId}`),
    enabled: !!txnId,
  });

  return (
    <Dialog open={txnId !== null} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-2xl max-h-[80vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Transaction history</DialogTitle>
          {txnData && (
            <p className="text-sm text-muted-foreground">
              {txnData.instrument_symbol} ·{" "}
              <span className="capitalize">{txnData.txn_type}</span> ·{" "}
              {txnData.date}
            </p>
          )}
        </DialogHeader>

        {auditLoading ? (
          <div className="space-y-3 py-2">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : !auditEvents || auditEvents.length === 0 ? (
          <p className="text-sm text-muted-foreground py-4">
            No history yet. Edits and deletions will appear here.
          </p>
        ) : (
          <ul className="space-y-0 py-2">
            {auditEvents.map((event, idx) => (
              <li key={event.id}>
                <div className="relative pl-6 py-3">
                  <span
                    className={cn(
                      "absolute left-0 top-4 size-2 rounded-full",
                      event.event_type === "edit"
                        ? "bg-foreground"
                        : "border border-foreground bg-background"
                    )}
                  />
                  <div className="text-sm font-semibold">
                    {event.event_type === "edit" ? "Edited" : "Deleted"}
                  </div>
                  <div className="text-xs text-muted-foreground">
                    {formatDateTime(event.changed_at)}
                  </div>
                  {event.event_type === "edit" && event.changed_fields && (
                    <ul className="mt-1 space-y-1 text-sm">
                      {Object.entries(event.changed_fields).map(
                        ([field, diff]) => {
                          const renderValue = (v: unknown): ReactNode => {
                            if (v == null) return "—";
                            if (field === "quantity") {
                              return formatQuantity(String(v));
                            }
                            if (field === "notes") {
                              const s = String(v);
                              if (/^auto-accrual\s+/.test(s)) {
                                const remainder = s.replace(
                                  /^auto-accrual\s+/,
                                  ""
                                );
                                return (
                                  <span className="inline-flex items-center gap-2">
                                    <Badge
                                      variant="outline"
                                      className="shrink-0"
                                    >
                                      auto-accrual
                                    </Badge>
                                    <span>{remainder}</span>
                                  </span>
                                );
                              }
                              return s;
                            }
                            return String(v);
                          };
                          return (
                            <li key={field}>
                              <span className="font-semibold">
                                {FIELD_LABELS[field] ?? field}
                              </span>{" "}
                              changed from{" "}
                              <span className="tabular-nums">
                                {renderValue(diff.old)}
                              </span>{" "}
                              to{" "}
                              <span className="tabular-nums">
                                {renderValue(diff.new)}
                              </span>
                            </li>
                          );
                        }
                      )}
                    </ul>
                  )}
                  {event.event_type === "delete" && (
                    <p className="mt-1 text-sm">
                      Marked deleted on {formatDate(event.changed_at)}.
                    </p>
                  )}
                </div>
                {idx < auditEvents.length - 1 && <Separator />}
              </li>
            ))}
          </ul>
        )}

        <DialogFooter>
          <Button onClick={onClose}>Close</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
