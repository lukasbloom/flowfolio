import { test } from "node:test";
import assert from "node:assert/strict";

// NOTE: Imports `../decimal-strings.ts` with the explicit extension because
// Node's ESM resolver (used at runtime via
// `node --test --experimental-strip-types`) requires it. The `lib/__tests__/`
// directory is excluded from `tsconfig.json` so TypeScript's normal "no .ts
// extension in imports" rule doesn't fire here; the file is consumed only by
// Node's standalone test runner, never by the Next.js bundle.
import {
  decimalField,
  decimalStringsEqual,
  normalizeDecimalInput,
  toDisplayNumber,
} from "../decimal-strings.ts";

// Characterization tests for the reconciliation money-equality
// gate. These pin CURRENT behavior of `decimalStringsEqual`; they do not impose
// new behavior. Where the source diverges from a naive numeric expectation the
// divergence is flagged in-line.

// --- Equal after normalization -------------------------------------------

test("trailing zeros after the decimal point are stripped: '1.500' == '1.5'", () => {
  assert.equal(decimalStringsEqual("1.500", "1.5"), true);
});

test("leading '+' is stripped: '+1.5' == '1.5'", () => {
  assert.equal(decimalStringsEqual("+1.5", "1.5"), true);
});

test("a bare trailing '.' is stripped: '1.' == '1'", () => {
  assert.equal(decimalStringsEqual("1.", "1"), true);
});

test("leading zeros on an integer (no decimal point) are stripped: '0007' == '7'", () => {
  assert.equal(decimalStringsEqual("0007", "7"), true);
});

test("integer vs decimal with trailing zero: '10' == '10.0'", () => {
  assert.equal(decimalStringsEqual("10", "10.0"), true);
});

// --- Zero family ----------------------------------------------------------

test("negative zero integer normalizes to '0': '-0' == '0'", () => {
  assert.equal(decimalStringsEqual("-0", "0"), true);
});

test("decimal zero normalizes to '0': '0.000' == '0'", () => {
  assert.equal(decimalStringsEqual("0.000", "0"), true);
});

test("negative decimal zero normalizes to '0': '-0.000' == '0'", () => {
  assert.equal(decimalStringsEqual("-0.000", "0"), true);
});

test("'-0.0' == '0'", () => {
  assert.equal(decimalStringsEqual("-0.0", "0"), true);
});

// --- Real drift must NOT be equal ----------------------------------------

test("sub-satoshi drift stays UNEQUAL (the whole point of avoiding Number()): '...454' != '...455'", () => {
  assert.equal(
    decimalStringsEqual(
      "1012.542910340662615454",
      "1012.542910340662615455"
    ),
    false
  );
});

test("'1.5' != '1.50000001'", () => {
  assert.equal(decimalStringsEqual("1.5", "1.50000001"), false);
});

// --- Negative handling ----------------------------------------------------

test("negative trailing-zero strip: '-1.50' == '-1.5'", () => {
  assert.equal(decimalStringsEqual("-1.50", "-1.5"), true);
});

test("sign matters: '-1.5' != '1.5'", () => {
  assert.equal(decimalStringsEqual("-1.5", "1.5"), false);
});

// --- Null / undefined / empty (characterization) --------------------------

test("null is never equal to a value: (null, '1') -> false", () => {
  assert.equal(decimalStringsEqual(null, "1"), false);
});

test("undefined vs undefined -> false (any null/undefined short-circuits to false)", () => {
  assert.equal(decimalStringsEqual(undefined, undefined), false);
});

test("characterization: two empty strings short-circuit normalization and are EQUAL: ('', '') -> true", () => {
  // Both args are non-null strings, so the null/undefined guard does not fire.
  // normalize("") returns "" un-normalized (the `n === ""` short-circuit), and
  // "" === "" so the function returns true. Pinning current behavior.
  assert.equal(decimalStringsEqual("", ""), true);
});

test("characterization: empty string is NOT equal to '0': ('', '0') -> false", () => {
  // normalize("") -> "" ; normalize("0") -> "0" ; "" !== "0".
  assert.equal(decimalStringsEqual("", "0"), false);
});

test("characterization: bare '-' short-circuits un-normalized and equals itself: ('-', '-') -> true", () => {
  assert.equal(decimalStringsEqual("-", "-"), true);
});

// --- Whitespace -----------------------------------------------------------

test("surrounding whitespace is trimmed: ' 1.5 ' == '1.5'", () => {
  assert.equal(decimalStringsEqual(" 1.5 ", "1.5"), true);
});

// --- Leading zeros are stripped whether or not a decimal point is present.
//     (This was a real bug — the old normalize() only stripped leading zeros
//     in the no-decimal-point branch, so "01.5" wrongly compared unequal to
//     "1.5". Fixed: normalize() now canonicalizes the integer part uniformly.)
test("leading zero with a decimal point is stripped: '01.5' == '1.5'", () => {
  assert.equal(decimalStringsEqual("01.5", "1.5"), true);
});

test("leading + trailing zeros together: '01.50' == '1.5'", () => {
  assert.equal(decimalStringsEqual("01.50", "1.5"), true);
});

test("bare fractional vs zero-prefixed: '.5' == '0.5'", () => {
  assert.equal(decimalStringsEqual(".5", "0.5"), true);
});

test("negative leading zero stripped: '-01.5' == '-1.5'", () => {
  assert.equal(decimalStringsEqual("-01.5", "-1.5"), true);
});

// --- toDisplayNumber ------------------------------------
// The contained display-math coercion. Behavior contract: a normal numeric
// string parses; null/undefined/"" collapse to 0 (the old guarded call sites
// never passed null/undefined through to Number, so this is behavior-preserving).

test("toDisplayNumber: normal decimal string parses to its number", () => {
  assert.equal(toDisplayNumber("1234.56"), 1234.56);
});

test("toDisplayNumber: null collapses to 0", () => {
  assert.equal(toDisplayNumber(null), 0);
});

test("toDisplayNumber: empty string collapses to 0", () => {
  assert.equal(toDisplayNumber(""), 0);
});

// --- normalizeDecimalInput -------------------------------------
// The shared canonicalizer behind decimalStringsEqual and the forms' decimalField.

test("normalizeDecimalInput strips leading zeros: '01.5' -> '1.5'", () => {
  assert.equal(normalizeDecimalInput("01.5"), "1.5");
});

test("normalizeDecimalInput strips trailing zeros: '1.50' -> '1.5'", () => {
  assert.equal(normalizeDecimalInput("1.50"), "1.5");
});

test("normalizeDecimalInput leading + trailing: '01.50' -> '1.5'", () => {
  assert.equal(normalizeDecimalInput("01.50"), "1.5");
});

test("normalizeDecimalInput bare fraction: '.5' -> '0.5'", () => {
  assert.equal(normalizeDecimalInput(".5"), "0.5");
});

test("normalizeDecimalInput integer leading zeros: '007' -> '7'", () => {
  assert.equal(normalizeDecimalInput("007"), "7");
});

test("normalizeDecimalInput collapses negative zero: '-0.000' -> '0'", () => {
  assert.equal(normalizeDecimalInput("-0.000"), "0");
});

test("normalizeDecimalInput trims integer-valued decimal: '10.0' -> '10'", () => {
  assert.equal(normalizeDecimalInput("10.0"), "10");
});

// Comma OR dot accepted as the decimal separator.
test("normalizeDecimalInput converts comma to dot: '1,5' -> '1.5'", () => {
  assert.equal(normalizeDecimalInput("1,5"), "1.5");
});

test("normalizeDecimalInput comma + leading/trailing zeros: '01,50' -> '1.5'", () => {
  assert.equal(normalizeDecimalInput("01,50"), "1.5");
});

// --- decimalField ----------------------------------------------
// Shared Zod field used by every numeric form input.

test("decimalField accepts a dot value and normalizes it", () => {
  const r = decimalField().safeParse("01.50");
  assert.equal(r.success, true);
  if (r.success) assert.equal(r.data, "1.5");
});

test("decimalField accepts a comma value and normalizes to dot (Option B)", () => {
  const r = decimalField().safeParse("1,5");
  assert.equal(r.success, true);
  if (r.success) assert.equal(r.data, "1.5");
});

test("decimalField rejects non-numeric input", () => {
  assert.equal(decimalField().safeParse("abc").success, false);
});

test("decimalField rejects grouped thousands (no separator grouping): '1.234,56'", () => {
  assert.equal(decimalField().safeParse("1.234,56").success, false);
});

test("decimalField positive=true rejects zero", () => {
  assert.equal(decimalField({ positive: true }).safeParse("0").success, false);
});

test("decimalField positive=true accepts a positive value", () => {
  const r = decimalField({ positive: true }).safeParse("2,5");
  assert.equal(r.success, true);
  if (r.success) assert.equal(r.data, "2.5");
});
