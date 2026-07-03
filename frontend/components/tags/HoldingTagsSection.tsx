"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { apiFetch } from "@/lib/api-client";
import { invalidatePortfolioCache } from "@/lib/invalidate-cache";
import { Skeleton } from "@/components/ui/skeleton";
import { HoldingTagsEditor } from "@/components/tags/HoldingTagsEditor";
import { TagChip } from "@/components/tags/TagChip";

interface Tag {
  id: string;
  name: string;
  holdings_count?: number;
}
interface InstrumentHolding {
  account_id: string;
  account_name: string;
  tags: Tag[];
}
interface InstrumentResponse {
  id: string;
  symbol: string;
  name: string;
}
interface TagsResponse {
  tags: Tag[];
}

// Internal hook that keeps the chip-X detach symmetric with HoldingTagsEditor's
// detach (same 9-key invalidation contract). Lives here so HoldingTagsSection owns the
// chip-row UX, while HoldingTagsEditor owns its own detach mutation independently.
function useDetachTag() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      accountId,
      instrumentId,
      tagId,
    }: {
      accountId: string;
      instrumentId: string;
      tagId: string;
    }) =>
      apiFetch<void>(
        `/api/holdings/${accountId}/${instrumentId}/tags/${tagId}`,
        { method: "DELETE" }
      ),
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({ queryKey: ["tags"] });
      queryClient.invalidateQueries({
        queryKey: ["instrument-holdings", variables.instrumentId],
      });
      invalidatePortfolioCache(queryClient);
    },
    onError: (err: Error) => {
      toast.error(`Could not remove tag. ${err.message}.`, { duration: 6000 });
    },
  });
}

export function HoldingTagsSection({ instrumentId }: { instrumentId: string }) {
  // Query 1: holdings + per-pair attached tags.
  const { data: holdings, isLoading: holdingsLoading } = useQuery<
    InstrumentHolding[]
  >({
    queryKey: ["instrument-holdings", instrumentId],
    queryFn: () =>
      apiFetch<InstrumentHolding[]>(
        `/api/instruments/${instrumentId}/holdings`
      ),
  });
  // Query 2: master tag list for the suggestion list inside each editor.
  const { data: tagsData } = useQuery<TagsResponse>({
    queryKey: ["tags"],
    queryFn: () => apiFetch<TagsResponse>("/api/tags"),
  });
  // Query 3: instrument metadata (for the symbol used in the popover header
  // "Tags for {symbol} in {account_name}"). Reuses the same query key as
  // OverviewTab so the cache is shared (no duplicate request).
  const { data: instrument } = useQuery<InstrumentResponse>({
    queryKey: ["instrument", instrumentId],
    queryFn: () =>
      apiFetch<InstrumentResponse>(`/api/instruments/${instrumentId}`),
  });

  const detachMutation = useDetachTag();

  const allTags = tagsData?.tags ?? [];
  const symbol = instrument?.symbol ?? "";
  const pairs = holdings ?? [];

  // Shed the section's own card chrome + heading +
  // description so it renders cleanly inside the OverviewTab tags popover.
  // The popover trigger ("Tags (N)") titles the surface, a redundant <h2>
  // would re-introduce the heading the popover button is replacing.
  return (
    <div className="space-y-3">
      {holdingsLoading ? (
        <Skeleton className="h-20 w-full" />
      ) : pairs.length === 0 ? (
        <p className="text-xs text-muted-foreground">
          {"You don't currently hold this instrument in any account. Tags attach to a (account, instrument) pair and appear once you record a transaction for it."}
        </p>
      ) : (
        <div className="space-y-4">
          {pairs.map((pair) => (
            <div key={pair.account_id}>
              <p className="text-xs text-muted-foreground">
                {`Held in: ${pair.account_name}`}
              </p>
              <div className="mt-2 flex min-h-11 flex-wrap items-center gap-2">
                {pair.tags.map((tag) => (
                  <TagChip
                    key={tag.id}
                    name={tag.name}
                    removable
                    contextSymbol={symbol}
                    contextAccountName={pair.account_name}
                    onRemove={() =>
                      detachMutation.mutate({
                        accountId: pair.account_id,
                        instrumentId,
                        tagId: tag.id,
                      })
                    }
                  />
                ))}
                <HoldingTagsEditor
                  instrumentId={instrumentId}
                  instrumentSymbol={symbol}
                  accountId={pair.account_id}
                  accountName={pair.account_name}
                  attachedTags={pair.tags}
                  allTags={allTags}
                />
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
