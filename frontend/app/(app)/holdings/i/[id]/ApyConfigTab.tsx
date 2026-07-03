"use client";

import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Trash2 } from "lucide-react";
import { toast } from "sonner";
import { ApyConfigForm } from "@/components/forms/ApyConfigForm";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { apiFetch, ApiError } from "@/lib/api-client";

interface Account {
  id: string;
  name: string;
}

interface ApyConfigResponse {
  id: string;
  account_id: string;
  instrument_id: string;
  apy_rate: string;
  effective_from: string;
  effective_to: string | null;
  compounding: string;
}

export function ApyConfigTab({ instrumentId }: { instrumentId: string }) {
  const qc = useQueryClient();

  const searchParams = useSearchParams();
  const accountId = searchParams.get("account") ?? undefined;

  const rowRefs = useRef<Record<string, HTMLTableRowElement | null>>({});
  const [pulseRowId, setPulseRowId] = useState<string | null>(null);

  const { data: accounts = [] } = useQuery({
    queryKey: ["accounts"],
    queryFn: () => apiFetch<Account[]>("/api/accounts"),
  });

  const { data: configs, isLoading } = useQuery({
    queryKey: ["apy-config", instrumentId],
    queryFn: () =>
      apiFetch<ApyConfigResponse[]>(`/api/apy-config?instrument_id=${instrumentId}`),
  });

  useEffect(() => {
    if (!accountId || !configs || configs.length === 0) return;
    const match = configs.find((c) => c.account_id === accountId);
    if (!match) return;   // silent no-match
    const el = rowRefs.current[match.id];
    if (!el) return;
    el.scrollIntoView({ block: "center", behavior: "smooth" });
    setPulseRowId(match.id);
    const t = window.setTimeout(() => setPulseRowId(null), 1500);
    return () => window.clearTimeout(t);
  }, [accountId, configs]);

  const deleteMutation = useMutation({
    mutationFn: (configId: string) =>
      apiFetch(`/api/apy-config/${configId}`, { method: "DELETE" }),
    onSuccess: () => {
      toast.success("APY config deleted.");
      qc.invalidateQueries({ queryKey: ["apy-config", instrumentId] });
    },
    onError: (err: Error) => {
      // 409 when the config is referenced by yield txns.
      if (err instanceof ApiError && err.status === 409) {
        toast.error(
          "APY rate is referenced by yield transactions. Close it via a new effective_from instead.",
        );
        return;
      }
      toast.error(`Could not delete. ${err.message}`);
    },
  });

  const accountName = (id: string) => accounts.find((a) => a.id === id)?.name ?? id;

  return (
    <div className="space-y-8">
      <section>
        <h2 className="text-2xl font-semibold leading-tight">Add APY rate</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Declare the annual yield rate the broker pays on this position. A new entry
          auto-closes the previous rate (effective_to set to the day before).
        </p>
        <div className="mt-4">
          <ApyConfigForm instrumentId={instrumentId} accountId={accountId} />
        </div>
      </section>
      <section>
        <h2 className="text-2xl font-semibold leading-tight">History</h2>
        {isLoading ? (
          <Skeleton className="mt-4 h-40 w-full" />
        ) : !configs || configs.length === 0 ? (
          <p className="mt-4 text-sm text-muted-foreground">
            No APY rates configured yet for this instrument.
          </p>
        ) : (
          <Table className="mt-4">
            <TableHeader>
              <TableRow>
                <TableHead>Account</TableHead>
                <TableHead className="text-right">APY %</TableHead>
                <TableHead>Effective from</TableHead>
                <TableHead>Effective to</TableHead>
                <TableHead></TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {configs.map((c) => (
                <TableRow
                  key={c.id}
                  ref={(el) => { rowRefs.current[c.id] = el; }}
                  data-pulse={pulseRowId === c.id ? "true" : undefined}
                >
                  <TableCell>{accountName(c.account_id)}</TableCell>
                  <TableCell className="text-right tabular-nums">
                    {(Number(c.apy_rate) * 100).toFixed(4)}%
                  </TableCell>
                  <TableCell className="tabular-nums">{c.effective_from}</TableCell>
                  <TableCell className="tabular-nums text-muted-foreground">
                    {c.effective_to ?? "—"}
                  </TableCell>
                  <TableCell>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="min-h-11"
                      onClick={() => deleteMutation.mutate(c.id)}
                      aria-label={`Delete APY config from ${c.effective_from}`}
                    >
                      <Trash2 className="size-4" />
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </section>
    </div>
  );
}
