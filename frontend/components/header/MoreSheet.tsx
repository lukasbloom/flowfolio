"use client";

import Link from "next/link";
import {
  Drawer,
  DrawerContent,
  DrawerHeader,
  DrawerTitle,
} from "@/components/ui/drawer";
import { TagFilterChip } from "@/components/header/TagFilterChip";
import { CurrencyChip } from "@/components/header/CurrencyChip";
import { LogoutButton } from "@/components/header/LogoutButton";
import { useMoreSheet } from "@/components/header/MoreSheetProvider";
import { navRoutesFor } from "@/lib/nav";

/**
 * Bottom-anchored vaul Drawer for mobile "More" navigation.
 *
 * vaul Drawer anchored to the viewport bottom (NOT shadcn Sheet, NOT a /more route).
 * Row order: Holdings link, Instruments link, Settings link,
 * TagFilterChip, CurrencyChip, LogoutButton. Nav-link rows derive
 * from navRoutesFor("more") so the order follows NAV_ROUTES. Nav rows
 * dismiss on tap.
 * Open state owned by MoreSheetContext. TagFilter/Currency providers
 * sit above this drawer so state survives dismount.
 */
export function MoreSheet() {
  const { open, setOpen } = useMoreSheet();
  return (
    <Drawer open={open} onOpenChange={setOpen} direction="bottom">
      <DrawerContent className="md:hidden">
        <DrawerHeader>
          <DrawerTitle>More</DrawerTitle>
        </DrawerHeader>
        <nav className="flex flex-col divide-y" aria-label="More navigation">
          {/* Order: Holdings, Instruments, Settings (from NAV_ROUTES surface "more"). */}
          {navRoutesFor("more").map((route) => (
            <Link
              key={route.href}
              href={route.href}
              onClick={() => setOpen(false)}
              className="flex min-h-12 items-center px-4 text-base"
            >
              {route.label}
            </Link>
          ))}
          <div className="flex min-h-12 items-center px-4">
            <TagFilterChip />
          </div>
          <div className="flex min-h-12 items-center px-4">
            <CurrencyChip />
          </div>
          <div className="flex min-h-12 items-center px-4 pt-2 pb-4">
            <LogoutButton />
          </div>
        </nav>
      </DrawerContent>
    </Drawer>
  );
}
