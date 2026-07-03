"use client";

import { useQuery } from "@tanstack/react-query";
import { AlertCircle } from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { apiFetch } from "@/lib/api-client";

interface Instrument {
  id: string;
  symbol: string;
  risk_level: string | null;
}

export function UnclassifiedHint() {
  const { data, isLoading } = useQuery<Instrument[]>({
    queryKey: ["instruments"],
    queryFn: () => apiFetch<Instrument[]>("/api/instruments"),
  });

  if (isLoading) {
    return (
      <div>
        <h2 className="text-xl font-semibold">Risk classification</h2>
        <p className="mt-2 text-sm text-muted-foreground">Loading…</p>
      </div>
    );
  }

  // Count instruments still on default "Medium" risk level
  const mediumCount = (data ?? []).filter((i) => i.risk_level === "Medium").length;

  return (
    <div>
      <h2 className="text-xl font-semibold">Risk classification</h2>
      <div className="mt-3">
        {mediumCount > 0 ? (
          <Alert variant="default">
            <AlertCircle className="text-muted-foreground" />
            <AlertTitle>
              {mediumCount} instruments still need a risk classification.
            </AlertTitle>
            <AlertDescription>
              Classified instruments give the Risk pie a meaningful breakdown. Open an
              instrument&apos;s detail page to set its risk level (High / Medium / Low / Liquid).
            </AlertDescription>
          </Alert>
        ) : (
          <p className="text-sm text-muted-foreground">All instruments are classified.</p>
        )}
      </div>
    </div>
  );
}
