import { test } from "node:test";
import assert from "node:assert/strict";

// NOTE: Imports `../reconciliation-drift.ts` with the explicit extension
// because Node's ESM resolver (used at runtime via
// `node --test --experimental-strip-types`) requires it. The
// `lib/__tests__/` directory is excluded from `tsconfig.json` so TypeScript's
// "no .ts extension in imports" rule does not fire here; the file is consumed
// only by Node's standalone test runner, never by the Next.js bundle.
import { isRowUnresolved } from "../reconciliation-drift.ts";

// Regression lock: the footer actionable-count (which gates
// the Save button) must count ONLY actionable rows (drift / missing /
// phantom) and never matched rows. A matched row must NOT block Save.

test("matched row (server trailing-zero form vs user shorthand) is NOT counted", () => {
  // The server stringifies app qty with trailing zeros; the user types "15.5".
  // decimalStringsEqual normalizes both to the same canonical form, so this is
  // a match and must be excluded — the classic over-count bug (no Number()).
  assert.equal(
    isRowUnresolved({
      appQty: "15.500000000000000000",
      snapQty: "15.5",
      hasDecision: false,
    }),
    false,
  );
});

test("exact-string matched row is NOT counted", () => {
  assert.equal(
    isRowUnresolved({ appQty: "2.5", snapQty: "2.5", hasDecision: false }),
    false,
  );
});

test("empty-input row is NOT counted (user hasn't typed a snapshot qty)", () => {
  assert.equal(
    isRowUnresolved({ appQty: "15.5", snapQty: "", hasDecision: false }),
    false,
  );
});

test("drifted row with NO decision IS counted", () => {
  assert.equal(
    isRowUnresolved({ appQty: "15.5", snapQty: "16", hasDecision: false }),
    true,
  );
});

test("drifted row WITH a staged decision is NOT counted", () => {
  assert.equal(
    isRowUnresolved({ appQty: "15.5", snapQty: "16", hasDecision: true }),
    false,
  );
});

test("sub-satoshi difference IS a real drift and IS counted (no IEEE-754 collapse)", () => {
  // A difference small enough to vanish under Number() equality is still a
  // genuine drift; decimalStringsEqual keeps it distinct, so it must count.
  assert.equal(
    isRowUnresolved({
      appQty: "0.10000000000000000",
      snapQty: "0.10000000000000001",
      hasDecision: false,
    }),
    true,
  );
});

test("phantom-shaped row (app qty, broker 0) with no decision IS counted", () => {
  assert.equal(
    isRowUnresolved({ appQty: "3", snapQty: "0", hasDecision: false }),
    true,
  );
});
