/** Render a version with a single leading "v"; pass non-numeric labels through. */
export function withV(version: string): string {
  return /^\d/.test(version) ? `v${version}` : version;
}

export interface UpdateActionableInput {
  /** True when the most recent daily release check failed. */
  checkFailed: boolean;
  /** True on a source-mounted development build (app_version === "dev"). */
  isDev: boolean;
  /** Latest known release, or null when unknown. */
  latestVersion: string | null;
  /** The running version. */
  currentVersion: string;
}

/** Parse "v1.2.3" / "1.2.3" into numeric parts, or null if not a dotted number. */
function parseVersion(version: string): number[] | null {
  const bare = version.replace(/^v/i, "");
  if (!/^\d+(?:\.\d+)*$/.test(bare)) return null;
  return bare.split(".").map(Number);
}

/**
 * True only when `latest` is a strictly newer release than `current`. Handles an
 * optional leading "v" on either side. Non-numeric versions (a dev build, a
 * malformed tag) return false so the prompt stays off rather than firing on a
 * string mismatch.
 */
export function isNewerVersion(latest: string, current: string): boolean {
  const a = parseVersion(latest);
  const b = parseVersion(current);
  if (a === null || b === null) return false;
  const len = Math.max(a.length, b.length);
  for (let i = 0; i < len; i++) {
    const x = a[i] ?? 0;
    const y = b[i] ?? 0;
    if (x !== y) return x > y;
  }
  return false;
}

/**
 * Whether Settings should offer the update guidance. False on a dev build
 * (no image to pull) or a failed check, and only true when a known release is
 * strictly newer than what is running. The banner keys off the server
 * `update_available` flag instead (which already forces False on dev); this
 * covers the Settings panel, which shows true availability and ignores banner
 * dismissal.
 */
export function updateActionable(input: UpdateActionableInput): boolean {
  return (
    !input.checkFailed &&
    !input.isDev &&
    input.latestVersion != null &&
    isNewerVersion(input.latestVersion, input.currentVersion)
  );
}
