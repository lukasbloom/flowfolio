import { execFile } from "node:child_process";
import { promisify } from "node:util";
import path from "node:path";

const exec = promisify(execFile);

// Compose files are referenced relative to REPO_ROOT (the cwd for exec calls below)
const COMPOSE_FILES = ["-f", "compose.yml", "-f", "compose.test.yml"];
// The test stack now runs the SINGLE image, one `flowfolio` service /
// `flowfolio-flowfolio-1` container (was `api` / `flowfolio-api-1` on the old
// multi-service stack). Override the container name via FLOWFOLIO_TEST_API_CONTAINER
// if your Docker Compose project name differs.
const TEST_SERVICE = "flowfolio";
const API_CONTAINER =
  process.env.FLOWFOLIO_TEST_API_CONTAINER ?? "flowfolio-flowfolio-1";

// Reset budget: <= 200ms median over 10 trials.
// Primary path: `docker compose exec` (more portable; honours compose project naming).
// Fallback: `docker exec <container>` (bypasses compose; saves ~100ms fixed cost).
// The pilot spec's [bench] test measures median and asserts the budget. If primary
// median exceeds 200ms, set FLOWFOLIO_TEST_DB_RESET_DIRECT=1 to switch implementations
// without changing spec code.
const USE_DIRECT_DOCKER_EXEC = process.env.FLOWFOLIO_TEST_DB_RESET_DIRECT === "1";

// Resolve the repo root relative to this helper file (frontend/tests/e2e/helpers/ -> repo root)
const REPO_ROOT = path.resolve(__dirname, "../../../../");

// test_db_reset.sh resets the DB to golden AND re-applies the APP_PASSWORD
// pre-seed (the committed golden is unclaimed, and the lifespan pre-seed only
// runs once at boot — so the script re-claims the test password to keep the
// storageState session valid; see that script's comment).
export async function resetGoldenDb(): Promise<void> {
  if (USE_DIRECT_DOCKER_EXEC) {
    // Fallback path — direct `docker exec`, bypasses compose's ~100ms fixed cost.
    await exec("docker", ["exec", API_CONTAINER, "/app/scripts/test_db_reset.sh"]);
  } else {
    // Primary path — `docker compose exec` honours the active compose context.
    await exec("docker", ["compose", ...COMPOSE_FILES, "exec", "-T", TEST_SERVICE,
      "/app/scripts/test_db_reset.sh"], { cwd: REPO_ROOT });
  }
}
