/**
 * Shared empty-state placeholder shown when the user selects "Custom" on a
 * TimeframeToggle but hasn't picked a from/to range yet. Used by both
 * NetWorthChart (in place of the chart) and PerfTable (in place of the table)
 * so the wording and visual treatment stay in sync.
 *
 * Visual mirrors the dashed-dropzone style that NetWorthChart used before
 * unification — reads as a placeholder rather than a card.
 */
export function CustomRangeEmpty() {
  return (
    <div className="flex h-80 flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border bg-muted/30 text-center text-sm text-muted-foreground md:h-[420px]">
      <p className="font-semibold">Pick a custom range</p>
      <p>Open the Custom pill and choose a from-date and to-date.</p>
    </div>
  );
}
