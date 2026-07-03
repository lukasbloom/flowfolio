"use client";

import type { ReactNode } from "react";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Drawer,
  DrawerContent,
  DrawerDescription,
  DrawerHeader,
  DrawerTitle,
} from "@/components/ui/drawer";
import { useMediaQuery } from "@/lib/use-media-query";

/**
 * Shared responsive Dialog (≥768px) / Drawer (<768px) shell.
 *
 * Extracted from the hand-written branches in AddTxnPicker, AddTxnFormSheet,
 * and EditTxnDialog. PRESERVES EXACTLY:
 *   - breakpoint `(min-width: 768px)`, via useMediaQuery's
 *     useSyncExternalStore so the first client render picks the right branch;
 *   - per-call-site content classes (Dialog vs Drawer differ — passed via
 *     `dialogClassName` / `drawerClassName`);
 *   - `onCloseAutoFocus` forwarding (callers own focus-restore to their trigger);
 *   - optional `aria-describedby` (EditTxnDialog wires it to its chip group).
 *
 * The companion Header/Title/Description components render the correct primitive
 * for the active breakpoint, replacing the per-site
 * HeaderWrapper/TitleWrapper/DescriptionWrapper aliases.
 *
 * The breakpoint is exposed on context so the Header/Title/Description children
 * resolve to the same Dialog-vs-Drawer choice the shell made.
 */

export const RESPONSIVE_DIALOG_BREAKPOINT = "(min-width: 768px)";

export function useResponsiveDialogDesktop(): boolean {
  return useMediaQuery(RESPONSIVE_DIALOG_BREAKPOINT);
}

interface ResponsiveDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Content className applied to the Dialog (≥768px) branch. */
  dialogClassName?: string;
  /** Content className applied to the Drawer (<768px) branch. */
  drawerClassName?: string;
  onCloseAutoFocus?: (e: Event) => void;
  "aria-describedby"?: string;
  children: ReactNode;
}

export function ResponsiveDialog({
  open,
  onOpenChange,
  dialogClassName,
  drawerClassName,
  onCloseAutoFocus,
  "aria-describedby": ariaDescribedBy,
  children,
}: ResponsiveDialogProps) {
  const isDesktop = useResponsiveDialogDesktop();

  if (isDesktop) {
    return (
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent
          className={dialogClassName}
          aria-describedby={ariaDescribedBy}
          onCloseAutoFocus={onCloseAutoFocus}
        >
          {children}
        </DialogContent>
      </Dialog>
    );
  }

  return (
    <Drawer open={open} onOpenChange={onOpenChange}>
      <DrawerContent
        className={drawerClassName}
        aria-describedby={ariaDescribedBy}
        onCloseAutoFocus={onCloseAutoFocus}
      >
        {children}
      </DrawerContent>
    </Drawer>
  );
}

export function ResponsiveDialogHeader({ children }: { children: ReactNode }) {
  const isDesktop = useResponsiveDialogDesktop();
  const Header = isDesktop ? DialogHeader : DrawerHeader;
  return <Header>{children}</Header>;
}

export function ResponsiveDialogTitle({ children }: { children: ReactNode }) {
  const isDesktop = useResponsiveDialogDesktop();
  const Title = isDesktop ? DialogTitle : DrawerTitle;
  return <Title>{children}</Title>;
}

export function ResponsiveDialogDescription({ children }: { children: ReactNode }) {
  const isDesktop = useResponsiveDialogDesktop();
  const Description = isDesktop ? DialogDescription : DrawerDescription;
  return <Description>{children}</Description>;
}
