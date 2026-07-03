"use client";

import { useRouter, useSearchParams } from "next/navigation";

import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";

export function ShowDeletedSwitch() {
  const router = useRouter();
  const search = useSearchParams();
  const checked = search.get("deleted") === "1";

  function onChange(next: boolean) {
    const params = new URLSearchParams(search.toString());
    if (next) {
      params.set("deleted", "1");
    } else {
      params.delete("deleted");
    }
    router.replace(`?${params.toString()}`, { scroll: false });
  }

  return (
    <div className="flex items-center gap-2 min-h-11">
      <Switch
        id="show-deleted"
        checked={checked}
        onCheckedChange={onChange}
        aria-label="Show deleted"
      />
      <Label htmlFor="show-deleted" className={checked ? "text-xs text-foreground font-medium" : "text-xs text-muted-foreground"}>
        Show deleted
      </Label>
    </div>
  );
}
