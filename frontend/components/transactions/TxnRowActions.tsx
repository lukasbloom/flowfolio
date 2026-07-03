"use client";

import { useRef } from "react";
import { History, MoreVertical, Pencil, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useMediaQuery } from "@/lib/use-media-query";

interface Props {
  txnId: string;
  deleted: boolean;
  /**
   * Called with the trigger element so the caller can restore focus after the
   * dialog closes.
   */
  onEdit: (triggerEl: HTMLButtonElement) => void;
  onDelete: () => void;
  onHistory: () => void;
  /**
   * Transaction type — only `adjustment` rows are now edit-disabled.
   * Yield rows (both manual and auto-accrual) are routed to EditTxnDialog
   * which shows the appropriate form or ActionBanner.
   */
  txnType?: string;
}

const SYSTEM_MANAGED_TOOLTIP = "Auto-generated — edit the source instead.";

export function TxnRowActions({
  deleted,
  onEdit,
  onDelete,
  onHistory,
  txnType,
}: Props) {
  // Ref for the mobile DropdownMenu trigger — passed to onEdit so focus can
  // be restored to the correct element when the edit dialog closes.
  const dropdownTriggerRef = useRef<HTMLButtonElement>(null);

  // Only `adjustment` rows remain edit-disabled (system-managed, no user edit path).
  // Yield rows are no longer disabled: EditTxnDialog routes them to YieldForm
  // (manual) or the ActionBanner read-only view (auto-accrual).
  const editDisabled = txnType === "adjustment";

  // This component is rendered once per virtual row, so
  // mounting BOTH the desktop tooltip-buttons cluster and the mobile Radix
  // DropdownMenu in every row was paying ~4× the necessary component cost.
  // Gate by viewport at render-time so each row only mounts the branch it
  // actually shows.
  const isDesktop = useMediaQuery("(min-width: 768px)");

  if (isDesktop) {
    return (
      <div className="flex items-center gap-1">
        {!deleted && (
          <>
            {editDisabled ? (
              <TooltipProvider>
                <Tooltip>
                  <TooltipTrigger asChild>
                    {/* span wrapper so the tooltip still fires on a disabled button */}
                    <span tabIndex={0}>
                      <Button
                        variant="ghost"
                        size="icon"
                        aria-label="Edit transaction (disabled)"
                        disabled
                      >
                        <Pencil className="size-4" aria-hidden="true" />
                      </Button>
                    </span>
                  </TooltipTrigger>
                  <TooltipContent>{SYSTEM_MANAGED_TOOLTIP}</TooltipContent>
                </Tooltip>
              </TooltipProvider>
            ) : (
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label="Edit transaction"
                    onClick={(e) => onEdit(e.currentTarget as HTMLButtonElement)}
                  >
                    <Pencil className="size-4" aria-hidden="true" />
                  </Button>
                </TooltipTrigger>
                <TooltipContent>Edit</TooltipContent>
              </Tooltip>
            )}
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label="Delete transaction"
                  className="hover:text-destructive"
                  onClick={onDelete}
                >
                  <Trash2 className="size-4" aria-hidden="true" />
                </Button>
              </TooltipTrigger>
              <TooltipContent>Delete</TooltipContent>
            </Tooltip>
          </>
        )}
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon"
              aria-label="View audit history"
              onClick={onHistory}
            >
              <History className="size-4" aria-hidden="true" />
            </Button>
          </TooltipTrigger>
          <TooltipContent>Audit history</TooltipContent>
        </Tooltip>
      </div>
    );
  }

  // Mobile: collapsed into a MoreVertical dropdown
  return (
    <div className="flex">
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            ref={dropdownTriggerRef}
            variant="ghost"
            size="icon"
            aria-label="Row actions"
          >
            <MoreVertical className="size-4" aria-hidden="true" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          {!deleted && (
            <>
              <DropdownMenuItem
                onClick={editDisabled
                  ? undefined
                  : () => { if (dropdownTriggerRef.current) onEdit(dropdownTriggerRef.current); }
                }
                disabled={editDisabled}
                title={editDisabled ? SYSTEM_MANAGED_TOOLTIP : undefined}
              >
                <Pencil className="size-4" />
                <span>Edit transaction</span>
              </DropdownMenuItem>
              <DropdownMenuItem
                variant="destructive"
                onClick={onDelete}
              >
                <Trash2 className="size-4" />
                <span>Delete transaction</span>
              </DropdownMenuItem>
            </>
          )}
          <DropdownMenuItem onClick={onHistory}>
            <History className="size-4" />
            <span>View audit history</span>
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}
