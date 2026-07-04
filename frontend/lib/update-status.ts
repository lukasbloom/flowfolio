/**
 * Pure overlay state machine for the self-update flow.
 *
 * No React here. `deriveOverlayPhase` maps the backend `update_state` plus the
 * poll outcome to a view phase, and `overlayCopy` returns the heading/sub-text.
 * Kept side-effect-free so the mapping is unit-testable
 * (lib/__tests__/update-status.test.ts) without rendering.
 */

export type OverlayPhase =
  | "preparing"
  | "pulling"
  | "restarting"
  | "unreachable"
  | "success"
  | "failed";

export interface DeriveOverlayInput {
  /** The updater state from /api/update-status, or null when status.json is idle. */
  updateState: string | null;
  /** True when the most recent /api/update-status poll failed (network / 5xx). */
  pollFailed: boolean;
  /** The running version reported by /api/version (null while it is unreachable). */
  reportedVersion: string | null;
  /** The version this run is updating to. */
  targetVersion: string | null;
}

const SPINNER_STATES = new Set<string>(["preparing", "pulling", "restarting"]);

/** Compare versions ignoring a single leading "v" and surrounding whitespace. */
export function versionsMatch(a: string | null, b: string | null): boolean {
  if (a === null || b === null) return false;
  const norm = (v: string) => v.trim().replace(/^v/, "");
  return norm(a) === norm(b);
}

/**
 * Map backend state + poll outcome to the overlay view phase.
 *
 * Rules:
 *  - A failed status poll during the container recreate is EXPECTED: the
 *    backend is briefly down. It maps to `unreachable`, never `failed`.
 *  - `failed` from the updater maps straight to `failed`.
 *  - `success` requires BOTH the updater reporting success AND /api/version
 *    flipping to the target — until the new container answers we stay on the
 *    `unreachable` waiting state rather than reloading into the old version.
 */
export function deriveOverlayPhase(input: DeriveOverlayInput): OverlayPhase {
  const { updateState, pollFailed, reportedVersion, targetVersion } = input;

  if (pollFailed) return "unreachable";

  if (updateState === "failed") return "failed";

  if (updateState === "success") {
    return versionsMatch(reportedVersion, targetVersion) ? "success" : "unreachable";
  }

  if (updateState !== null && SPINNER_STATES.has(updateState)) {
    return updateState as OverlayPhase;
  }

  // No/unknown updater state yet (just submitted, status.json not written) →
  // show the first spinner step.
  return "preparing";
}

export interface OverlayCopy {
  heading: string;
  sub: string;
}

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

/** Heading + sub-text for each overlay phase. */
export function overlayCopy(
  phase: OverlayPhase,
  current: string | null,
  latest: string | null,
): OverlayCopy {
  const latestLabel = latest ? withV(latest) : "the new version";
  const currentLabel = current ? withV(current) : "your previous version";

  switch (phase) {
    case "preparing":
      return { heading: "Updating Flowfolio…", sub: "Backing up your data…" };
    case "pulling":
      return { heading: "Updating Flowfolio…", sub: `Downloading ${latestLabel}…` };
    case "restarting":
      return { heading: "Updating Flowfolio…", sub: "Restarting on the new version…" };
    case "unreachable":
      return {
        heading: "Almost back…",
        sub: "Flowfolio is restarting. This page will reload automatically.",
      };
    case "success":
      return { heading: `Updated to ${latestLabel}`, sub: "Reloading…" };
    case "failed":
      return {
        heading: "Update failed",
        sub: `Flowfolio rolled back to ${currentLabel}. Your data is safe.`,
      };
  }
}
