"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { MoreHorizontal } from "lucide-react";
import { cn } from "@/lib/utils";
import { useMoreSheet } from "@/components/header/MoreSheetProvider";
import { isRouteActive, navRoutesFor } from "@/lib/nav";

// Mobile tab destinations come from the shared NAV_ROUTES table (surface
// "tabbar"): Track, Compare, Activity — Holdings is intentionally absent (it
// lives behind the More sheet). key + icon travel with each tabbar route.
const ROUTE_TABS = navRoutesFor("tabbar");

function isMoreActive(pathname: string, moreOpen: boolean) {
  if (moreOpen) return true;
  const knownTabbed = ["/track", "/compare", "/activity", "/login"];
  return !knownTabbed.some(
    (route) => pathname === route || pathname.startsWith(`${route}/`),
  );
}

export function BottomTabBar() {
  const pathname = usePathname() ?? "/";
  const { open: moreOpen, setOpen: setMoreOpen } = useMoreSheet();

  return (
    <nav
      aria-label="Bottom navigation"
      className="fixed bottom-0 inset-x-0 z-30 h-16 pb-safe border-t bg-background md:hidden"
    >
      <ul className="flex h-full items-stretch justify-around">
        {ROUTE_TABS.map((tab) => {
          const active = isRouteActive(pathname, tab.href);
          // tabbar routes always carry an icon + key in NAV_ROUTES; the
          // optional types are narrowed here for the (impossible) absent case.
          const Icon = tab.icon;
          if (!Icon) return null;
          return (
            <li key={tab.key ?? tab.href} className="flex-1">
              {/* tap-active-tab is a no-op (Next.js same-path push is a no-op). */}
              <Link
                href={tab.href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "flex h-full min-h-11 min-w-11 flex-col items-center justify-center gap-0.5 text-xs",
                  active
                    ? "font-semibold text-foreground"
                    : "font-normal text-muted-foreground",
                )}
              >
                <span
                  className={cn(
                    "flex items-center justify-center",
                    active && "rounded-full bg-foreground/10 p-1.5",
                  )}
                >
                  <Icon className="size-5" aria-hidden />
                </span>
                <span>{tab.label}</span>
              </Link>
            </li>
          );
        })}
        {(() => {
          const active = isMoreActive(pathname, moreOpen);
          return (
            <li key="more" className="flex-1">
              {/* tap-active-tab is a no-op (setMoreOpen(true) when already open is idempotent). */}
              <button
                type="button"
                onClick={() => setMoreOpen(true)}
                aria-current={active ? "page" : undefined}
                aria-expanded={moreOpen}
                className={cn(
                  "flex h-full w-full min-h-11 min-w-11 flex-col items-center justify-center gap-0.5 text-xs",
                  active
                    ? "font-semibold text-foreground"
                    : "font-normal text-muted-foreground",
                )}
              >
                <span
                  className={cn(
                    "flex items-center justify-center",
                    active && "rounded-full bg-foreground/10 p-1.5",
                  )}
                >
                  <MoreHorizontal className="size-5" aria-hidden />
                </span>
                <span>More</span>
              </button>
            </li>
          );
        })()}
      </ul>
    </nav>
  );
}
