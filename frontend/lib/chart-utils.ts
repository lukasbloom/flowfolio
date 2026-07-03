/**
 * Shared chart helpers, promoted from byte-identical copies in
 * NetWorthChart and InstrumentPriceChart.
 */

/**
 * Escape a string for safe interpolation into an ECharts tooltip's innerHTML.
 * ECharts tooltip formatters emit raw HTML, so any
 * user-controlled text (instrument symbols/names) must be entity-escaped.
 */
export function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/**
 * Format a Date as a local-calendar YYYY-MM-DD string. Uses the local
 * getFullYear/getMonth/getDate (NOT toISOString, which would shift to UTC and
 * can land on the wrong calendar day) so it matches backend calendar dates.
 */
export function toIsoDate(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}
