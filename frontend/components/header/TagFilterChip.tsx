"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronDown, Settings2 } from "lucide-react";

import { apiFetch } from "@/lib/api-client";
import { useTagFilter } from "@/lib/tag-filter";
import { useTagsManager } from "@/components/tags/TagsManagerProvider";
import { cn } from "@/lib/utils";
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
} from "@/components/ui/dropdown-menu";

interface Tag { id: string; name: string; holdings_count?: number }
interface TagsResponse { tags: Tag[] }

export function TagFilterChip() {
  const [open, setOpen] = useState(false);
  const { tagFilter, setTagFilter } = useTagFilter();
  const { openManager } = useTagsManager();
  const { data } = useQuery<TagsResponse>({
    queryKey: ["tags"],
    queryFn: () => apiFetch<TagsResponse>("/api/tags"),
  });
  const tags = data?.tags ?? [];

  return (
    <DropdownMenu open={open} onOpenChange={setOpen}>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          aria-haspopup="menu"
          aria-expanded={open}
          aria-label="Filter by tag"
          className={cn(
            "inline-flex h-9 items-center gap-2 rounded-full border border-border bg-card px-3 text-sm",
            tagFilter === null ? "text-muted-foreground" : "text-foreground"
          )}
        >
          {tagFilter !== null && <span className="size-2 rounded-full bg-foreground" aria-hidden />}
          <span>{tagFilter ?? "All tags"}</span>
          <ChevronDown className="size-3" aria-hidden />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuItem onClick={() => setTagFilter(null)}>All tags</DropdownMenuItem>
        {tags
          .slice()
          .sort((a, b) => a.name.localeCompare(b.name))
          .map((t) => (
            <DropdownMenuItem key={t.id} onClick={() => setTagFilter(t.name)}>
              {t.name}
            </DropdownMenuItem>
          ))}
        <DropdownMenuSeparator />
        <DropdownMenuItem onClick={() => openManager()} className="min-h-11">
          <Settings2 className="size-3.5 text-muted-foreground" aria-hidden />
          <span>Manage tags…</span>
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
