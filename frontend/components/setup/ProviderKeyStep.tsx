"use client";

import { useMutation } from "@tanstack/react-query";
import { ExternalLink } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ApiError, apiFetch } from "@/lib/api-client";

/** One provider the wizard walks the user through. Mirrors GET /api/keys rows. */
export interface WizardProvider {
  id: string;
  label: string;
  blurb: string;
  free_tier: string;
  register_url: string;
  optional: boolean;
}

interface ProviderKeyStepProps {
  provider: WizardProvider;
  stepIndex: number;
  total: number;
  onSaved: () => void;
  onSkip: () => void;
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
 * One first-run wizard step for a single provider. Shows
 * what the key enables, its free-tier limit, and a register link. Save is
 * test-then-persist (PUT /api/keys/{id}): a failed test (422) keeps the user on
 * the step with an inline error and never calls onSaved. The input always starts
 * empty and is never seeded from any stored value.
 */
export function ProviderKeyStep({
  provider,
  stepIndex,
  total,
  onSaved,
  onSkip,
}: ProviderKeyStepProps) {
  const [value, setValue] = useState(""); // always starts empty, never seeded
  const [error, setError] = useState<string | null>(null);

  const saveMutation = useMutation({
    mutationFn: (candidate: string) =>
      apiFetch<void>(`/api/keys/${provider.id}`, {
        method: "PUT",
        body: JSON.stringify({ value: candidate }),
      }),
    onSuccess: () => onSaved(),
    onError: (err) => {
      // A 422 (failed test or required-empty) keeps the user on the step.
      setError(errorDetail(err));
    },
  });

  const busy = saveMutation.isPending;

  function submit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    saveMutation.mutate(value.trim());
  }

  return (
    <div className="space-y-4">
      <p className="text-xs uppercase tracking-wide text-muted-foreground">
        Step {stepIndex + 1} of {total}
      </p>

      <div className="space-y-1">
        <h2 className="flex items-center gap-2 text-lg font-medium">
          {provider.label}
          {provider.optional ? (
            <span className="text-xs text-muted-foreground">(optional)</span>
          ) : null}
        </h2>
        <p className="text-sm text-muted-foreground">{provider.blurb}</p>
        <p className="text-sm text-muted-foreground">{provider.free_tier}</p>
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

      <form onSubmit={submit} className="space-y-3">
        <Input
          type="password"
          autoComplete="off"
          autoFocus
          placeholder={
            provider.optional
              ? "Paste a key, or leave empty to skip"
              : "Paste your API key"
          }
          value={value}
          onChange={(event) => setValue(event.target.value)}
          disabled={busy}
        />
        {error ? (
          <p className="text-sm text-negative" role="alert">
            {error}
          </p>
        ) : null}
        <div className="flex gap-2">
          <Button
            type="submit"
            disabled={busy}
            className="min-h-11 flex-1"
          >
            {saveMutation.isPending ? "Saving…" : "Save"}
          </Button>
          <Button
            type="button"
            variant="ghost"
            onClick={onSkip}
            disabled={busy}
            className="min-h-11 flex-1"
          >
            Skip
          </Button>
        </div>
      </form>
    </div>
  );
}
