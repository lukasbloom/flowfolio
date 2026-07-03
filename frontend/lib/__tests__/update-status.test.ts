import { test } from "node:test";
import assert from "node:assert/strict";

// Explicit .ts extension: this dir is excluded from tsconfig and consumed only
// by Node's standalone runner (`node --test --experimental-strip-types`).
import {
  deriveOverlayPhase,
  overlayCopy,
  versionsMatch,
} from "../update-status.ts";

const base = {
  updateState: null as string | null,
  pollFailed: false,
  reportedVersion: null as string | null,
  targetVersion: "v1.3.0" as string | null,
};

// A failed poll during the recreate window is `unreachable`, NOT `failed`.
test("a failed status poll maps to unreachable (not failed)", () => {
  assert.equal(
    deriveOverlayPhase({ ...base, updateState: "restarting", pollFailed: true }),
    "unreachable",
  );
});

test("a failed poll wins even over a backend 'failed' state (still recreating)", () => {
  assert.equal(
    deriveOverlayPhase({ ...base, updateState: "failed", pollFailed: true }),
    "unreachable",
  );
});

test("backend spinner states pass through 1:1", () => {
  for (const s of ["preparing", "pulling", "restarting"]) {
    assert.equal(deriveOverlayPhase({ ...base, updateState: s }), s);
  }
});

test("backend failed maps to failed", () => {
  assert.equal(deriveOverlayPhase({ ...base, updateState: "failed" }), "failed");
});

// Success requires BOTH updater success AND the version flip.
test("success requires the version to flip to the target", () => {
  // Updater says success but /api/version still reports the old version → wait.
  assert.equal(
    deriveOverlayPhase({
      ...base,
      updateState: "success",
      reportedVersion: "v1.2.0",
    }),
    "unreachable",
  );
  // Version flipped → success.
  assert.equal(
    deriveOverlayPhase({
      ...base,
      updateState: "success",
      reportedVersion: "v1.3.0",
    }),
    "success",
  );
});

test("success tolerates a missing 'v' prefix on either side", () => {
  assert.equal(
    deriveOverlayPhase({
      ...base,
      updateState: "success",
      reportedVersion: "1.3.0",
      targetVersion: "v1.3.0",
    }),
    "success",
  );
});

test("no/unknown updater state yet → preparing", () => {
  assert.equal(deriveOverlayPhase({ ...base, updateState: null }), "preparing");
});

test("versionsMatch normalizes the leading v and whitespace", () => {
  assert.equal(versionsMatch(" v1.3.0 ", "1.3.0"), true);
  assert.equal(versionsMatch("v1.3.0", "v1.3.1"), false);
  assert.equal(versionsMatch(null, "v1.3.0"), false);
});

test("overlayCopy renders the contract strings", () => {
  assert.equal(
    overlayCopy("pulling", "v1.2.0", "v1.3.0").sub,
    "Downloading v1.3.0…",
  );
  assert.equal(overlayCopy("success", "v1.2.0", "v1.3.0").heading, "Updated to v1.3.0");
  assert.match(overlayCopy("failed", "v1.2.0", "v1.3.0").sub, /rolled back to v1.2.0/);
  assert.equal(overlayCopy("unreachable", null, null).heading, "Almost back…");
});
