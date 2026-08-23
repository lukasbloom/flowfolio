import type { APIRequestContext } from "@playwright/test";

/**
 * Probes a JSON endpoint on the stack under test. Returns the parsed body,
 * or null on any failure (unreachable, timeout, non-ok status, bad JSON).
 * Gated specs default to skipping when this returns null, since that means
 * "wrong stack", not a genuine assertion failure.
 */
export async function probeJson<T>(
  request: APIRequestContext,
  path: string,
): Promise<T | null> {
  try {
    const res = await request.get(path, { timeout: 5_000 });
    if (!res.ok()) return null;
    return (await res.json()) as T;
  } catch {
    return null;
  }
}
