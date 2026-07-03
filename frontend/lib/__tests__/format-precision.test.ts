import { test } from "node:test";
import assert from "node:assert/strict";

// NOTE: Imports `../format.ts` with the explicit extension because Node's
// ESM resolver (used at runtime via `node --test --experimental-strip-types`)
// requires it. The `lib/__tests__/` directory is excluded from `tsconfig.json`
// so TypeScript's normal "no .ts extension in imports" rule doesn't fire here;
// the file is consumed only by Node's standalone test runner, never by the
// Next.js bundle. Mirrors the convention in format.test.ts.
import {
  formatMoney,
  formatSignedMoney,
  directionalColor,
} from "../format.ts";
import {
  DELETE_DIALOG_SOFT_DELETE_COPY,
  DELETE_DIALOG_SOFT_DELETE_COPY_LEAD,
} from "../../components/transactions/delete-dialog-copy.ts";

// Pins the Decimal-consistent precision contract for the
// realized-P&L / KPI magnitude display path (RealizedCell + KpiStrip both
// delegate to formatSignedMoney → formatMoney; the fix lives at the helper,
// never re-introduced per-component).
//
// The pre-fix path coerced the
// magnitude through `Number(value)` inside formatMoney, which rounds any value
// beyond Number.MAX_SAFE_INTEGER (9_007_199_254_740_991). For a typical
// portfolio the realized/KPI magnitudes are bounded far within that
// range, so the loss was latent rather than observable — but the helper path
// was genuinely lossy at the boundary. The fix hands the Decimal STRING form
// straight to Intl.NumberFormat (which formats string inputs without an
// IEEE-754 round-trip), extending the sign-from-the-string discipline that
// formatSignedMoney already used.

// A magnitude with one more significant digit than JS `number` can represent
// exactly: 9_007_199_254_740_993 is just past Number.MAX_SAFE_INTEGER, and the
// trailing ".99" is precisely what a `Number()` round-trip would corrupt to
// ".00" / "...994". If this assertion ever fails with "...994.00", a lossy
// Number()/Math.abs coercion has been reintroduced into the magnitude path.
const HIGH_PRECISION_MAGNITUDE = "9007199254740993.99";

test("formatMoney EUR preserves Decimal precision beyond Number.MAX_SAFE_INTEGER (no Number() round-trip)", () => {
  assert.equal(
    formatMoney(HIGH_PRECISION_MAGNITUDE, "EUR"),
    "€9,007,199,254,740,993.99"
  );
});

test("formatMoney USD preserves Decimal precision beyond Number.MAX_SAFE_INTEGER", () => {
  assert.equal(
    formatMoney(HIGH_PRECISION_MAGNITUDE, "USD"),
    "$9,007,199,254,740,993.99"
  );
});

test("formatSignedMoney (the realized/KPI path) preserves precision for a large positive magnitude", () => {
  // RealizedCell and KpiStrip render lifetime realized P&L via this helper.
  assert.equal(
    formatSignedMoney(HIGH_PRECISION_MAGNITUDE, "EUR"),
    "+ €9,007,199,254,740,993.99"
  );
});

test("formatSignedMoney preserves precision AND derives abs from the string for a large negative magnitude", () => {
  // The leading minus is stripped from the STRING form (never via Math.abs), so
  // the magnitude survives and only the sign prefix differs.
  assert.equal(
    formatSignedMoney("-" + HIGH_PRECISION_MAGNITUDE, "EUR"),
    "− €9,007,199,254,740,993.99"
  );
});

test("formatSignedMoney keeps the bounded realized/KPI magnitudes in play exact (regression pin for the values actually rendered)", () => {
  // A realistic large realized total for a six-figure portfolio — these are
  // bounded well within Number range, so this pins the current correct output
  // so it cannot silently regress.
  assert.equal(formatSignedMoney("123456.78", "EUR"), "+ €123,456.78");
  assert.equal(formatSignedMoney("-99.50", "USD"), "− $99.50");
  assert.equal(formatSignedMoney("0", "EUR"), "€0.00");
});

test("directionalColor agrees with the sign of a high-precision string magnitude", () => {
  // The directional color token is derived for the same realized/KPI values;
  // pin that the sign classification is correct for large precise magnitudes.
  assert.equal(directionalColor(HIGH_PRECISION_MAGNITUDE), "text-positive");
  assert.equal(directionalColor("-" + HIGH_PRECISION_MAGNITUDE), "text-negative");
  assert.equal(directionalColor("0"), "text-muted-foreground");
  assert.equal(directionalColor(null), "text-muted-foreground");
});

// The delete-transaction dialog copy must honestly describe
// soft-delete behavior and must never claim a deleted transaction can be
// "restored". The audit found the misleading "restore from audit history"
// wording was already removed; these assertions lock that so it cannot return.
// The copy is asserted via the JSX-free constant DeleteConfirmDialog.tsx
// renders (the dialog itself can't be imported here — node:test strips types
// but cannot transform the component's JSX).

test("delete-dialog copy honestly describes soft-delete", () => {
  assert.ok(
    DELETE_DIALOG_SOFT_DELETE_COPY.includes("Soft-deleted"),
    `expected 'Soft-deleted' in: ${DELETE_DIALOG_SOFT_DELETE_COPY}`
  );
  assert.ok(
    DELETE_DIALOG_SOFT_DELETE_COPY.includes("Show deleted"),
    `expected the 'Show deleted' affordance in: ${DELETE_DIALOG_SOFT_DELETE_COPY}`
  );
});

test("delete-dialog copy contains NO misleading 'restore' wording (regression guard)", () => {
  assert.ok(
    !/restore/i.test(DELETE_DIALOG_SOFT_DELETE_COPY),
    `delete-dialog copy must not promise restore, got: ${DELETE_DIALOG_SOFT_DELETE_COPY}`
  );
  assert.ok(
    !/audit history/i.test(DELETE_DIALOG_SOFT_DELETE_COPY),
    `delete-dialog copy must not reference 'audit history' as a restore path, got: ${DELETE_DIALOG_SOFT_DELETE_COPY}`
  );
  // The lead sentence is what a user reads first — guard it explicitly too.
  assert.ok(!/restore/i.test(DELETE_DIALOG_SOFT_DELETE_COPY_LEAD));
});
