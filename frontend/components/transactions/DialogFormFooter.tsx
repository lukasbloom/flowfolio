import { type ReactNode } from "react";

import { cn } from "@/lib/utils";

/**
 * Shared footer band for the transaction dialog forms (buy / spend / trade /
 * yield edit + create dialogs) so they all look identical.
 *
 * Renders a full-bleed muted band with a top rule and right-aligned buttons
 * (primary action last) — the shadcn dialog-footer flex convention plus the
 * project's banded treatment. The negative margins assume the form sits inside
 * the EditTxnDialog body wrapper: on mobile (Drawer) that wrapper supplies the
 * px-4/pb-4 padding, which we cancel with -mx-4/-mb-4; on desktop the Dialog's
 * p-6 is the only padding layer (the wrapper zeroes its own on md+), so
 * md:-mx-6 / md:-mb-6 reach the card edge and md:rounded-b-lg matches its corners.
 *
 * Pass `md:col-span-2` (or similar) via className when the form is a CSS grid so
 * the footer spans every column.
 */
export function DialogFormFooter({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "-mx-4 -mb-4 mt-2 flex flex-col-reverse gap-2 border-t bg-muted/50 px-4 pt-4 pb-4 sm:flex-row sm:justify-end md:-mx-6 md:-mb-6 md:px-6 md:pb-6 md:rounded-b-lg",
        className,
      )}
    >
      {children}
    </div>
  );
}
