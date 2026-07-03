"use client";

import { useEffect, useRef } from "react";
import { Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useAddTxn } from "@/components/transactions/AddTxnProvider";
import { useMediaQuery } from "@/lib/use-media-query";

export function AddButton() {
  const { openTypePicker, registerTrigger } = useAddTxn();
  // Register as the provider-owned focus-return target. Picker/FormSheet
  // onCloseAutoFocus reads triggerRef.current to refocus. The DOM id="add-trigger"
  // is the stable e2e selector hook.
  //
  // Gate registration by isDesktop so the FAB owns triggerRef on mobile and this
  // button owns it on desktop. Cleanup nulls the slot on viewport transitions to
  // guarantee the other consumer's mount-effect can claim it without race.
  const ref = useRef<HTMLButtonElement>(null);
  const isDesktop = useMediaQuery("(min-width: 768px)");
  useEffect(() => {
    if (!isDesktop) return;
    registerTrigger(ref.current);
    return () => registerTrigger(null);
  }, [registerTrigger, isDesktop]);
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          ref={ref}
          id="add-trigger"
          variant="default"
          onClick={openTypePicker}
          aria-label="Add transaction"
        >
          <Plus className="size-4" aria-hidden />
          Add
        </Button>
      </TooltipTrigger>
      <TooltipContent>Add a new transaction</TooltipContent>
    </Tooltip>
  );
}
