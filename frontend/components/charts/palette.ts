// Chart palette. Scoped to the chart layer; NOT exposed as Tailwind tokens.
// Colors are WCAG-checked; the semantic-token equivalents are declared in globals.css @theme.

// Existing semantic tokens (mirror the hex equivalents declared in globals.css @theme):
export const POSITIVE = "#16A34A"; // --color-positive (oklch 0.598 0.151 152.0)
export const NEGATIVE = "#DC2626"; // --color-negative / --color-destructive
export const ACCENT = "#262626";   // --color-foreground
export const MUTED = "#737373";    // --color-muted-foreground
export const BORDER = "#E5E5E5";   // --color-border

// Chart-only constants (NOT design-system tokens):
export const CHART_REALIZED = "#D97706"; // amber-600 — realized segment + pie palette index 3
export const CHART_BLUE = "#0EA5E9";     // sky-500 — pie palette index 4 (5th distinct slice)
export const CHART_PURPLE = "#A855F7";   // purple-500 — pie palette index 5 (6th distinct slice)
export const CHART_TEAL = "#0D9488";     // teal-600 — pie palette index 0 (largest slice)
export const CHART_PINK = "#DB2777";     // pink-600 — pie palette index 1 (second slice)

// Allocation-pie 6-color sequence:
// Indices 0/1 swapped from ACCENT/MUTED (two near-duplicate dark grays
// that landed on the largest two slices) to teal/pink for at-a-glance distinction.
// ACCENT and MUTED remain exported for use as text/border tokens elsewhere.
export const PIE_PALETTE = [
  CHART_TEAL,     // was ACCENT (#262626) — too dark, indistinguishable from MUTED at glance
  CHART_PINK,     // was MUTED (#737373) — see above
  POSITIVE,       // green
  CHART_REALIZED, // amber
  CHART_BLUE,     // sky
  CHART_PURPLE,   // purple
] as const;

// Contributions stacked-bar palette:
export const CONTRIB_BAR_PALETTE = {
  deposits: POSITIVE,
  spendings: NEGATIVE,
  realized: CHART_REALIZED,
  yield: MUTED,
} as const;

// Cost-basis-vs-value (and other 2-series line charts) — index 0 = primary, index 1 = secondary.
// CHART_BLUE distinguishes the cost-basis trace from the green POSITIVE portfolio-value line
// even when the two overlap on monotone history.
export const LINE_PALETTE = [CHART_BLUE, POSITIVE] as const;
