"use client";

import { X } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

interface TagChipProps {
  name: string;
  removable?: boolean;
  onRemove?: () => void;
  // Required when removable=true to satisfy the aria-label contract:
  //   "Remove tag {name} from {symbol} in {account_name}"
  contextSymbol?: string;
  contextAccountName?: string;
  className?: string;
}

export function TagChip({
  name,
  removable = false,
  onRemove,
  contextSymbol,
  contextAccountName,
  className,
}: TagChipProps) {
  const ariaLabel =
    removable && contextSymbol && contextAccountName
      ? `Remove tag ${name} from ${contextSymbol} in ${contextAccountName}`
      : `Remove tag ${name}`;

  return (
    <Badge
      variant="secondary"
      className={cn(
        "h-7 gap-1 rounded-full px-2.5 text-sm font-normal",
        className
      )}
    >
      <span>{name}</span>
      {removable && (
        <button
          type="button"
          aria-label={ariaLabel}
          onClick={onRemove}
          className="ml-0.5 inline-flex items-center justify-center rounded-full p-0.5 hover:bg-muted"
        >
          <X className="size-3" aria-hidden />
        </button>
      )}
    </Badge>
  );
}
