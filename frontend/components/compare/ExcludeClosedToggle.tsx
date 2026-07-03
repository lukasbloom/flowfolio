"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Switch } from "@/components/ui/switch";

export function ExcludeClosedToggle() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const checked = searchParams.get("excludeClosed") === "1";

  function toggle(next: boolean) {
    const params = new URLSearchParams(searchParams);
    if (next) {
      params.set("excludeClosed", "1");
    } else {
      params.delete("excludeClosed");
    }
    const qs = params.toString();
    router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
  }

  return (
    <label className="flex items-center gap-2 text-sm">
      <Switch
        checked={checked}
        onCheckedChange={toggle}
        aria-label="Exclude closed positions"
      />
      <span>Exclude closed</span>
    </label>
  );
}
