"use client";

import { useRouter, useSearchParams } from "next/navigation";

import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";

/**
 * URL-driven yield-visibility toggle, mirroring {@link ShowDeletedSwitch}.
 *
 * Default (off-param) state = yield rows are visible, matching the
 * convention used by `?deleted=1` for "show deleted". We only persist the
 * NON-default state (`hide_yield=1`) so a clean URL means "show everything",
 * which matches user expectation when landing on /activity for the first time.
 *
 * Filtering happens client-side post-fetch inside TxnList — the API query
 * cache key intentionally excludes this flag so toggling does NOT refetch.
 */
export function ShowYieldSwitch() {
  const router = useRouter();
  const search = useSearchParams();
  const hideYield = search.get("hide_yield") === "1";
  // Switch is ON when yield rows are visible (i.e. when hide_yield is NOT set),
  // so the affirmative "Show yield" label reads naturally.
  const checked = !hideYield;

  function onChange(next: boolean) {
    const params = new URLSearchParams(search.toString());
    if (next) {
      // User wants to show yield: drop the hide flag.
      params.delete("hide_yield");
    } else {
      params.set("hide_yield", "1");
    }
    router.replace(`?${params.toString()}`, { scroll: false });
  }

  return (
    <div className="flex items-center gap-2 min-h-11">
      <Switch
        id="show-yield"
        checked={checked}
        onCheckedChange={onChange}
        aria-label="Show yield"
      />
      <Label
        htmlFor="show-yield"
        className={
          checked
            ? "text-xs text-foreground font-medium"
            : "text-xs text-muted-foreground"
        }
      >
        Show yield
      </Label>
    </div>
  );
}
