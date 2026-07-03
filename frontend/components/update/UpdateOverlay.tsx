"use client";

import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, Loader2, XCircle } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { apiFetch } from "@/lib/api-client";
import {
  deriveOverlayPhase,
  overlayCopy,
  type OverlayPhase,
} from "@/lib/update-status";

interface UpdateStatusResponse {
  current_version: string;
  latest_version: string | null;
  update_in_progress: boolean;
  update_state: string | null;
  update_message: string | null;
  update_log_tail: string | null;
}

interface VersionResponse {
  version: string;
}

interface Props {
  /** The version we are updating to (drives the success version-flip check). */
  targetVersion: string;
  /** The version we are updating from (failure copy). */
  currentVersion: string;
}

const POLL_MS = 2000;
// Hard client-side ceiling. If the run never reaches a terminal state (a
// crashed/stuck updater), drop the infinite spinner and show a recoverable
// "couldn't confirm" terminal so the user is never trapped on the overlay.
const MAX_WAIT_MS = 5 * 60 * 1000;

/**
 * Blocking full-screen update overlay.
 *
 * Polls /api/update-status (+ /api/version for the success flip) and feeds both
 * through the pure `deriveOverlayPhase` state machine. It tolerates the backend
 * being briefly unreachable during the container recreate (an expected step, not
 * an error), auto-reloads on success, and on failure reveals a safe text log
 * tail (`whitespace-pre-wrap`, never rendered HTML) plus a clean reload path.
 */
export function UpdateOverlay({ targetVersion, currentVersion }: Props) {
  const [showDetails, setShowDetails] = useState(false);
  const [timedOut, setTimedOut] = useState(false);
  const cardRef = useRef<HTMLDivElement>(null);

  // Start the max-wait ceiling once, on mount.
  useEffect(() => {
    const t = setTimeout(() => setTimedOut(true), MAX_WAIT_MS);
    return () => clearTimeout(t);
  }, []);

  const statusQuery = useQuery({
    queryKey: ["update-status", "overlay"],
    queryFn: () => apiFetch<UpdateStatusResponse>("/api/update-status"),
    refetchInterval: POLL_MS,
    retry: false,
    gcTime: 0,
  });

  const versionQuery = useQuery({
    queryKey: ["version", "overlay"],
    queryFn: () => apiFetch<VersionResponse>("/api/version"),
    refetchInterval: POLL_MS,
    retry: false,
    gcTime: 0,
  });

  const phase: OverlayPhase = deriveOverlayPhase({
    updateState: statusQuery.data?.update_state ?? null,
    // A failed poll (network drop / 5xx) during recreate → unreachable, not error.
    pollFailed: statusQuery.isError,
    reportedVersion: versionQuery.data?.version ?? null,
    targetVersion,
  });

  // After the ceiling, a still-non-terminal run becomes a recoverable failure so
  // the user can reload rather than spin forever.
  const stuck = timedOut && phase !== "success" && phase !== "failed";
  const effectivePhase: OverlayPhase = stuck ? "failed" : phase;

  const logTail = statusQuery.data?.update_log_tail ?? null;
  const copy = stuck
    ? {
        heading: "This is taking longer than expected",
        sub: "We couldn't confirm the update finished. Reload Flowfolio and check Settings → Software updates.",
      }
    : overlayCopy(phase, currentVersion, targetVersion);

  // Auto-reload into the new version once we reach success.
  useEffect(() => {
    if (phase !== "success") return;
    const t = setTimeout(() => window.location.reload(), 1200);
    return () => clearTimeout(t);
  }, [phase]);

  // Move focus into the card on mount (simple focus trap — the card owns the
  // screen and has no dismiss affordance while running).
  useEffect(() => {
    cardRef.current?.focus();
  }, []);

  const isSpinner =
    effectivePhase === "preparing" ||
    effectivePhase === "pulling" ||
    effectivePhase === "restarting" ||
    effectivePhase === "unreachable";

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-background/80 backdrop-blur-sm p-6"
      role="alertdialog"
      aria-modal="true"
      aria-live="polite"
    >
      <div
        ref={cardRef}
        tabIndex={-1}
        className="bg-card border rounded-xl max-w-md w-full p-8 space-y-4 outline-none"
      >
        <div className="flex flex-col items-center text-center space-y-4">
          {isSpinner ? (
            <Loader2
              className="size-10 animate-spin text-muted-foreground"
              aria-hidden="true"
            />
          ) : effectivePhase === "success" ? (
            <CheckCircle2 className="size-10 text-positive" aria-hidden="true" />
          ) : (
            <XCircle className="size-10 text-destructive" aria-hidden="true" />
          )}
          <div className="space-y-1.5">
            <h2 className="text-base font-semibold">{copy.heading}</h2>
            <p className="text-sm text-muted-foreground">{copy.sub}</p>
          </div>
        </div>

        {effectivePhase === "failed" ? (
          <div className="space-y-3">
            <p className="text-sm text-muted-foreground text-center">
              If this keeps happening, check the server logs or update manually
              with docker compose pull.
            </p>
            <div className="flex flex-col gap-2">
              <Button onClick={() => window.location.reload()}>
                Reload Flowfolio
              </Button>
              <Button
                variant="ghost"
                onClick={() => setShowDetails((v) => !v)}
                aria-expanded={showDetails}
              >
                {showDetails ? "Hide details" : "View details"}
              </Button>
            </div>
            {showDetails && logTail ? (
              <pre className="whitespace-pre-wrap font-mono text-sm text-muted-foreground bg-muted border rounded-md max-h-48 overflow-y-auto p-3 tabular-nums">
                {logTail}
              </pre>
            ) : null}
          </div>
        ) : null}
      </div>
    </div>
  );
}
