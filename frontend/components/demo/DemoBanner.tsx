"use client";

import { FlaskConical, ExternalLink } from "lucide-react";

import { ActionBanner } from "@/components/ui/action-banner";
import { useConfig } from "@/lib/config";

// Persistent, NON-dismissible demo framing. Gated on the
// /api/config demo flag, so it renders nothing in normal mode. It uses
// ActionBanner (no X, no onDismiss) by construction, contrast UpdateBanner,
// which is dismissible. The link points at the GitHub repo for now; the
// landing-page link will be wired in once the landing exists.
const REPO_URL = "https://github.com/lukasbloom/flowfolio";

export function DemoBanner() {
  const { data } = useConfig();

  if (!data?.demo) return null;

  return (
    <ActionBanner
      className="mb-6 bg-card"
      icon={
        <FlaskConical
          className="size-4 text-muted-foreground"
          aria-hidden="true"
        />
      }
      title="You're exploring the Flowfolio demo"
      body="This is a synthetic sample portfolio, not real financial data. Add, edit, and explore freely — the demo resets to its seed every few hours, so any changes are temporary."
      action={
        <a
          href={REPO_URL}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 text-sm underline text-muted-foreground hover:text-foreground"
        >
          View Flowfolio on GitHub
          <ExternalLink className="size-3.5" aria-hidden="true" />
        </a>
      }
    />
  );
}
