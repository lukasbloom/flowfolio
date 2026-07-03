"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { apiFetch } from "@/lib/api-client";

interface MuteButtonProps {
  instrumentId: string;
  symbol: string;
}

export function MuteButton({ instrumentId, symbol }: MuteButtonProps) {
  const queryClient = useQueryClient();

  const mutation = useMutation({
    mutationFn: () =>
      apiFetch<void>(`/api/concentration/mute/${encodeURIComponent(instrumentId)}`, {
        method: "POST",
      }),
    onSuccess: () => {
      toast.success(
        `Concentration alert silenced for ${symbol}. Manage muted holdings in Settings.`
      );
      void queryClient.invalidateQueries({ queryKey: ["concentration"] });
      void queryClient.invalidateQueries({ queryKey: ["concentration-mutes"] });
    },
    onError: (err: Error) => {
      toast.error(`Could not silence alert for ${symbol}. ${err.message}`, {
        duration: 6000,
      });
    },
  });

  return (
    <Button
      type="button"
      variant="default"
      size="sm"
      className="min-h-11"
      disabled={mutation.isPending}
      onClick={() => mutation.mutate()}
    >
      Mute {symbol}
    </Button>
  );
}
