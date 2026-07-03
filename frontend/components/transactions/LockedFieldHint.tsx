import { Info } from "lucide-react";

import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";

/**
 * Hover hint for a locked dialog field (Type / Account / Instrument once a
 * transaction exists). Shared across the buy / spend / trade / yield forms so
 * the lock affordance is identical everywhere.
 *
 * Render it next to — never inside — a FormLabel: a <button> inside a <label>
 * is invalid HTML and would steal the label's click-to-focus. Pattern:
 *   <div className="flex items-center gap-1.5">
 *     <FormLabel>Account</FormLabel>
 *     {txnId && <LockedFieldHint text="Account can't be changed — …" />}
 *   </div>
 */
export function LockedFieldHint({ text }: { text: string }) {
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            type="button"
            tabIndex={-1}
            aria-label={text}
            className="text-muted-foreground transition-colors hover:text-foreground"
          >
            <Info className="size-3.5" aria-hidden />
          </button>
        </TooltipTrigger>
        <TooltipContent className="max-w-56">{text}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
