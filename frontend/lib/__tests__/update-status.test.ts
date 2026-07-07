import { test } from "node:test";
import assert from "node:assert/strict";

// Explicit .ts extension: this dir is excluded from tsconfig and consumed only
// by Node's standalone runner (`node --test --experimental-strip-types`).
import { updateActionable } from "../update-status.ts";

// updateActionable: whether Settings should offer the "Update now" action.
const actionableBase = {
  checkFailed: false,
  isDev: false,
  latestVersion: "v1.3.0" as string | null,
  currentVersion: "v1.2.0",
};

test("updateActionable is true when a newer release exists on a release build", () => {
  assert.equal(updateActionable(actionableBase), true);
});

test("updateActionable is false on a dev build even with a newer latest", () => {
  assert.equal(updateActionable({ ...actionableBase, isDev: true }), false);
});

test("updateActionable is false when already on the latest", () => {
  assert.equal(
    updateActionable({ ...actionableBase, currentVersion: "v1.3.0" }),
    false,
  );
});

test("updateActionable is false when the check failed", () => {
  assert.equal(updateActionable({ ...actionableBase, checkFailed: true }), false);
});

test("updateActionable is false when there is no known latest", () => {
  assert.equal(updateActionable({ ...actionableBase, latestVersion: null }), false);
});
