"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";

const TABS = [
  { value: "active", href: "/holdings/active", label: "Active" },
  { value: "closed", href: "/holdings/closed", label: "Closed" },
] as const;

export function HoldingsTabs() {
  const pathname = usePathname();

  // Defense in depth: if a future change removes the layout guard, or if HoldingsTabs is
  // ever mounted directly by another caller, this strip MUST NOT render on /holdings/i/*.
  // No sub-tab pill appears on instrument-detail. The primary
  // suppression is the layout guard (which prevents this component from being mounted at
  // all on /holdings/i/*); this guard is a no-op safety net for any future regression.
  if (pathname.startsWith("/holdings/i/")) {
    return null;
  }

  // The defensive 'active' default below is no longer load-bearing for /holdings/i/* (the
  // two guards above prevent the strip from rendering there entirely). It IS retained as a
  // third safety net + as the documented behaviour for any unexpected pathname under
  // /holdings (e.g., a future /holdings/foo route would default to highlighting Active
  // rather than crashing or rendering an unselected strip). Keeping it costs nothing.
  const value =
    pathname === "/holdings" || pathname === "/holdings/active"
      ? "active"
      : pathname === "/holdings/closed"
        ? "closed"
        : "active";

  return (
    <Tabs value={value}>
      <TabsList>
        {TABS.map((t) => (
          <TabsTrigger key={t.value} value={t.value} asChild>
            <Link href={t.href}>{t.label}</Link>
          </TabsTrigger>
        ))}
      </TabsList>
    </Tabs>
  );
}
