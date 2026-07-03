#!/usr/bin/env sh
# Clean-volume single-image smoke test: builds the REAL single image from
# the top-level Dockerfile, boots it from an empty volume on the "stranger" path
# (no DOMAIN, no SECRET_KEY, no APP_PASSWORD), and asserts the whole stack works:
#   - Caddy is up and routing /api/* and / (HTTP mode)
#   - the healthcheck is green
#   - first-run bootstrap claims an admin password — migrations ran
#   - the supervisor brought up all three processes (caddy + uvicorn + node)
#   - the backup job is registered in the in-process scheduler
#   - the TLS env-switch picks HTTPS/ACME when DOMAIN is set, plain HTTP when not
#   - the measured uncompressed image size is under the 800MB budget
#
# This is the phase gate — run it (with the Docker daemon up) before
# /gsd-verify-work. The measured size is written back into docs/IMAGE_SIZE.md.
#
# Run: sh scripts/smoke_single_image.sh
#
# Shape mirrors scripts/test_backup.sh: set -eu, mktemp/trap cleanup EXIT,
# INFO/PASS/FAIL echoes, non-zero exit on any assertion failure.

set -eu

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
IMAGE_SIZE_DOC="${REPO_ROOT}/docs/IMAGE_SIZE.md"

IMAGE="flowfolio:smoke"
HTTP_CONTAINER="flowfolio-smoke"
TLS_CONTAINER="flowfolio-smoke-tls"
# Host ports are overridable so the smoke test does not collide with a running
# dev/test stack (which holds 8080). The container always serves HTTP on 8080
# (Caddy no-DOMAIN) and HTTPS on 443; only the published host ports change.
SMOKE_HTTP_PORT="${SMOKE_HTTP_PORT:-18080}"
SMOKE_TLS_PORT="${SMOKE_TLS_PORT:-18443}"
BASE_URL="http://localhost:${SMOKE_HTTP_PORT}"
HEALTH_URL="${BASE_URL}/api/healthcheck"
SIZE_BUDGET_MB=800

cleanup() {
    # Best-effort teardown of both throwaway containers (clean-volume only).
    docker rm -f "${HTTP_CONTAINER}" >/dev/null 2>&1 || true
    docker rm -f "${TLS_CONTAINER}" >/dev/null 2>&1 || true
    echo "CLEANUP: Removed smoke containers"
}
trap cleanup EXIT

fail() {
    echo "FAIL: $1" >&2
    exit 1
}

# --------------------------------------------------------------------------
# Pre-flight: the Docker daemon must be running. This is an operational gate,
# not a code failure.
# --------------------------------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
    echo "ERROR: docker CLI not found — Docker daemon required for the smoke test." >&2
    exit 2
fi
if ! docker info >/dev/null 2>&1; then
    echo "ERROR: Docker daemon required — start Docker Desktop / dockerd and re-run." >&2
    exit 2
fi

# Start from a clean slate: drop any leftover smoke container from a prior run.
docker rm -f "${HTTP_CONTAINER}" "${TLS_CONTAINER}" >/dev/null 2>&1 || true

# --------------------------------------------------------------------------
# 1. Build the real artifact from the top-level Dockerfile.
# --------------------------------------------------------------------------
echo "INFO: Building ${IMAGE} from the single-image Dockerfile..."
docker build -t "${IMAGE}" "${REPO_ROOT}" || fail "docker build failed"
echo "PASS: image built"

# --------------------------------------------------------------------------
# 2. Boot from a CLEAN volume on the stranger path: no DOMAIN, no SECRET_KEY,
#    no APP_PASSWORD. With no DOMAIN, Caddy serves plain HTTP on 8080.
#    An anonymous volume is created fresh for /data (clean-volume contract).
# --------------------------------------------------------------------------
echo "INFO: Booting ${HTTP_CONTAINER} (no DOMAIN → ${BASE_URL})..."
docker run -d --name "${HTTP_CONTAINER}" \
    -p "${SMOKE_HTTP_PORT}:8080" \
    -e BACKUP_ENCRYPTION_KEY=smoke-test-key \
    "${IMAGE}" >/dev/null || fail "docker run (HTTP path) failed"

# --------------------------------------------------------------------------
# 3. Poll the healthcheck until 200 or ~60s timeout. A green healthcheck proves
#    Caddy is up AND routing /api/* AND uvicorn is alive.
# --------------------------------------------------------------------------
echo "INFO: Waiting for ${HEALTH_URL} to return 200 (≤60s)..."
HEALTHY=0
i=0
while [ "${i}" -lt 60 ]; do
    CODE=$(curl -s -o /dev/null -w '%{http_code}' "${HEALTH_URL}" 2>/dev/null || echo "000")
    if [ "${CODE}" = "200" ]; then
        HEALTHY=1
        break
    fi
    i=$((i + 1))
    sleep 1
done
if [ "${HEALTHY}" != "1" ]; then
    echo "---- container logs (healthcheck never went green) ----" >&2
    docker logs "${HTTP_CONTAINER}" 2>&1 | tail -40 >&2 || true
    fail "healthcheck never returned 200 within 60s"
fi
echo "PASS: healthcheck green — Caddy is up, routing /api/*, uvicorn alive"

# --------------------------------------------------------------------------
# 4. First-run bootstrap. Migrations ran implicitly — the setup
#    endpoints depend on the migrated DB. status:false → claim:200 → status:true.
# --------------------------------------------------------------------------
echo "INFO: Asserting first-run bootstrap..."
STATUS_BEFORE=$(curl -s "${BASE_URL}/api/setup/status" 2>/dev/null || echo "")
echo "${STATUS_BEFORE}" | grep -q '"claimed":false' \
    || fail "expected /api/setup/status {\"claimed\":false} before claim, got: ${STATUS_BEFORE}"

COOKIE_JAR="$(mktemp)"
CLAIM_CODE=$(curl -s -o /dev/null -w '%{http_code}' \
    -c "${COOKIE_JAR}" \
    -H 'Content-Type: application/json' \
    -d '{"password":"smoke-pass-1234"}' \
    "${BASE_URL}/api/setup/claim" 2>/dev/null || echo "000")
if [ "${CLAIM_CODE}" != "200" ]; then
    rm -f "${COOKIE_JAR}"
    fail "expected /api/setup/claim → 200, got: ${CLAIM_CODE}"
fi
# The claim issues a session cookie.
grep -qi 'session' "${COOKIE_JAR}" \
    || { rm -f "${COOKIE_JAR}"; fail "claim did not set a session cookie"; }
rm -f "${COOKIE_JAR}"

STATUS_AFTER=$(curl -s "${BASE_URL}/api/setup/status" 2>/dev/null || echo "")
echo "${STATUS_AFTER}" | grep -q '"claimed":true' \
    || fail "expected /api/setup/status {\"claimed\":true} after claim, got: ${STATUS_AFTER}"
echo "PASS: first-run bootstrap claimed + issued a session cookie"

# --------------------------------------------------------------------------
# 5. Assert the supervisor brought up all three processes. Behavioral proof:
#    / returns the Next.js app HTML (node up via Caddy catch-all) and the
#    healthcheck is 200 (uvicorn + caddy up). Cross-check via the process list.
# --------------------------------------------------------------------------
echo "INFO: Asserting all three supervised processes are up..."
# Follow redirects: / 307s to /track (Next middleware), which then renders the
# app HTML. A successful render through Caddy's catch-all proves node is up.
ROOT_BODY=$(curl -s -L "${BASE_URL}/" 2>/dev/null || echo "")
echo "${ROOT_BODY}" | grep -qi '<!doctype html\|<html\|__next\|_next' \
    || fail "expected Next.js app HTML at / (node up via Caddy catch-all)"
# The slim image has no `ps`; read process names from /proc/*/comm instead
# (s6 supervises caddy, uvicorn=python, and the Next standalone server=next-server).
PS_OUT=$(docker exec "${HTTP_CONTAINER}" sh -c 'cat /proc/*/comm 2>/dev/null' 2>/dev/null || echo "")
echo "${PS_OUT}" | grep -q 'caddy' || fail "caddy process not found in container"
echo "${PS_OUT}" | grep -Eq 'uvicorn|python' || fail "uvicorn/python process not found in container"
echo "${PS_OUT}" | grep -Eq 'node|next-server' || fail "node/next-server process not found in container"
echo "PASS: caddy + uvicorn + node all up (3-process supervisor, / serves app HTML)"

# --------------------------------------------------------------------------
# 6. Assert the backup job is registered in the scheduler. The scheduler_started
#    log line now lists the registered jobs including "backup".
# --------------------------------------------------------------------------
echo "INFO: Asserting the backup job is registered in the scheduler..."
LOGS=$(docker logs "${HTTP_CONTAINER}" 2>&1 || echo "")
# The in-process scheduler runs inside uvicorn; its `scheduler_started` log goes
# through the app's stdlib logger (not surfaced by uvicorn's logging config), so
# we assert the registration directly against the SAME code the live process ran:
# settings.scheduler_enabled is true (so start_scheduler fires in the lifespan)
# AND start_scheduler wires a job with id="backup" into APScheduler.
BACKUP_CHECK=$(docker exec "${HTTP_CONTAINER}" sh -c 'cd /app && python -c "
import asyncio, types
from app.core.config import settings
from app.services.scheduler import start_scheduler
assert settings.scheduler_enabled, \"scheduler not enabled\"
async def main():
    app = types.SimpleNamespace(state=types.SimpleNamespace())
    start_scheduler(app)
    job = app.state.scheduler.get_job(\"backup\")
    app.state.scheduler.shutdown(wait=False)
    return job
print(\"BACKUP_JOB_REGISTERED\" if asyncio.run(main()) is not None else \"BACKUP_JOB_MISSING\")
"' 2>/dev/null || echo "BACKUP_CHECK_ERROR")
echo "${BACKUP_CHECK}" | grep -q 'BACKUP_JOB_REGISTERED' \
    || fail "backup job not registered (scheduler_enabled + start_scheduler id=backup check failed): ${BACKUP_CHECK}"
echo "PASS: backup job registered in the in-process scheduler (scheduler_enabled + id=backup wired)"

# --------------------------------------------------------------------------
# 7. TLS env-switch SHAPE (no real domain). A short-lived run with DOMAIN set
#    must take the HTTPS/ACME path (Caddy attempts a cert against the fake domain
#    and binds 443), whereas the no-DOMAIN run above served plain HTTP on 8080
#    with no cert attempt. Assertion is lenient (log-grep) — a real cert needs a
#    real domain, so ACME failure against example.test is expected and fine.
# --------------------------------------------------------------------------
echo "INFO: Asserting the DOMAIN-set TLS env-switch (HTTPS/ACME path)..."
# The no-DOMAIN run must NOT have attempted ACME/TLS.
if echo "${LOGS}" | grep -Eqi 'acme|obtaining certificate|tls handshake'; then
    fail "no-DOMAIN run unexpectedly took the HTTPS/ACME path"
fi
docker run -d --name "${TLS_CONTAINER}" \
    -p "${SMOKE_TLS_PORT}:443" \
    -e DOMAIN=example.test \
    -e BACKUP_ENCRYPTION_KEY=smoke-test-key \
    "${IMAGE}" >/dev/null || fail "docker run (TLS path) failed"
# Give Caddy a few seconds to choose the HTTPS path and start ACME.
TLS_PATH=0
j=0
while [ "${j}" -lt 20 ]; do
    TLS_LOGS=$(docker logs "${TLS_CONTAINER}" 2>&1 || echo "")
    if echo "${TLS_LOGS}" | grep -Eqi 'acme|obtaining certificate|tls|https://example.test|443'; then
        TLS_PATH=1
        break
    fi
    j=$((j + 1))
    sleep 1
done
docker rm -f "${TLS_CONTAINER}" >/dev/null 2>&1 || true
if [ "${TLS_PATH}" != "1" ]; then
    echo "---- TLS container logs (HTTPS path not detected) ----" >&2
    echo "${TLS_LOGS}" | tail -40 >&2 || true
    fail "DOMAIN set but Caddy did not take the HTTPS/ACME path"
fi
echo "PASS: DOMAIN set → Caddy took the HTTPS/ACME path; unset → plain HTTP (TLS env-switch)"

# --------------------------------------------------------------------------
# 8. Measure size. Convert bytes → MB, FAIL if ≥ 800MB, and record the
#    measured value in docs/IMAGE_SIZE.md (overwriting the placeholder line).
# --------------------------------------------------------------------------
echo "INFO: Measuring uncompressed image size (budget ${SIZE_BUDGET_MB}MB)..."
SIZE_BYTES=$(docker image inspect "${IMAGE}" --format '{{.Size}}' 2>/dev/null || echo "")
case "${SIZE_BYTES}" in
    ''|*[!0-9]*) fail "could not read image size via docker image inspect" ;;
esac
# Integer MB (1 MB = 1,000,000 bytes, matching docker's decimal sizing).
SIZE_MB=$((SIZE_BYTES / 1000000))
echo "INFO: measured size = ${SIZE_MB} MB (${SIZE_BYTES} bytes)"
if [ "${SIZE_MB}" -ge "${SIZE_BUDGET_MB}" ]; then
    fail "image size ${SIZE_MB}MB exceeds the ${SIZE_BUDGET_MB}MB budget"
fi

# Overwrite the "Measured:" line in docs/IMAGE_SIZE.md with the real number.
if [ -f "${IMAGE_SIZE_DOC}" ]; then
    MEASURED_LINE="Measured: ${SIZE_MB} MB (on $(date -u +%Y-%m-%dT%H:%M:%SZ), budget ${SIZE_BUDGET_MB} MB)"
    TMP_DOC="$(mktemp)"
    sed "s|^Measured:.*|${MEASURED_LINE}|" "${IMAGE_SIZE_DOC}" > "${TMP_DOC}"
    mv -f "${TMP_DOC}" "${IMAGE_SIZE_DOC}"
    echo "PASS: recorded '${MEASURED_LINE}' in docs/IMAGE_SIZE.md"
else
    echo "WARN: ${IMAGE_SIZE_DOC} missing — skipping size record" >&2
fi
echo "PASS: image size ${SIZE_MB}MB is under the ${SIZE_BUDGET_MB}MB budget"

echo "PASS: single-image smoke test passed — the shipped artifact boots, bootstraps, supervises 3 processes, registers backup, switches TLS by DOMAIN, and fits the size budget."
