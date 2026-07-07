"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { formatDistanceToNow, parseISO } from "date-fns";
import { CheckCircle2, ExternalLink, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { apiFetch } from "@/lib/api-client";
import { useConfig } from "@/lib/config";
import { updateActionable, withV } from "@/lib/update-status";

interface UpdateStatusResponse {
  current_version: string;
  latest_version: string | null;
  update_available: boolean;
  release_notes_url: string | null;
  dismissed: boolean;
  last_checked: string | null;
  check_failed: boolean;
  is_dev: boolean;
  backups_configured: boolean;
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
 * Settings -> Software updates panel. Shows the TRUE current-vs-latest
 * status (ignoring banner dismissal), links release notes out, and when a
 * newer release exists tells the user how to update their own container.
 * "Check for updates" forces an immediate GitHub re-check.
 */
export function SoftwareUpdatesSection() {
  const qc = useQueryClient();
  const { data: config } = useConfig();

  const statusQuery = useQuery({
    queryKey: ["update-status"],
    queryFn: () => apiFetch<UpdateStatusResponse>("/api/update-status"),
    staleTime: 30_000,
  });

  // Force an immediate GitHub re-check (the daily cron only refreshes once per
  // UTC day). Refetch the status afterward so the panel + banner reflect it.
  const checkMutation = useMutation({
    mutationFn: () =>
      apiFetch<{ status: string; latest_version: string | null }>(
        "/api/update/check",
        { method: "POST" },
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["update-status"] });
    },
  });

  // Hide the Settings "Software updates" panel in demo mode. The hosted demo
  // is not the user's own container, so container-update guidance does not apply.
  if (config?.demo) return null;

  const data = statusQuery.data;

  // The panel always shows TRUE availability (unlike the banner, it ignores
  // the dismissed flag). A failed daily check takes precedence in the copy. A
  // dev build is never actionable (updateActionable returns false on is_dev).
  const updateAvailable =
    data != null &&
    updateActionable({
      checkFailed: data.check_failed,
      isDev: data.is_dev,
      latestVersion: data.latest_version,
      currentVersion: data.current_version,
    });

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

        <div className="flex shrink-0 flex-wrap items-center justify-end gap-2">
          <Button
            variant="outline"
            onClick={() => checkMutation.mutate()}
            disabled={checkMutation.isPending}
            className="min-h-11 sm:min-h-9"
          >
            {checkMutation.isPending ? (
              <Loader2 className="size-4 animate-spin" aria-hidden="true" />
            ) : null}
            <span className={checkMutation.isPending ? "ml-1.5" : undefined}>
              {checkMutation.isPending ? "Checking…" : "Check for updates"}
            </span>
          </Button>
        </div>
      </div>

      {updateAvailable && data?.latest_version ? (
        <div className="space-y-2 rounded-md border bg-muted/40 p-3">
          <p className="text-sm text-muted-foreground">
            Flowfolio {withV(data.latest_version)} is ready. Update your
            container to upgrade. Your data volume and settings are preserved.
          </p>
          <pre className="overflow-x-auto rounded bg-muted px-3 py-2 text-xs">
            <code>docker compose pull &amp;&amp; docker compose up -d</code>
          </pre>
          <p className="text-sm text-muted-foreground">
            On Portainer, open the stack and choose{" "}
            <span className="font-medium text-foreground">Pull and redeploy</span>.
          </p>
        </div>
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

  // Dev build: the version comparison is meaningless (source-mounted, no image
  // to pull). Explain rather than imply an update path.
  if (data.is_dev) {
    return (
      <div className="space-y-1">
        <p className="text-sm text-muted-foreground">
          Development build ({current}). Update by pulling the latest source and
          rebuilding.
        </p>
        {data.latest_version ? (
          <p className="text-sm text-muted-foreground">
            Latest release: {withV(data.latest_version)}.
          </p>
        ) : null}
      </div>
    );
  }

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
