"use client";

import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { AddButton } from "@/components/header/AddButton";
import { CurrencyChip } from "@/components/header/CurrencyChip";
import { TagFilterChip } from "@/components/header/TagFilterChip";
import { UserMenu } from "@/components/header/UserMenu";
import { Separator } from "@/components/ui/separator";
import { isRouteActive, navRoutesFor } from "@/lib/nav";
import { cn } from "@/lib/utils";

// Settings was previously sixth in primary nav; it now lives in the
// UserMenu dropdown on the right. Primary destinations come from the shared
// NAV_ROUTES table (surface "header"): Track, Compare, Holdings, Activity.
const NAV_LINKS = navRoutesFor("header");

export function AppHeader() {
  const pathname = usePathname();

  return (
    <header className="sticky top-0 z-40 h-14 border-b border-border bg-background hidden md:flex">
      {/* Flex layout: the nav is flex-1 + justify-center, so it centers in the
          space BETWEEN the brand and the controls (equal whitespace on each
          side of the menu). Geometric centering in the full header looked
          right-heavy because the brand is much narrower than the controls
          cluster, leaving a large gap on the left. */}
      <div className="mx-auto flex h-full w-full max-w-7xl items-center gap-6 px-4 md:px-6">
        {/* Zone 1 — brand */}
        <Link
          href="/track"
          className="flex items-center gap-2"
        >
          <Image
            src="/logo-mark.png"
            alt=""
            width={1060}
            height={503}
            priority
            className="h-6 w-auto"
          />
          <span className="text-base font-semibold tracking-tight">Flowfolio</span>
        </Link>

        {/* Zone 2 — primary destinations */}
        <nav
          aria-label="Primary"
          className="flex flex-1 items-center justify-center gap-6 text-sm"
        >
          {NAV_LINKS.map((link) => {
            const active = isRouteActive(pathname, link.href);
            const Icon = link.icon;
            return (
              <Link
                key={link.href}
                href={link.href}
                className={cn(
                  "h-14 inline-flex items-center gap-1.5 border-b-2 transition-colors",
                  active
                    ? "text-foreground border-foreground"
                    : "text-muted-foreground border-transparent hover:text-foreground"
                )}
              >
                {Icon && <Icon className="size-4" aria-hidden="true" />}
                {link.label}
              </Link>
            );
          })}
        </nav>

        {/* Zone 3 — primary action, then a vertical separator into the
            global-state cluster (tag, currency, account menu). The separator
            visually splits "do something" (Add) from "global state /
            identity" without needing a background tint that would nest pills
            inside another pill. */}
        <div className="flex items-center gap-3">
          <AddButton />
          <Separator
            orientation="vertical"
            className="data-[orientation=vertical]:h-6"
          />
          <TagFilterChip />
          <CurrencyChip />
          <UserMenu />
        </div>
      </div>
    </header>
  );
}
