"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Check, ChevronDown } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { apiFetch } from "@/lib/api-client";
import { cn } from "@/lib/utils";

interface Instrument {
  id: string;
  symbol: string;
  name: string;
}

interface InstrumentMultiSelectProps {
  /** Currently selected instrument ids. Empty array means "all instruments". */
  value: string[];
  /** Called with the next selection (sorted by user-toggle order). */
  onChange: (next: string[]) => void;
  className?: string;
}

/**
 * Per-chart instrument multi-select. Empty selection reads
 * "All instruments" and represents the full-portfolio default; one
 * selection reads the instrument's symbol; many selections read
 * "N selected".
 *
 * Mirrors the Popover + Input + button-row structure used in
 * `AddInstrumentRow.tsx` for visual parity. (The plan suggested shadcn
 * `Command + Checkbox` primitives but those are not installed in this
 * project; the Popover/Input pattern is the project precedent.)
 *
 * Uses a held-only TanStack Query cache key `["instruments", { held: true }]`
 * distinct from the project-wide `["instruments"]` cache so existing
 * consumers of the full list (notably `NetWorthChart`'s missing-price
 * warning hint at line ~155) are unaffected. A previously-persisted
 * localStorage selection may reference an instrument no longer in the
 * held list — the UUID still drives the chart filter, but the trigger
 * label falls back to `"1 selected"` since the symbol lookup misses. The
 * user clears via the Clear button; no auto-prune.
 */
export function InstrumentMultiSelect({
  value,
  onChange,
  className,
}: InstrumentMultiSelectProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");

  const { data: instruments = [] } = useQuery<Instrument[]>({
    queryKey: ["instruments", { held: true }],
    queryFn: () => apiFetch<Instrument[]>("/api/instruments?held=true"),
    staleTime: 60_000,
  });

  // Sort once for display; keep the user's selection order stable in `value`.
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return instruments
      .slice()
      .sort((a, b) => a.symbol.localeCompare(b.symbol))
      .filter((i) => {
        if (!q) return true;
        return (
          i.symbol.toLowerCase().includes(q) ||
          i.name.toLowerCase().includes(q)
        );
      });
  }, [instruments, query]);

  const selectedSet = useMemo(() => new Set(value), [value]);

  const triggerLabel = useMemo(() => {
    if (value.length === 0) return "All instruments";
    if (value.length === 1) {
      const match = instruments.find((i) => i.id === value[0]);
      return match?.symbol ?? "1 selected";
    }
    return `${value.length} selected`;
  }, [value, instruments]);

  function toggle(id: string) {
    if (selectedSet.has(id)) {
      onChange(value.filter((v) => v !== id));
    } else {
      onChange([...value, id]);
    }
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          size="sm"
          className={cn(
            "min-h-9 shrink-0 justify-between gap-2 px-3 text-xs font-medium",
            className
          )}
          aria-label="Filter chart by instrument"
        >
          <span className="truncate">{triggerLabel}</span>
          <ChevronDown className="size-3.5 shrink-0 opacity-60" aria-hidden />
        </Button>
      </PopoverTrigger>
      <PopoverContent
        align="end"
        collisionPadding={8}
        className="w-72 p-0"
      >
        <div className="border-b p-2">
          <Input
            autoFocus
            placeholder="Search instruments…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        <div className="max-h-72 overflow-y-auto py-1">
          {filtered.length === 0 ? (
            <p className="px-3 py-2 text-xs text-muted-foreground">
              {query
                ? `No instruments match "${query}".`
                : "No instruments to filter."}
            </p>
          ) : (
            filtered.map((instrument) => {
              const isSelected = selectedSet.has(instrument.id);
              return (
                <button
                  key={instrument.id}
                  type="button"
                  onClick={() => toggle(instrument.id)}
                  className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-accent hover:text-accent-foreground"
                  aria-pressed={isSelected}
                >
                  <span
                    aria-hidden
                    className={cn(
                      "flex size-4 shrink-0 items-center justify-center rounded-sm border",
                      isSelected
                        ? "border-primary bg-primary text-primary-foreground"
                        : "border-input"
                    )}
                  >
                    {isSelected ? <Check className="size-3" /> : null}
                  </span>
                  <span className="font-medium">{instrument.symbol}</span>
                  <span className="ml-auto truncate text-muted-foreground">
                    {instrument.name}
                  </span>
                </button>
              );
            })
          )}
        </div>
        <div className="border-t p-1">
          <button
            type="button"
            onClick={() => onChange([])}
            disabled={value.length === 0}
            className="flex w-full items-center justify-center rounded px-3 py-2 text-xs text-muted-foreground hover:bg-accent hover:text-accent-foreground disabled:pointer-events-none disabled:opacity-50"
          >
            Clear
          </button>
        </div>
      </PopoverContent>
    </Popover>
  );
}
