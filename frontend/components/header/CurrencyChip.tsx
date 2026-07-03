"use client";

import { useCurrency } from "@/lib/currency";
import { cn } from "@/lib/utils";

const SEGMENTS = ["EUR", "USD"] as const;

export function CurrencyChip() {
  const { currency, setCurrency } = useCurrency();
  return (
    <div
      role="radiogroup"
      aria-label="Display currency"
      className="inline-flex h-9 items-center rounded-full border border-border bg-card p-0.5"
    >
      {SEGMENTS.map((seg) => {
        const active = currency === seg;
        return (
          <button
            key={seg}
            type="button"
            role="radio"
            aria-checked={active}
            onClick={() => setCurrency(seg)}
            className={cn(
              "px-3 h-8 text-sm font-semibold rounded-full transition-colors",
              active
                ? "bg-foreground text-background"
                : "text-muted-foreground hover:bg-muted",
            )}
          >
            {seg}
          </button>
        );
      })}
    </div>
  );
}
