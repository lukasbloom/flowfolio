import { test } from "node:test";
import assert from "node:assert/strict";

// NOTE: Imports `../format.ts` with the explicit extension because Node's
// ESM resolver (used at runtime via `node --test --experimental-strip-types`)
// requires it. The `lib/__tests__/` directory is excluded from `tsconfig.json`
// so TypeScript's normal "no .ts extension in imports" rule doesn't fire
// here; the file is consumed only by Node's standalone test runner, never
// by the Next.js bundle.
import { formatCompactMoney, formatMoney, formatPercent } from "../format.ts";

// Pins the new PREFIX layout for USD formatMoney
// and both branches of formatCompactMoney, while leaving EUR formatMoney
// (already prefix via Intl style:"currency") and formatPercent (suffix " %")
// untouched as regression guards.

test("formatMoney USD renders as prefix '$' with en-GB separators (2dp)", () => {
  assert.equal(formatMoney(1234.5, "USD"), "$1,234.50");
});

test("formatMoney USD zero is '$0.00'", () => {
  assert.equal(formatMoney(0, "USD"), "$0.00");
});

test("formatMoney USD negative contains both '$' and the absolute value (locale-dependent ordering)", () => {
  const out = formatMoney(-50, "USD");
  assert.ok(out.includes("$"), `expected '$' in ${out}`);
  assert.ok(out.includes("50.00"), `expected '50.00' in ${out}`);
  assert.ok(out.includes("-"), `expected '-' in ${out}`);
});

test("formatMoney EUR already prefixes via Intl; tolerates NBSP/thin-space between symbol and number", () => {
  const out = formatMoney(1234.5, "EUR");
  // Intl.NumberFormat("en-GB", { style:"currency", currency:"EUR" }) emits "€1,234.50" today,
  // but the regex tolerates Intl emitting a NBSP/thin space before the number.
  assert.match(out, /^€\s?1,234\.50$/);
});

test("formatCompactMoney EUR base branch (< 1,000) prefixes with '€'", () => {
  assert.equal(formatCompactMoney(425, "EUR"), "€425");
});

test("formatCompactMoney EUR K branch (>= 1,000) prefixes with '€'", () => {
  assert.equal(formatCompactMoney(12500, "EUR"), "€12.5K");
});

test("formatCompactMoney USD M branch (>= 1,000,000) prefixes with '$'", () => {
  assert.equal(formatCompactMoney(1234567, "USD"), "$1.2M");
});

test("formatCompactMoney USD base branch (< 1,000) prefixes with '$'", () => {
  assert.equal(formatCompactMoney(425, "USD"), "$425");
});

test("formatPercent unchanged — keeps trailing '%' suffix, never a prefix (regression guard)", () => {
  // Browser Intl emits "12.34 %" (NBSP/thin-space → " %" after the existing
  // .replace() chain). Node's V8 Intl variant emits a bare "12.34%". Either
  // way the assertion that matters for the prefix-flip work is: percent is
  // SUFFIX, never a leading sigil. Pin that invariant only.
  const out = formatPercent(0.1234);
  assert.ok(out.endsWith("%"), `expected trailing '%' in ${JSON.stringify(out)}`);
  assert.ok(!out.startsWith("%"), `expected NOT to start with '%' in ${JSON.stringify(out)}`);
  assert.ok(out.includes("12.34"), `expected '12.34' in ${JSON.stringify(out)}`);
});
