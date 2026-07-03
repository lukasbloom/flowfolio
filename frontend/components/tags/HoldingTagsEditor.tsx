"use client";

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, Plus } from "lucide-react";
import { toast } from "sonner";

import { ApiError, apiFetch } from "@/lib/api-client";
import { invalidatePortfolioCache } from "@/lib/invalidate-cache";
import { useTagsManager } from "@/components/tags/TagsManagerProvider";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Separator } from "@/components/ui/separator";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";

interface Tag {
  id: string;
  name: string;
  holdings_count?: number;
}

interface HoldingTagsEditorProps {
  instrumentId: string;
  instrumentSymbol: string;
  accountId: string;
  accountName: string;
  attachedTags: Tag[];
  allTags: Tag[];
}

export function HoldingTagsEditor({
  instrumentId,
  instrumentSymbol,
  accountId,
  accountName,
  attachedTags,
  allTags,
}: HoldingTagsEditorProps) {
  const queryClient = useQueryClient();
  const { openManager } = useTagsManager();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");

  // 9-key invalidation contract, repeated inline in each mutation's onSuccess
  // so each queryKey is explicit per mutation.
  const attachMutation = useMutation({
    mutationFn: (tagId: string) =>
      apiFetch<void>(`/api/holdings/${accountId}/${instrumentId}/tags`, {
        method: "POST",
        body: JSON.stringify({ tag_id: tagId }),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["tags"] });
      queryClient.invalidateQueries({ queryKey: ["instrument-holdings", instrumentId] });
      invalidatePortfolioCache(queryClient);
    },
    onError: (err: Error) => {
      if (err instanceof ApiError && err.status === 422) {
        toast.error("Could not attach tag. Holding or tag was removed.", {
          duration: 6000,
        });
        return;
      }
      toast.error(`Could not attach tag. ${err.message}.`, { duration: 6000 });
    },
  });

  const detachMutation = useMutation({
    mutationFn: (tagId: string) =>
      apiFetch<void>(
        `/api/holdings/${accountId}/${instrumentId}/tags/${tagId}`,
        { method: "DELETE" }
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["tags"] });
      queryClient.invalidateQueries({ queryKey: ["instrument-holdings", instrumentId] });
      invalidatePortfolioCache(queryClient);
    },
    onError: (err: Error) => {
      toast.error(`Could not remove tag. ${err.message}.`, { duration: 6000 });
    },
  });

  const inlineCreateAttachMutation = useMutation({
    mutationFn: async (name: string) => {
      // Step 1: create the tag.
      const newTag = await apiFetch<Tag>("/api/tags", {
        method: "POST",
        body: JSON.stringify({ name }),
      });
      // Step 2: attach to this holding.
      await apiFetch<void>(
        `/api/holdings/${accountId}/${instrumentId}/tags`,
        {
          method: "POST",
          body: JSON.stringify({ tag_id: newTag.id }),
        }
      );
      return newTag;
    },
    onSuccess: (newTag) => {
      setQuery("");
      queryClient.invalidateQueries({ queryKey: ["tags"] });
      queryClient.invalidateQueries({ queryKey: ["instrument-holdings", instrumentId] });
      invalidatePortfolioCache(queryClient);
      toast.success(`Tag "${newTag.name}" created and applied.`);
    },
    onError: (err: Error) => {
      // Step 1 may fail with 409 (race: another tab created the tag).
      // Refresh ["tags"] so the user can click the now-existing suggestion row.
      if (err instanceof ApiError && err.status === 409) {
        toast.error(
          `A tag named "${query.trim()}" already exists. Click it in the list above to attach.`,
          { duration: 6000 }
        );
        queryClient.invalidateQueries({ queryKey: ["tags"] });
        return;
      }
      toast.error(`Could not create or attach tag. ${err.message}.`, {
        duration: 6000,
      });
    },
  });

  const attachedIds = new Set(attachedTags.map((t) => t.id));
  const queryTrimmed = query.trim();
  const filtered =
    queryTrimmed.length === 0
      ? allTags
      : allTags.filter((t) =>
          t.name.toLowerCase().includes(queryTrimmed.toLowerCase())
        );
  const exactMatch = allTags.find((t) => t.name === queryTrimmed);
  const showInlineCreate = queryTrimmed.length >= 1 && !exactMatch;
  const canSubmitInlineCreate =
    showInlineCreate && queryTrimmed.length <= 64;

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          size="sm"
          className="min-h-11"
          aria-haspopup="dialog"
        >
          <Plus className="size-3.5" aria-hidden />
          Add tag
        </Button>
      </PopoverTrigger>
      <PopoverContent
        side="bottom"
        align="start"
        collisionPadding={8}
        className="w-72 p-0"
      >
        {/* Header */}
        <div className="border-b border-border p-3">
          <p className="text-base font-semibold">
            {`Tags for ${instrumentSymbol} in ${accountName}`}
          </p>
        </div>

        {/* Search input */}
        <div className="p-3">
          <Input
            autoFocus
            placeholder="Search or create…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            maxLength={64}
            aria-label="Search or create tag"
            onKeyDown={(e) => {
              if (e.key === "Enter" && canSubmitInlineCreate) {
                e.preventDefault();
                inlineCreateAttachMutation.mutate(queryTrimmed);
              }
            }}
          />
        </div>

        {/* Suggestion list (or zero-tags-anywhere caption) */}
        <div className="max-h-72 overflow-y-auto px-1 pb-1">
          {allTags.length === 0 && queryTrimmed.length === 0 ? (
            <p className="px-2 py-3 text-xs text-muted-foreground">
              No tags yet. Type a name and press Enter to create.
            </p>
          ) : (
            <>
              {filtered.map((t) => {
                const isAttached = attachedIds.has(t.id);
                return (
                  <button
                    key={t.id}
                    type="button"
                    onClick={() =>
                      isAttached
                        ? detachMutation.mutate(t.id)
                        : attachMutation.mutate(t.id)
                    }
                    className="flex min-h-11 w-full items-center justify-between rounded px-2 py-2 text-left text-sm hover:bg-muted"
                  >
                    <span>{t.name}</span>
                    {isAttached && (
                      <Check
                        className="size-3.5 text-foreground"
                        aria-hidden
                      />
                    )}
                  </button>
                );
              })}
              {showInlineCreate && (
                <button
                  type="button"
                  disabled={!canSubmitInlineCreate}
                  onClick={() =>
                    inlineCreateAttachMutation.mutate(queryTrimmed)
                  }
                  className="flex min-h-11 w-full items-center gap-2 rounded px-2 py-2 text-left text-sm hover:bg-muted disabled:opacity-50"
                >
                  <Plus
                    className="size-3.5 text-muted-foreground"
                    aria-hidden
                  />
                  <span>{`Press Enter to create "${queryTrimmed}"`}</span>
                </button>
              )}
            </>
          )}
        </div>

        {/* Footer: Manage all tags… link */}
        <Separator />
        <div className="p-3">
          <button
            type="button"
            onClick={() => {
              setOpen(false);
              openManager();
            }}
            className="text-sm text-muted-foreground hover:text-foreground"
          >
            Manage all tags…
          </button>
        </div>
      </PopoverContent>
    </Popover>
  );
}
