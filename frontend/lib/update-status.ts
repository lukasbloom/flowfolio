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

/**
 * Whether Settings should offer the "Update now" action. False on a dev build
 * (no image to pull) or a failed check, and only true when a known release
 * differs from what is running. The banner keys off the server `update_available`
 * flag instead (which already forces False on dev); this covers the Settings
 * panel, which shows true availability and ignores banner dismissal.
 */
export function updateActionable(input: UpdateActionableInput): boolean {
  return (
    !input.checkFailed &&
    !input.isDev &&
    input.latestVersion != null &&
    input.latestVersion !== input.currentVersion
  );
}
