"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError, apiFetch } from "@/lib/api-client";

interface ProviderStatus {
  id: string;
  label: string;
  blurb: string;
  free_tier: string;
  register_url: string;
  optional: boolean;
  configured: boolean;
  masked: string | null;
}

interface KeysResponse {
  demo: boolean;
  providers: ProviderStatus[];
}

/** Pull the human message out of a FastAPI `{ "detail": ... }` body, else raw text. */
function errorDetail(err: unknown): string {
  if (err instanceof ApiError) {
    try {
      const parsed = JSON.parse(err.detail);
      if (parsed && typeof parsed.detail === "string") return parsed.detail;
    } catch {
      // detail was not JSON, fall through to the raw text
    }
    return err.detail;
  }
  return err instanceof Error ? err.message : "Something went wrong";
}

/**
 * Settings → Data sources / API keys. A dumb renderer over
 * GET /api/keys: masked status per provider, a Replace form that always starts
 * empty, a Clear action, and saves gated by the backend test-then-persist
 * (a 422 blocks the save and shows inline). Goes read-only in demo mode.
 */
export function DataSourcesSection() {
  const keysQuery = useQuery({
    queryKey: ["keys"],
    queryFn: () => apiFetch<KeysResponse>("/api/keys"),
    staleTime: 30_000,
  });

  const data = keysQuery.data;

  return (
    <section
      aria-labelledby="data-sources-heading"
      id="data-sources"
      className="space-y-4"
    >
      <div className="space-y-1">
        <h2 id="data-sources-heading" className="text-base font-semibold">
          Data sources and API keys
        </h2>
        <p className="text-sm text-muted-foreground">
          Price and FX providers Flowfolio fetches from. Keys are stored on your
          server and never shown in full.
        </p>
      </div>

      {keysQuery.isLoading ? (
        <div className="space-y-3">
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-20 w-full" />
        </div>
      ) : !data ? (
        <p className="text-sm text-muted-foreground">
          Couldn&apos;t load API keys. Refresh the page to try again.
        </p>
      ) : data.demo ? (
        <DemoNotice providers={data.providers} />
      ) : (
        <ul className="divide-y divide-border rounded-lg border">
          {data.providers.map((provider) => (
            <ProviderRow key={provider.id} provider={provider} />
          ))}
        </ul>
      )}
    </section>
  );
}

/** Demo read-only: a notice plus the bare provider list, no values or controls. */
function DemoNotice({ providers }: { providers: ProviderStatus[] }) {
  return (
    <div className="space-y-4">
      <p
        role="note"
        className="rounded-md border border-border bg-muted/40 px-3 py-2 text-sm text-muted-foreground"
      >
        API keys are disabled in demo mode. Run your own Flowfolio instance to
        connect live price and FX data.
      </p>
      <ul className="divide-y divide-border rounded-lg border">
        {providers.map((provider) => (
          <li key={provider.id} className="px-3 py-3">
            <div className="font-medium">{provider.label}</div>
            <p className="text-sm text-muted-foreground">{provider.blurb}</p>
          </li>
        ))}
      </ul>
    </div>
  );
}

function ProviderRow({ provider }: { provider: ProviderStatus }) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(""); // always starts empty, never seeded from masked
  const [error, setError] = useState<string | null>(null);

  const saveMutation = useMutation({
    mutationFn: (candidate: string) =>
      apiFetch<void>(`/api/keys/${provider.id}`, {
        method: "PUT",
        body: JSON.stringify({ value: candidate }),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["keys"] });
      setEditing(false);
      setValue("");
      setError(null);
    },
    onError: (err) => {
      // A 422 (failed test or required-empty) blocks the save: keep the input
      // open with no list change and surface the detail inline.
      setError(errorDetail(err));
    },
  });

  const clearMutation = useMutation({
    mutationFn: () =>
      apiFetch<void>(`/api/keys/${provider.id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["keys"] }),
  });

  const busy = saveMutation.isPending || clearMutation.isPending;

  function openEditor() {
    setValue(""); // the Replace input is never populated from the stored mask
    setError(null);
    setEditing(true);
  }

  function cancelEditor() {
    setEditing(false);
    setValue("");
    setError(null);
  }

  function submit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    saveMutation.mutate(value.trim());
  }

  return (
    <li className="space-y-2 px-3 py-3">
      <div className="flex items-start justify-between gap-3">
        <div className="space-y-0.5">
          <div className="flex items-center gap-2">
            <span className="font-medium">{provider.label}</span>
            {provider.optional ? (
              <span className="text-xs text-muted-foreground">(optional)</span>
            ) : null}
          </div>
          <p className="text-sm text-muted-foreground">
            {provider.blurb} {provider.free_tier}
          </p>
          <a
            href={provider.register_url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-sm text-muted-foreground underline hover:text-foreground"
          >
            Get a key
            <ExternalLink className="size-3.5" aria-hidden="true" />
          </a>
        </div>
        <span className="shrink-0 font-mono text-sm text-muted-foreground">
          {provider.configured && provider.masked ? provider.masked : "Not set"}
        </span>
      </div>

      {editing ? (
        <form onSubmit={submit} className="space-y-2">
          <div className="flex flex-wrap gap-2">
            <Input
              type="password"
              autoComplete="off"
              autoFocus
              placeholder={
                provider.optional
                  ? "Paste a key, or leave empty to clear"
                  : "Paste your API key"
              }
              value={value}
              onChange={(event) => setValue(event.target.value)}
              disabled={busy}
              className="min-w-48 flex-1"
            />
            <Button
              type="submit"
              disabled={busy}
              className="min-h-11 shrink-0 sm:min-h-9"
            >
              {saveMutation.isPending ? "Saving…" : "Save"}
            </Button>
            <Button
              type="button"
              variant="ghost"
              onClick={cancelEditor}
              disabled={busy}
              className="min-h-11 shrink-0 sm:min-h-9"
            >
              Cancel
            </Button>
          </div>
          {error ? (
            <p className="text-sm text-negative" role="alert">
              {error}
            </p>
          ) : null}
        </form>
      ) : (
        <div className="flex gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={openEditor}
            disabled={busy}
            className="min-h-11 sm:min-h-7"
          >
            {provider.configured ? "Replace" : "Add key"}
          </Button>
          {provider.configured ? (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => clearMutation.mutate()}
              disabled={busy}
              className="min-h-11 sm:min-h-7"
            >
              {clearMutation.isPending ? "Clearing…" : "Clear"}
            </Button>
          ) : null}
        </div>
      )}
    </li>
  );
}
