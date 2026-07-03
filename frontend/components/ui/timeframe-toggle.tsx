"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Calendar } from "@/components/ui/calendar";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { cn } from "@/lib/utils";

/**
 * TimeframeToggle: shared presentational primitive consumed
 * by NetWorthSection and PerformanceSection. Knows nothing about URLs, data
 * fetching, or pages — pure UI + (optional) custom-range Popover.
 *
 * Visual is the canonical Perf grid (fixed-width pills, no overflow scroll).
 * Accessibility comes from Radix ToggleGroup: role=radiogroup, roving tab
 * index, arrow-key navigation.
 *
 * Pass `customRange` to enable a final "Custom" pill. The pill's label is
 * ALWAYS the string "Custom" regardless of the picked dates — the active
 * range is rendered as a subtitle next to the chart heading (see
 * formatDateRange in @/lib/format).
 */

export type TimeframeValue = string;

export interface TimeframeTogglePresetOption {
  value: TimeframeValue;
  label: string;
}

export interface TimeframeToggleProps {
  presets: TimeframeTogglePresetOption[];
  value: TimeframeValue;
  onChange: (next: TimeframeValue) => void;
  ariaLabel: string;
  customRange?: {
    from: Date | null;
    to: Date | null;
    onChange: (range: { from: Date | null; to: Date | null }) => void;
  };
}

// Per-pill classes. The `rounded-md` is inherited from the shadcn
// `toggleVariants` base (active state in particular) and survives because the
// parent ToggleGroup is rendered with `spacing={1}` — when spacing=0, shadcn's
// segmented-control convention strips border-radius from middle items
// (toggle-group.tsx:67 `data-[spacing=0]:rounded-none`). With spacing≠0, every
// pill keeps `rounded-md`, so the active dark fill reads as a rounded pill.
const PILL_CLASSES =
  "px-3 text-xs text-muted-foreground data-[state=on]:bg-foreground data-[state=on]:text-background";

export function TimeframeToggle({
  presets,
  value,
  onChange,
  ariaLabel,
  customRange,
}: TimeframeToggleProps) {
  const hasCustom = customRange !== undefined;
  const cols = presets.length + (hasCustom ? 1 : 0);

  const [popoverOpen, setPopoverOpen] = useState(false);
  const [draftFrom, setDraftFrom] = useState<Date | undefined>(
    customRange?.from ?? undefined
  );
  const [draftTo, setDraftTo] = useState<Date | undefined>(
    customRange?.to ?? undefined
  );

  function handleValueChange(next: string) {
    if (!next) return; // ignore deselection (Radix fires empty on re-click)
    if (next === "custom") {
      // Lift current committed range into the draft so re-opening the
      // Popover doesn't visually reset the picked dates.
      setDraftFrom(customRange?.from ?? undefined);
      setDraftTo(customRange?.to ?? undefined);
      setPopoverOpen(true);
      onChange("custom");
      return;
    }
    setPopoverOpen(false);
    onChange(next);
  }

  function applyRange() {
    if (!customRange) return;
    if (!draftFrom || !draftTo) return;
    if (draftFrom > draftTo) return;
    customRange.onChange({ from: draftFrom, to: draftTo });
    onChange("custom");
    setPopoverOpen(false);
  }

  function cancelRange() {
    setDraftFrom(customRange?.from ?? undefined);
    setDraftTo(customRange?.to ?? undefined);
    setPopoverOpen(false);
  }

  return (
    <ToggleGroup
      type="single"
      // size="sm" → 32px pills (h-8) instead of 36px (h-9 default), so the
      // bar reads closer to the All-instruments dropdown's footprint.
      // spacing={1} → 4px gap AND tells shadcn's toggle-group to skip its
      // segmented-control rounding strip (see PILL_CLASSES comment above).
      size="sm"
      spacing={1}
      value={value}
      onValueChange={handleValueChange}
      aria-label={ariaLabel}
      className="grid rounded-lg border border-border bg-background p-0.5 sm:inline-grid"
      // Tailwind's grid-cols-N can't take a runtime value, so the column
      // template is inline. This keeps the pills equal-width regardless of
      // label length and prevents the horizontal-scroll overflow the
      // old NW selector showed on mobile.
      style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` }}
    >
      {presets.map((preset) => (
        <ToggleGroupItem
          key={preset.value}
          value={preset.value}
          aria-label={preset.label}
          className={cn(PILL_CLASSES)}
        >
          {preset.label}
        </ToggleGroupItem>
      ))}
      {hasCustom && (
        <Popover open={popoverOpen} onOpenChange={setPopoverOpen}>
          <PopoverTrigger asChild>
            <ToggleGroupItem
              value="custom"
              aria-label="Custom range"
              className={cn(PILL_CLASSES)}
            >
              Custom
            </ToggleGroupItem>
          </PopoverTrigger>
          <PopoverContent className="w-auto p-3" align="end">
            <div className="flex flex-col gap-3">
              <div className="flex flex-col gap-3 md:flex-row">
                <div className="flex flex-col gap-1">
                  <span className="text-xs text-muted-foreground">From</span>
                  <Calendar
                    mode="single"
                    selected={draftFrom}
                    onSelect={setDraftFrom}
                    disabled={draftTo ? { after: draftTo } : undefined}
                  />
                </div>
                <div className="flex flex-col gap-1">
                  <span className="text-xs text-muted-foreground">To</span>
                  <Calendar
                    mode="single"
                    selected={draftTo}
                    onSelect={setDraftTo}
                    disabled={draftFrom ? { before: draftFrom } : undefined}
                  />
                </div>
              </div>
              <div className="flex justify-end gap-2">
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={cancelRange}
                >
                  Cancel
                </Button>
                <Button
                  type="button"
                  size="sm"
                  onClick={applyRange}
                  disabled={!draftFrom || !draftTo || draftFrom > draftTo}
                >
                  Apply range
                </Button>
              </div>
            </div>
          </PopoverContent>
        </Popover>
      )}
    </ToggleGroup>
  );
}
