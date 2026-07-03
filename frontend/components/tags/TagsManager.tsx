"use client";

import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { ApiError, apiFetch } from "@/lib/api-client";
import { useTagFilter } from "@/lib/tag-filter";
import { useTagsManager } from "@/components/tags/TagsManagerProvider";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Separator } from "@/components/ui/separator";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

interface Tag {
  id: string;
  name: string;
  holdings_count: number;
}
interface TagsResponse {
  tags: Tag[];
}

function DeleteTagConfirm({ tag, onClose }: { tag: Tag; onClose: () => void }) {
  const queryClient = useQueryClient();
  const { tagFilter, setTagFilter } = useTagFilter();

  const cascadeBody =
    tag.holdings_count > 0
      ? `This will detach it from ${tag.holdings_count} holdings. Tag deletions cannot be undone.`
      : "This will detach it from any holdings it is applied to. Tag deletions cannot be undone.";

  const mutation = useMutation({
    mutationFn: () =>
      apiFetch<void>(`/api/tags/${tag.id}`, { method: "DELETE" }),
    onSuccess: () => {
      // If the deleted tag is the active filter, clear it BEFORE invalidating
      // (so refetches go out without the now-orphan tag name).
      if (tagFilter === tag.name) {
        setTagFilter(null);
      }
      // 9-key invalidation contract:
      queryClient.invalidateQueries({ queryKey: ["tags"] });
      queryClient.invalidateQueries({ queryKey: ["instrument-holdings"] });
      queryClient.invalidateQueries({ queryKey: ["perf"] });
      queryClient.invalidateQueries({ queryKey: ["networth"] });
      queryClient.invalidateQueries({ queryKey: ["allocation"] });
      queryClient.invalidateQueries({ queryKey: ["contributions-bars"] });
      queryClient.invalidateQueries({ queryKey: ["contributions-overlay"] });
      queryClient.invalidateQueries({ queryKey: ["closed"] });
      queryClient.invalidateQueries({ queryKey: ["realized"] });
      toast.success(`Tag "${tag.name}" deleted.`);
      onClose();
    },
    onError: (err: Error) => {
      // 404 → tag was already removed (treat as success).
      // apiFetch throws ApiError with a numeric .status field — use that, not Response.
      if (err instanceof ApiError && err.status === 404) {
        if (tagFilter === tag.name) {
          setTagFilter(null);
        }
        queryClient.invalidateQueries({ queryKey: ["tags"] });
        toast(`Tag "${tag.name}" was already removed.`);
        onClose();
        return;
      }
      toast.error(`Could not delete tag. ${err.message}.`, { duration: 6000 });
    },
  });

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>{`Delete tag "${tag.name}"?`}</DialogTitle>
          <DialogDescription>{cascadeBody}</DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="destructive"
            onClick={() => mutation.mutate()}
            disabled={mutation.isPending}
          >
            Delete tag
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function TagsManager() {
  const { open, closeManager } = useTagsManager();
  const queryClient = useQueryClient();
  const { data } = useQuery<TagsResponse>({
    queryKey: ["tags"],
    queryFn: () => apiFetch<TagsResponse>("/api/tags"),
  });
  const tags = (data?.tags ?? [])
    .slice()
    .sort((a, b) => a.name.localeCompare(b.name));

  const [name, setName] = useState("");
  const [validationError, setValidationError] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<Tag | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const createMutation = useMutation({
    mutationFn: (body: { name: string }) =>
      apiFetch<Tag>("/api/tags", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: (newTag) => {
      setName("");
      setValidationError(null);
      queryClient.invalidateQueries({ queryKey: ["tags"] });
      toast.success(`Tag "${newTag.name}" created.`);
      inputRef.current?.focus();
    },
    onError: (err: Error) => {
      // apiFetch throws ApiError with a numeric .status field — use that, not Response.
      if (err instanceof ApiError) {
        if (err.status === 409) {
          setValidationError(`A tag named "${name.trim()}" already exists.`);
          return;
        }
        if (err.status === 422) {
          setValidationError("Enter a name between 1 and 64 characters.");
          return;
        }
      }
      // network / 5xx
      toast.error(`Could not create tag. ${err.message}.`, { duration: 6000 });
    },
  });

  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = name.trim();
    if (trimmed.length === 0 || trimmed.length > 64) {
      setValidationError("Enter a name between 1 and 64 characters.");
      return;
    }
    setValidationError(null);
    createMutation.mutate({ name: trimmed });
  }

  return (
    <>
      <Dialog open={open} onOpenChange={(o) => !o && closeManager()}>
        <DialogContent className="flex max-h-[80vh] max-w-md flex-col">
          <DialogHeader>
            <DialogTitle>Manage tags</DialogTitle>
            <DialogDescription>
              Tags filter every dashboard when applied from the header chip.
            </DialogDescription>
          </DialogHeader>

          {/* Create form */}
          <form onSubmit={onSubmit} className="flex items-start gap-2">
            <Input
              ref={inputRef}
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Add a new tag…"
              autoFocus
              maxLength={64}
              aria-label="New tag name"
            />
            <Button
              type="submit"
              size="sm"
              disabled={createMutation.isPending}
              className="shrink-0"
            >
              <Plus className="size-3.5" />
              Create tag
            </Button>
          </form>
          {validationError !== null ? (
            <p className="mt-1 text-xs text-destructive">{validationError}</p>
          ) : (
            <p className="mt-1 text-xs text-muted-foreground">
              Tag names are 1–64 characters.
            </p>
          )}

          <Separator className="my-4" />

          {/* List or empty state */}
          {tags.length === 0 ? (
            <p className="py-6 text-center text-xs text-muted-foreground">
              No tags yet. Create one above to start tagging holdings.
            </p>
          ) : (
            <ul role="list" className="flex-1 overflow-y-auto">
              {tags.map((tag) => (
                <li
                  key={tag.id}
                  className="flex min-h-11 items-center justify-between py-2"
                >
                  <span className="text-sm">{tag.name}</span>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="min-h-11 min-w-11"
                    aria-label={`Delete tag ${tag.name}`}
                    onClick={() => setConfirmDelete(tag)}
                  >
                    <Trash2 className="size-4 text-muted-foreground hover:text-destructive" />
                  </Button>
                </li>
              ))}
            </ul>
          )}

          <DialogFooter>
            <Button variant="outline" onClick={closeManager}>
              Close
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {confirmDelete !== null && (
        <DeleteTagConfirm
          tag={confirmDelete}
          onClose={() => setConfirmDelete(null)}
        />
      )}
    </>
  );
}
