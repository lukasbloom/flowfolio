/**
 * Delete-transaction dialog body copy.
 *
 * Extracted into a pure, JSX-free module so the honest soft-delete wording can
 * be (a) the single source of truth rendered by DeleteConfirmDialog.tsx and
 * (b) asserted by a node:test regression guard that must NOT import React/JSX
 * (the test runner uses `node --test --experimental-strip-types`, which strips
 * types but cannot transform JSX).
 *
 * The audit found the misleading "restore from audit history" wording was
 * ALREADY removed in v1.x — this constant pins the honest copy so that
 * wording cannot return. The regression test in
 * lib/__tests__/format-precision.test.ts asserts this string contains
 * "Soft-deleted" and does NOT contain "restore".
 */
export const DELETE_DIALOG_SOFT_DELETE_COPY_LEAD =
  "Soft-deleted transactions stay on file with full history but are excluded from analytics. Toggle";

export const DELETE_DIALOG_SHOW_DELETED_LABEL = "Show deleted";

export const DELETE_DIALOG_SOFT_DELETE_COPY_TAIL = "in the ledger to view them.";

/**
 * The full body copy as a single plain string, for the regression assertion.
 * DeleteConfirmDialog.tsx renders the three parts above with an emphasized
 * "Show deleted" span between them; this concatenation reconstructs the
 * rendered sentence for substring checks.
 */
export const DELETE_DIALOG_SOFT_DELETE_COPY = `${DELETE_DIALOG_SOFT_DELETE_COPY_LEAD} ${DELETE_DIALOG_SHOW_DELETED_LABEL} ${DELETE_DIALOG_SOFT_DELETE_COPY_TAIL}`;
