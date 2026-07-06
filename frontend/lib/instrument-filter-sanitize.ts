/**
 * Drop persisted instrument-filter ids that are not in the known-instrument
 * set.
 *
 * A persisted net-worth filter (a cookie) can reference an instrument that has
 * since been deleted — or whose id changed after a DB restore — and a stale id
 * would scope the chart to a nonexistent instrument, rendering it empty with no
 * error. Returns a new array preserving input order; an all-stale filter
 * collapses to `[]` (the whole portfolio). Pure (no React) so it unit-tests in
 * the standalone `node --test` runner.
 */
export function sanitizeInstrumentFilter(
  ids: readonly string[],
  knownIds: ReadonlySet<string>,
): string[] {
  return ids.filter((id) => knownIds.has(id));
}
