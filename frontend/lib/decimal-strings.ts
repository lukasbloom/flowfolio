/**
 * Decimal-string utilities for money input + the reconciliation flow.
 *
 * The persisted quantity is server-derived via Python Decimal — the frontend
 * MUST NOT use Number() coercion for equality checks or button-gate math, per
 * CLAUDE.md "NEVER float for money". These helpers operate purely on the
 * string representation and never round-trip through IEEE-754.
 */
import { z } from "zod";

/**
 * Canonicalize a decimal-as-string to a single plain form. The one place the
 * client decides what a valid number looks like (mirrors the backend's
 * DecimalText canonicalization at the persistence boundary).
 *
 * Rules: trim; accept comma OR dot as the decimal separator and normalize to
 * dot (plan 008 — locale input accommodation); strip the leading "+"; strip
 * ALL leading zeros on the integer part and trailing zeros on the fraction;
 * collapse "-0"/"0.000" to "0". Empty string and bare "-" pass through
 * unchanged. Never goes through Number()/IEEE-754.
 *
 * NOTE: this accepts a single decimal separator only — no thousands grouping
 * (e.g. "1.234,56" is NOT a valid grouped number here; full locale formatting
 * is UX-H8, out of scope).
 */
export function normalizeDecimalInput(raw: string): string {
  const n = raw.trim();
  if (n === "" || n === "-") return n;
  // Comma-or-dot decimal separator → dot (plan 008, Option B).
  const dotted = n.replace(",", ".");
  // Strip a leading sign, remembering negativity.
  const negative = dotted.startsWith("-");
  const unsigned =
    dotted.startsWith("+") || dotted.startsWith("-") ? dotted.slice(1) : dotted;
  // Split integer / fractional parts (no decimal point => empty fraction).
  const [rawInt = "", rawFrac = ""] = unsigned.split(".");
  const intPart = rawInt.replace(/^0+/, "") || "0"; // strip ALL leading zeros
  const fracPart = rawFrac.replace(/0+$/, ""); // strip trailing zeros
  const v = fracPart ? `${intPart}.${fracPart}` : intPart;
  if (v === "0") return "0"; // canonical zero (collapses -0, 0.000, etc.)
  return negative ? `-${v}` : v;
}

/**
 * Compare two Decimal-as-string values for equality WITHOUT going through
 * Number() coercion. Two quantities are equal iff their canonical forms are
 * identical. A sub-satoshi difference that would pass IEEE-754 equality is
 * still a real drift, so we never want to treat them as matched.
 *
 * Extracted to /lib to close the Number()-based matched check and so both
 * ReconciliationDiffTable and RejectDriftDrawer can share it.
 */
export function decimalStringsEqual(
  a: string | undefined | null,
  b: string | undefined | null
): boolean {
  if (a === null || a === undefined || b === null || b === undefined)
    return false;
  return normalizeDecimalInput(a) === normalizeDecimalInput(b);
}

/**
 * Shared Zod schema for a money/quantity text field (plan 008). Validates the
 * shape (digits with an optional single comma/dot decimal separator) and
 * transforms the submitted value to canonical form via normalizeDecimalInput,
 * so every form sends the backend a canonical string. Replaces the six
 * copy-pasted `z.string().regex(/^\d+([.,]\d+)?$/, ...)` fields.
 */
export function decimalField(opts?: { positive?: boolean; message?: string }) {
  const base = z
    .string()
    .regex(/^\d+([.,]\d+)?$/, opts?.message ?? "Enter a valid number")
    .transform(normalizeDecimalInput);
  if (opts?.positive) {
    return base.refine((s) => Number(s) > 0, "Must be > 0");
  }
  return base;
}

/**
 * Convert a Decimal-as-string API value to a JS number FOR DISPLAY MATH ONLY.
 *
 * Contract: safe for aggregation/sorting of portfolio-scale values rendered
 * at ≤ 2dp (float64 carries ~15-16 significant digits; portfolio values are
 * well inside that). NEVER use the result for equality checks, persistence,
 * or anything sent back to the API — use decimalStringsEqual / the raw
 * string for those. See plans/006 for the backend's exact-decimal contract.
 *
 * null / undefined / "" → 0. Note this differs from bare `Number(undefined)`
 * (which is NaN): every existing call site either pre-checks null/undefined
 * or operates on a non-null `string` field, so this collapse is behavior-
 * preserving there. New call sites that need NaN-on-undefined must not use
 * this helper.
 */
export function toDisplayNumber(value: string | null | undefined): number {
  if (value === null || value === undefined || value === "") return 0;
  return Number(value);
}
