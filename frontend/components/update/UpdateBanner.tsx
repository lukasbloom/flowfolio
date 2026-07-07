"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowUpCircle, X } from "lucide-react";
import Link from "next/link";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { apiFetch } from "@/lib/api-client";
import { useConfig } from "@/lib/config";
import { useUpdateBanner } from "@/components/update/UpdateBannerProvider";

interface UpdateStatusResponse {
  current_version: string;
  latest_version: string | null;
  update_available: boolean;
  release_notes_url: string | null;
  dismissed: boolean;
  last_checked: string | null;
  check_failed: boolean;
}

// Copy contract: render versions with a single leading "v" (e.g. v1.2.1).
// Skip the prefix for non-numeric labels like the "dev" build tag.
function withV(version: string): string {
  return /^\d/.test(version) ? `v${version}` : version;
}

export function UpdateBanner() {
  const { dismissedVersion, dismiss } = useUpdateBanner();
  const { data: config } = useConfig();

  const { data } = useQuery({
    queryKey: ["update-status"],
    queryFn: () => apiFetch<UpdateStatusResponse>("/api/update-status"),
    staleTime: 30_000,
  });

  // Hide the update banner in demo mode. The hosted demo is not the user's
  // own container, so there is nothing for them to update. The banner is a
  // passive, dismissable notice. It only links to Settings and never offers
  // an in-app apply action.
  if (config?.demo) return null;

  // Absent when up to date, and instantly hidden after an optimistic
  // dismiss of this exact latest version (server truth reconciles via the
  // provider's invalidate).
  if (!data || !data.update_available || !data.latest_version) return null;
  if (dismissedVersion === data.latest_version) return null;

  const { current_version, latest_version, release_notes_url } = data;

  return (
    <Alert
      // Quiet neutral treatment: bg-card + default border,
      // NOT the amber alarm left-border / role=alert reserved for ConcentrationBanner.
      className="mb-6 bg-card"
      role="status"
    >
      <ArrowUpCircle
        className="size-4 text-muted-foreground"
        aria-hidden="true"
      />
      <AlertTitle className="flex items-center justify-between gap-2">
        <span className="font-semibold">Update available</span>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          aria-label="Dismiss update notice"
          className="min-h-11 shrink-0"
          onClick={() => dismiss(latest_version)}
        >
          <X className="size-4" aria-hidden="true" />
        </Button>
      </AlertTitle>
      <AlertDescription className="mt-1 flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:items-center sm:gap-x-4">
        <span>
          {`Flowfolio ${withV(latest_version)} is ready. You're on ${withV(current_version)}.`}
        </span>
        <span className="flex flex-wrap gap-x-4 gap-y-1">
          {release_notes_url ? (
            <a
              href={release_notes_url}
              target="_blank"
              rel="noopener noreferrer"
              className="underline text-muted-foreground hover:text-foreground"
            >
              View release notes
            </a>
          ) : null}
          <Link
            href="/settings#software-updates"
            className="underline text-muted-foreground hover:text-foreground"
          >
            Update in Settings
          </Link>
        </span>
      </AlertDescription>
    </Alert>
  );
}
