"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { apiFetch } from "@/lib/api-client";

interface MuteRow {
  instrument_id: string;
  instrument_symbol: string;
  instrument_name: string;
}

function UnmuteButton({
  instrumentId,
  symbol,
}: {
  instrumentId: string;
  symbol: string;
}) {
  const qc = useQueryClient();

  const mutation = useMutation({
    mutationFn: () =>
      apiFetch(`/api/concentration/mute/${encodeURIComponent(instrumentId)}`, { method: "DELETE" }),
    onSuccess: () => {
      toast.success(
        `${symbol} unmuted. Concentration alert may reappear if it still exceeds the threshold.`,
        { duration: 4000 }
      );
      qc.invalidateQueries({ queryKey: ["concentration"] });
      qc.invalidateQueries({ queryKey: ["concentration-mutes"] });
    },
    onError: (err: Error) => {
      toast.error(`Could not unmute ${symbol}. ${err.message}.`, { duration: 6000 });
    },
  });

  return (
    <Button
      variant="outline"
      size="sm"
      onClick={() => mutation.mutate()}
      disabled={mutation.isPending}
    >
      Unmute
    </Button>
  );
}

export function MutedHoldingsList() {
  const { data, isLoading } = useQuery<MuteRow[]>({
    queryKey: ["concentration-mutes"],
    queryFn: () => apiFetch<MuteRow[]>("/api/concentration/mutes"),
  });

  return (
    <div>
      <h2 className="text-xl font-semibold">Muted holdings</h2>

      {isLoading && (
        <p className="mt-2 text-sm text-muted-foreground">Loading…</p>
      )}

      {!isLoading && (!data || data.length === 0) && (
        <div className="mt-3">
          <p className="text-sm text-muted-foreground">No holdings are currently muted.</p>
          <p className="mt-1 text-sm text-muted-foreground">
            Mute a holding from the concentration banner on your dashboard.
          </p>
        </div>
      )}

      {!isLoading && data && data.length > 0 && (
        <ul className="mt-3 space-y-2">
          {data.map((row) => (
            <li
              key={row.instrument_id}
              className="flex items-center justify-between gap-3 rounded-lg border border-border bg-card px-4 py-3"
            >
              <div className="min-w-0">
                <span className="block text-sm font-semibold">{row.instrument_symbol}</span>
                <span className="block text-xs text-muted-foreground">{row.instrument_name}</span>
              </div>
              <Badge
                variant="outline"
                className="shrink-0 border-warning text-warning"
              >
                muted
              </Badge>
              <UnmuteButton
                instrumentId={row.instrument_id}
                symbol={row.instrument_symbol}
              />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
