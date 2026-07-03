"use client";

import { useEffect, useState } from "react";
import { AlertTriangle } from "lucide-react";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { STALE_MS, formatRelativeHours } from "@/lib/format";

interface StaleBadgeProps {
  fetchedAt: string | null | undefined; // ISO 8601 from API
}

export function StaleBadge({ fetchedAt }: StaleBadgeProps) {
  // Date.now() is impure — compute ageMs in an effect after mount so the
  // render is deterministic w.r.t. props. Re-checks every 60s while mounted.
  const [now, setNow] = useState<number | null>(null);

  useEffect(() => {
    // setState in effect is intentional: Date.now() is impure so we cannot
    // call it during render. The interval re-checks staleness every 60s
    // (callback path is not a "synchronous" effect body and is allowed).
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setNow(Date.now());
    const id = setInterval(() => setNow(Date.now()), 60_000);
    return () => clearInterval(id);
  }, []);

  if (!fetchedAt) return null;
  if (now === null) return null; // SSR + first paint: hide until hydrated
  const ageMs = now - new Date(fetchedAt).getTime();
  if (ageMs < STALE_MS) return null;
  const ageLabel = formatRelativeHours(fetchedAt, now);
  return (
    <Tooltip delayDuration={0}>
      <TooltipTrigger asChild>
        <span
          className="ml-1 inline-flex items-center gap-1 rounded border border-amber-500 bg-amber-500/15 px-1.5 py-0.5 text-xs font-medium text-amber-700"
          aria-label={`Stale price — last refreshed ${ageLabel} ago`}
        >
          <AlertTriangle className="size-3" aria-hidden="true" />
          Stale
        </span>
      </TooltipTrigger>
      <TooltipContent>
        Stale — last refreshed {ageLabel} ago. Daily refresh runs at 22:00 UTC.
      </TooltipContent>
    </Tooltip>
  );
}
