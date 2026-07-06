import { test } from "node:test";
import assert from "node:assert/strict";

// Explicit .ts extension: consumed only by Node's `--experimental-strip-types`
// test runner, never by the Next.js bundle (see decimal-strings.test.ts).
import { sanitizeInstrumentFilter } from "../instrument-filter-sanitize.ts";

test("keeps ids that exist in the known set", () => {
  assert.deepEqual(
    sanitizeInstrumentFilter(["a", "b"], new Set(["a", "b", "c"])),
    ["a", "b"],
  );
});

test("drops a stale id (deleted instrument / restored DB)", () => {
  assert.deepEqual(
    sanitizeInstrumentFilter(["a", "stale", "b"], new Set(["a", "b"])),
    ["a", "b"],
  );
});

test("an all-stale filter collapses to [] (whole portfolio)", () => {
  assert.deepEqual(sanitizeInstrumentFilter(["x", "y"], new Set(["a"])), []);
});

test("an already-empty filter stays empty", () => {
  assert.deepEqual(sanitizeInstrumentFilter([], new Set(["a"])), []);
});

test("preserves the original order of surviving ids", () => {
  assert.deepEqual(
    sanitizeInstrumentFilter(["c", "a", "b"], new Set(["a", "b", "c"])),
    ["c", "a", "b"],
  );
});
