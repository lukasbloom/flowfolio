"use client";

import { BackfillAllButton } from "./BackfillAllButton";

export function PriceHistorySection() {
  return (
    <section
      aria-labelledby="price-history-section-heading"
      className="space-y-4"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="space-y-1">
          <h2
            id="price-history-section-heading"
            className="text-base font-semibold"
          >
            Price history
          </h2>
          <p className="text-sm text-muted-foreground">
            Refresh historical price data from your stock and crypto providers.
            Fetches one daily quote per instrument since your first transaction.
          </p>
        </div>
        <BackfillAllButton />
      </div>
    </section>
  );
}
