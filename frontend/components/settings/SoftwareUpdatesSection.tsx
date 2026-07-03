"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { formatDistanceToNow, parseISO } from "date-fns";
import { CheckCircle2, ExternalLink, Loader2 } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { UpdateConfirmDialog } from "@/components/update/UpdateConfirmDialog";
import { UpdateOverlay } from "@/components/update/UpdateOverlay";
import { apiFetch } from "@/lib/api-client";
import { useConfig } from "@/lib/config";
import { withV } from "@/lib/update-status";

interface UpdateStatusResponse {
  current_version: string;
  latest_version: string | null;
  update_available: boolean;
  release_notes_url: string | null;
  dismissed: boolean;
  last_checked: string | null;
  check_failed: boolean;
  backups_configured: boolean;
  update_in_progress: boolean;
  update_state: string | null;
  update_message: string | null;
  update_log_tail: string | null;
}

interface ApplyResponse {
  request_id: string;
  state: string | null;
}

function lastCheckedLabel(iso: string | null): string | null {
  if (!iso) return null;
  try {
    return `Last checked ${formatDistanceToNow(parseISO(iso), { addSuffix: true })}.`;
  } catch {
    return null;
  }
}

/**
 * Settings → Software updates panel. The canonical action
 * home: it shows the TRUE current-vs-latest status (ignoring banner dismissal),
 * links the release notes out (never inline markdown), and drives confirm → apply
 * → blocking overlay. While a run is in flight the Update now button is disabled
 * with a spinner (the backend also re-attaches rather than re-recreating).
 */
export function SoftwareUpdatesSection() {
  const qc = useQueryClient();
  const { data: config } = useConfig();
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [overlayOpen, setOverlayOpen] = useState(false);

  const statusQuery = useQuery({
    queryKey: ["update-status"],
    queryFn: () => apiFetch<UpdateStatusResponse>("/api/update-status"),
    staleTime: 30_000,
  });

  const applyMutation = useMutation({
    mutationFn: () =>
      apiFetch<ApplyResponse>("/api/update/apply", { method: "POST" }),
    onSuccess: () => {
      setOverlayOpen(true);
      qc.invalidateQueries({ queryKey: ["update-status"] });
    },
  });

  // Hide the Settings "Software updates" panel in demo mode. UI
  // defense-in-depth ONLY — the enforcing control is the 403 on POST
  // /api/update/apply, which blocks a direct API call regardless of the UI.
  if (config?.demo) return null;

  const data = statusQuery.data;
  const inProgress = data?.update_in_progress ?? false;
  const busy = inProgress || applyMutation.isPending;

  // The panel always shows TRUE availability — unlike the banner it ignores the
  // dismissed flag. A failed daily check takes precedence in the copy.
  const updateAvailable =
    data != null &&
    !data.check_failed &&
    data.latest_version != null &&
    data.latest_version !== data.current_version;

  return (
    <section
      aria-labelledby="software-updates-heading"
      id="software-updates"
      className="space-y-4"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="space-y-1">
          <h2
            id="software-updates-heading"
            className="text-base font-semibold"
          >
            Software updates
          </h2>
          {statusQuery.isLoading ? (
            <Skeleton className="h-5 w-64" />
          ) : data ? (
            <StatusLine data={data} updateAvailable={updateAvailable} />
          ) : (
            <p className="text-sm text-muted-foreground">
              Couldn&apos;t load update status. Refresh the page to try again.
            </p>
          )}
        </div>

        {updateAvailable ? (
          <Button
            onClick={() => setConfirmOpen(true)}
            disabled={busy}
            className="shrink-0 min-h-11 sm:min-h-9"
          >
            {busy ? (
              <Loader2 className="size-4 animate-spin" aria-hidden="true" />
            ) : null}
            <span className={busy ? "ml-1.5" : undefined}>
              {inProgress ? "Updating…" : "Update now"}
            </span>
          </Button>
        ) : null}
      </div>

      {data?.latest_version ? (
        <UpdateConfirmDialog
          open={confirmOpen}
          onOpenChange={setConfirmOpen}
          onConfirm={() => {
            setConfirmOpen(false);
            applyMutation.mutate();
          }}
          isPending={applyMutation.isPending}
          latest={data.latest_version}
          current={data.current_version}
          backupsConfigured={data.backups_configured}
        />
      ) : null}

      {overlayOpen && data?.latest_version ? (
        <UpdateOverlay
          targetVersion={data.latest_version}
          currentVersion={data.current_version}
        />
      ) : null}
    </section>
  );
}

function StatusLine({
  data,
  updateAvailable,
}: {
  data: UpdateStatusResponse;
  updateAvailable: boolean;
}) {
  const current = withV(data.current_version);

  if (data.check_failed) {
    return (
      <p className="text-sm text-muted-foreground">
        Couldn&apos;t check for updates. We&apos;ll try again automatically.
        You&apos;re on {current}.
      </p>
    );
  }

  if (updateAvailable && data.latest_version) {
    return (
      <div className="space-y-1">
        <p className="text-sm text-muted-foreground">
          Flowfolio {withV(data.latest_version)} is available. You&apos;re on{" "}
          {current}.
        </p>
        {data.release_notes_url ? (
          <a
            href={data.release_notes_url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-sm underline text-muted-foreground hover:text-foreground"
          >
            View release notes
            <ExternalLink className="size-3.5" aria-hidden="true" />
          </a>
        ) : null}
      </div>
    );
  }

  const checked = lastCheckedLabel(data.last_checked);
  return (
    <div className="space-y-1">
      <p className="flex items-center gap-1.5 text-sm text-muted-foreground">
        <CheckCircle2 className="size-4 text-positive" aria-hidden="true" />
        You&apos;re on the latest version ({current}).
      </p>
      {checked ? (
        <p className="text-sm text-muted-foreground">{checked}</p>
      ) : null}
    </div>
  );
}
