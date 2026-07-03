"use client";

import { useEffect, useRef } from "react";
import { Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useAddTxn } from "@/components/transactions/AddTxnProvider";
import { useMoreSheet } from "@/components/header/MoreSheetProvider";
import { useMediaQuery } from "@/lib/use-media-query";

/**
 * Mobile floating action button for "Add transaction".
 *
 * Fixed right-4 with bottom-safe-offset-tab utility; size-14 (56px).
 * Returns null while MoreSheet is open (iOS/Slack idiom, avoids the
 * FAB floating over the drawer top edge).
 * DOM id is add-trigger-fab (distinct from desktop AddButton's add-trigger),
 * registers with AddTxnProvider on mount so Picker/FormSheet onCloseAutoFocus
 * returns focus here.
 *
 * Mount gate: useMediaQuery ensures this component does NOT mount at ≥768px.
 * Using CSS `md:hidden` alone leaves the FAB in the DOM at desktop, and its
 * useEffect would still call registerTrigger, overwriting AddButton's registration
 * with a display:none target — breaking the desktop focus-return contract.
 */
export function AddTxnFab() {
  const ref = useRef<HTMLButtonElement | null>(null);
  const { openTypePicker, registerTrigger } = useAddTxn();
  const { open: moreOpen } = useMoreSheet();
  const isDesktop = useMediaQuery("(min-width: 768px)");

  useEffect(() => {
    if (isDesktop || moreOpen) return;
    registerTrigger(ref.current);
    return () => registerTrigger(null);
  }, [registerTrigger, isDesktop, moreOpen]);

  if (isDesktop || moreOpen) {
    return null;
  }

  return (
    <Button
      ref={ref}
      id="add-trigger-fab"
      type="button"
      variant="default"
      onClick={openTypePicker}
      aria-label="Add transaction"
      className="fixed right-4 z-40 bottom-safe-offset-tab size-14 rounded-full p-0 shadow-lg"
    >
      <Plus className="size-6" aria-hidden />
    </Button>
  );
}
