"use client";

import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";

/**
 * Mobile top app bar. Sibling of AppHeader (desktop). Mounted in (app)/layout.tsx.
 *
 * flex md:hidden, only rendered below 768px.
 * Brand left, centered page title from ROUTE_TITLES, no right cluster.
 */
const ROUTE_TITLES: ReadonlyArray<readonly [string, string]> = [
  ["/track", "Track"],
  ["/compare", "Compare"],
  ["/holdings/active", "Active"],
  ["/holdings/closed", "Closed"],
  ["/holdings/i/", "Instrument"],
  ["/holdings", "Holdings"],
  ["/instruments", "Instruments"],
  ["/activity", "Activity"],
  ["/settings", "Settings"],
  ["/reconcile", "Reconcile"],
];

function resolveTitle(pathname: string): string {
  // Longest-prefix-first by virtue of the array order:
  // /holdings/active matches before /holdings; /holdings/i/ matches before /holdings.
  for (const [prefix, title] of ROUTE_TITLES) {
    if (pathname === prefix || pathname.startsWith(prefix)) {
      return title;
    }
  }
  return "Flowfolio";
}

export function MobileAppBar() {
  const pathname = usePathname();
  const title = resolveTitle(pathname);

  return (
    <header className="sticky top-0 z-40 h-14 border-b border-border bg-background flex md:hidden">
      <div className="relative mx-auto flex h-full w-full max-w-7xl items-center px-4">
        <Link href="/track" className="shrink-0" aria-label="Flowfolio home">
          <Image
            src="/logo-mark.png"
            alt="Flowfolio"
            width={1060}
            height={503}
            priority
            className="h-6 w-auto"
          />
        </Link>
        <h1
          className="absolute left-1/2 -translate-x-1/2 text-sm font-medium text-foreground"
          aria-live="polite"
        >
          {title}
        </h1>
      </div>
    </header>
  );
}
