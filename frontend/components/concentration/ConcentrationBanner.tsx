"use client";

import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, ChevronDown, ChevronUp, X } from "lucide-react";
import Link from "next/link";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { apiFetch } from "@/lib/api-client";
import { MuteButton } from "@/components/concentration/MuteButton";
import { useConcentrationBanner } from "@/components/concentration/ConcentrationBannerProvider";

interface ConcentrationOffender {
  instrument_id: string;
  instrument_symbol: string;
  percent: string;
}

interface ConcentrationResponse {
  threshold: string;
  offenders: ConcentrationOffender[];
}

export function ConcentrationBanner() {
  const { expanded, setExpanded, dismissedKey, setDismissedKey } =
    useConcentrationBanner();

  const { data } = useQuery({
    queryKey: ["concentration"],
    queryFn: () => apiFetch<ConcentrationResponse>("/api/concentration"),
    staleTime: 30_000,
  });

  if (!data || data.offenders.length === 0) return null;

  const { threshold, offenders } = data;
  // Backend serializes percentages as decimal strings (CLAUDE.md
  // "NEVER float for money"). At display time we deliberately step out of
  // Decimal, `Number(...)` followed by `.toFixed(N)` is safe for values
  // far below `Number.MAX_SAFE_INTEGER` and the precision we render
  // (threshold to 0dp, offender percent to 1dp) is orders of magnitude
  // above what IEEE-754 boundary cases could perturb. Keep this boundary
  // tight: do NOT promote Number()'d percentages into anything that
  // round-trips back to the server.
  const thresholdN = (Number(threshold) * 100).toFixed(0);
  const n = offenders.length;

  // Dismissal is keyed by the current offender set so the banner
  // reappears automatically when the breaching holdings change (e.g. a
  // newly added position crosses the threshold). MuteButton remains the
  // mechanism for per-instrument suppression.
  const currentKey = offenders
    .map((o) => o.instrument_id)
    .sort()
    .join(",");
  const isDismissed = dismissedKey !== null && dismissedKey === currentKey;
  if (isDismissed) return null;

  // Single-breach inline shape: no Review expander, MuteButton inline.
  if (n === 1) {
    const o = offenders[0];
    const pct = (Number(o.percent) * 100).toFixed(1);
    return (
      <Alert
        // The shadcn Alert grid is items-start, but the single row is 44px tall
        // (the min-h-11 Mute button), so a top-aligned icon floats above the
        // text. items-center centers it; translate-y-0 zeroes the primitive's
        // +2px svg nudge (Tailwind v4 `translate` property; meant for top-aligned
        // alerts) so the icon's center lines up exactly with the text's center.
        className="mb-6 items-center [&>svg]:translate-y-0 border border-border border-l-4 border-l-amber-500 bg-card"
        role="alert"
      >
        <AlertTriangle className="size-4 text-amber-500" aria-hidden="true" />
        <AlertTitle className="flex items-center justify-between gap-2">
          <span>
            {o.instrument_symbol} exceeds your {thresholdN}% concentration threshold ({pct}%).
          </span>
          <div className="flex items-center gap-2">
            <MuteButton instrumentId={o.instrument_id} symbol={o.instrument_symbol} />
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label="Dismiss banner"
              className="shrink-0"
              onClick={() => setDismissedKey(currentKey)}
            >
              <X className="size-4" aria-hidden="true" />
            </Button>
          </div>
        </AlertTitle>
      </Alert>
    );
  }

  // Multi-breach (n >= 2): collapsed summary + Review expander.
  return (
    <Alert
      // Center the icon with the single summary row when collapsed (same 44px
      // row-height issue as the single-breach banner); revert to the grid's
      // top-alignment when expanded so the icon sits at the top of the list.
      className={`mb-6 border border-border border-l-4 border-l-amber-500 bg-card ${
        expanded ? "" : "items-center [&>svg]:translate-y-0"
      }`}
      role="alert"
    >
      <AlertTriangle className="size-4 text-amber-500" aria-hidden="true" />
      <AlertTitle className="flex items-center justify-between gap-2">
        <span>
          {n} holdings exceed your {thresholdN}% concentration threshold.
        </span>
        <div className="flex items-center gap-2">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => setExpanded(!expanded)}
            aria-expanded={expanded}
          >
            Review
            {expanded ? (
              <ChevronUp className="ml-1 size-3.5" aria-hidden="true" />
            ) : (
              <ChevronDown className="ml-1 size-3.5" aria-hidden="true" />
            )}
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label="Dismiss banner"
            className="shrink-0"
            onClick={() => setDismissedKey(currentKey)}
          >
            <X className="size-4" aria-hidden="true" />
          </Button>
        </div>
      </AlertTitle>
      {expanded && (
        <AlertDescription className="mt-3 space-y-2">
          {offenders.map((o) => (
            <div key={o.instrument_id} className="flex items-center justify-between gap-3">
              <div className="flex items-baseline gap-2">
                <span className="font-medium">{o.instrument_symbol}</span>
                <span className="text-sm text-muted-foreground tabular-nums">
                  {(Number(o.percent) * 100).toFixed(1)}%
                </span>
              </div>
              <MuteButton instrumentId={o.instrument_id} symbol={o.instrument_symbol} />
            </div>
          ))}
          <p className="text-sm text-muted-foreground pt-2">
            <Link href="/settings" className="underline hover:text-foreground">
              Manage threshold from Settings.
            </Link>
          </p>
        </AlertDescription>
      )}
    </Alert>
  );
}
