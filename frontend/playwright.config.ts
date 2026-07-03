import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright config for Flowfolio frontend e2e tests.
 *
 * Two projects:
 *   - "chromium": integration specs against the dev compose stack (http://localhost:8080)
 *   - "snapshots-chromium": snapshot specs against the hermetic test stack (http://localhost:8081)
 *
 * Start stacks before running:
 *   Dev:   docker compose -f compose.yml -f compose.dev.yml up -d
 *   Test:  docker compose -f compose.yml -f compose.test.yml up -d
 */
export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: false,          // Single-user app; tests share the same SQLite DB
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: 1,                    // SQLite single-writer; no concurrency
  reporter: "list",
  globalSetup: "./global-setup.ts",
  snapshotPathTemplate: "{snapshotDir}/{testFileName}/{arg}{ext}",

  use: {
    baseURL: process.env.PW_BASE_URL ?? "http://localhost:8080",
    trace: "on-first-retry",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },

  projects: [
    {
      name: "chromium",                     // existing — integration specs against dev stack
      testIgnore: /.*\.(snapshot|screenshots)\.spec\.ts/,
      use: { ...devices["Desktop Chrome"] },
    },
    {
      name: "snapshots-chromium",
      testMatch: /.*\.snapshot\.spec\.ts/,
      // Baselines live in tests/e2e/snapshots/__baselines__/<specFileName>/<name>.html
      // so the spec .ts file and the baseline directory do not share the same parent
      // path (which would cause EEXIST trying to mkdir a name that is already a file).
      snapshotDir: "./tests/e2e/snapshots/__baselines__",
      use: {
        ...devices["Desktop Chrome"],
        timezoneId: "UTC",
        locale: "en-GB",                    // match project formatRelativeHours decisions
        storageState: "tests/e2e/.auth/storageState.json",
        baseURL: process.env.PW_BASE_URL ?? "http://localhost:8081",
      },
    },
    {
      // Marketing-screenshot capture (05-01). Distinct testMatch (*.screenshots.spec.ts)
      // so it never collides with the *.snapshot.spec.ts baseline assertions. Reuses the
      // snapshots-chromium settings (golden seed, frozen clock, UTC, en-GB, test stack
      // storageState) but writes published PNGs to docs/screenshots/ instead of asserting.
      // Per-test viewport (desktop 1440x900 vs mobile 390x844) is set inside the spec.
      name: "marketing-chromium",
      testMatch: /.*\.screenshots\.spec\.ts/,
      use: {
        ...devices["Desktop Chrome"],
        timezoneId: "UTC",
        locale: "en-GB",
        storageState: "tests/e2e/.auth/storageState.json",
        baseURL: process.env.PW_BASE_URL ?? "http://localhost:8081",
      },
    },
  ],
});
