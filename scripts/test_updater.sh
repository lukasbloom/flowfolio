#!/usr/bin/env sh
# Dry-run test for scripts/updater.sh. Prepends a FAKE `docker` to PATH that
# records every invocation and returns canned output (health switchable via
# FAKE_HEALTHY). No real Docker, containers, or images are touched.
#
# Proves:
#   (a) the happy path transitions preparing -> pulling -> restarting -> success;
#   (b) the updater issues `compose ... pull flowfolio` + `compose ... up -d
#       flowfolio` and NEVER a pull of a tag derived from target_version;
#   (c) the unhealthy path ends `failed` after a digest re-pin + recreate.
#
# Run: sh scripts/test_updater.sh

set -eu

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
UPDATER_SH="${SCRIPT_DIR}/updater.sh"

TEMP_DIR="$(mktemp -d)"
FAKE_BIN="${TEMP_DIR}/bin"
CHANNEL="${TEMP_DIR}/update"
DOCKER_CALLS="${TEMP_DIR}/docker-calls.log"
mkdir -p "${FAKE_BIN}" "${CHANNEL}"
: > "${DOCKER_CALLS}"

cleanup() {
    rm -rf "${TEMP_DIR}"
    echo "CLEANUP: Removed ${TEMP_DIR}"
}
trap cleanup EXIT

export DOCKER_CALLS

# ---- fake docker shim ----------------------------------------------------
cat > "${FAKE_BIN}/docker" <<'SHIM'
#!/usr/bin/env sh
# Record the full invocation, then emit canned output per subcommand.
echo "docker $*" >> "${DOCKER_CALLS}"

case "${1}" in
  compose)
    shift
    while [ "${1:-}" = "-f" ]; do shift 2; done
    case "${1:-}" in
      ps)   echo "fakeappcid0001" ;;   # ps -q flowfolio -> a stable fake id
      pull) : ;;                        # pull flowfolio
      up)   : ;;                        # up -d flowfolio
      *)    : ;;
    esac
    ;;
  inspect)
    echo "sha256:oldimageid0001" ;;     # current image digest
  exec)
    shift
    while [ "${1:-}" = "-e" ]; do shift 2; done
    shift                               # drop the container id
    case "$*" in
      *sqlite3*alembic_version*)
        # Optional schema-delta simulation: when SCHEMA_DELTA_COUNTER points at a
        # counter file, the FIRST read (the recorded old head) returns 0001 and
        # every LATER read (the post-rollback new head) returns 0002, so rollback()
        # takes the schema-advanced restore branch. Without it the head is stable.
        if [ -n "${SCHEMA_DELTA_COUNTER:-}" ]; then
          _n="$(cat "${SCHEMA_DELTA_COUNTER}" 2>/dev/null || echo 0)"
          _n=$((_n + 1)); echo "${_n}" > "${SCHEMA_DELTA_COUNTER}"
          [ "${_n}" -le 1 ] && echo "head_rev_0001" || echo "head_rev_0002"
        else
          echo "head_rev_0001"
        fi ;;
      *preupdate*) echo "/backups/preupdate/flowfolio-preupdate-fake.db.age" ;;
      *curl*healthcheck*) [ "${FAKE_HEALTHY:-0}" = "1" ] && exit 0 || exit 1 ;;
      *backup.sh*) exit 0 ;;
      *restore_local.sh*) exit 0 ;;
      *) : ;;
    esac
    ;;
  tag) : ;;
  *)   : ;;
esac
exit 0
SHIM
chmod +x "${FAKE_BIN}/docker"

write_request() {
    cat > "${CHANNEL}/request.json" <<EOF
{ "request_id": "${1}", "target_version": "${2}", "requested_at": "2026-06-26T00:00:00Z" }
EOF
}

run_updater() {
    UPDATER_ONESHOT=1 \
    UPDATE_CHANNEL_DIR="${CHANNEL}" \
    COMPOSE_FILE="${TEMP_DIR}/compose.yml" \
    APP_IMAGE_REF="ghcr.io/lukasbloom/flowfolio:latest" \
    UPDATER_LOG_FILE="${TEMP_DIR}/updater.log" \
    HEALTHCHECK_TIMEOUT="${1}" \
    HEALTHCHECK_INTERVAL=1 \
    PATH="${FAKE_BIN}:${PATH}" \
    sh "${UPDATER_SH}"
}

status_state() { sed -n 's/.*"state"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "${CHANNEL}/status.json" | head -n1; }

# ==========================================================================
# (1) Happy path — target_version is a tag that must NEVER be pulled.
# ==========================================================================
echo "TEST: healthy update path..."
export FAKE_HEALTHY=1
write_request "req-healthy-001" "v9.9.9"
HAPPY_OUT="${TEMP_DIR}/happy.out"
run_updater 4 > "${HAPPY_OUT}" 2>&1

# (a) transitions in order, ending at success
for st in preparing pulling restarting success; do
    if ! grep -q "STATUS: ${st}" "${HAPPY_OUT}"; then
        echo "FAIL: missing status transition '${st}'" >&2; cat "${HAPPY_OUT}" >&2; exit 1
    fi
done
LAST_STATUS="$(grep 'STATUS:' "${HAPPY_OUT}" | tail -n1)"
case "${LAST_STATUS}" in
  *success*) : ;;
  *) echo "FAIL: last status was not success: ${LAST_STATUS}" >&2; exit 1 ;;
esac
if [ "$(status_state)" != "success" ]; then
    echo "FAIL: status.json state is not 'success' (got '$(status_state)')" >&2; exit 1
fi
echo "PASS: healthy path transitioned preparing->pulling->restarting->success."

# (b) exactly the compose-pinned pull + recreate, and NO target_version tag pull
if ! grep -q 'compose .* pull flowfolio' "${DOCKER_CALLS}"; then
    echo "FAIL: expected 'compose ... pull flowfolio' in docker calls" >&2; cat "${DOCKER_CALLS}" >&2; exit 1
fi
if ! grep -q 'compose .* up -d flowfolio' "${DOCKER_CALLS}"; then
    echo "FAIL: expected 'compose ... up -d flowfolio' in docker calls" >&2; cat "${DOCKER_CALLS}" >&2; exit 1
fi
if grep -Eq 'pull[^\n]*9\.9\.9' "${DOCKER_CALLS}"; then
    echo "FAIL: updater pulled a tag derived from target_version (tag-injection!)" >&2; cat "${DOCKER_CALLS}" >&2; exit 1
fi
# Every pull must be of the bare service name, never an image ref.
if grep -E 'pull ' "${DOCKER_CALLS}" | grep -vq 'pull flowfolio'; then
    echo "FAIL: a pull targeted something other than the 'flowfolio' service" >&2; cat "${DOCKER_CALLS}" >&2; exit 1
fi
echo "PASS: only the compose-pinned 'flowfolio' service was pulled (no tag injection)."

# ==========================================================================
# (2) Unhealthy path — must roll back: re-pin old digest + recreate, end failed.
# ==========================================================================
echo "TEST: unhealthy update path (rollback)..."
: > "${DOCKER_CALLS}"
export FAKE_HEALTHY=0
write_request "req-unhealthy-001" "v9.9.9"
UNHEALTHY_OUT="${TEMP_DIR}/unhealthy.out"
run_updater 2 > "${UNHEALTHY_OUT}" 2>&1

if [ "$(status_state)" != "failed" ]; then
    echo "FAIL: unhealthy path must end 'failed' (got '$(status_state)')" >&2; cat "${UNHEALTHY_OUT}" >&2; exit 1
fi
if ! grep -q 'tag sha256:oldimageid0001 ghcr.io/lukasbloom/flowfolio:latest' "${DOCKER_CALLS}"; then
    echo "FAIL: rollback did not re-pin the recorded old image digest" >&2; cat "${DOCKER_CALLS}" >&2; exit 1
fi
UP_COUNT="$(grep -c 'compose .* up -d flowfolio' "${DOCKER_CALLS}")"
if [ "${UP_COUNT}" -lt 2 ]; then
    echo "FAIL: expected >=2 'up -d flowfolio' (initial recreate + rollback recreate), got ${UP_COUNT}" >&2; cat "${DOCKER_CALLS}" >&2; exit 1
fi
echo "PASS: unhealthy path re-pinned the old digest, recreated, and ended 'failed'."

# ==========================================================================
# (3) Unhealthy + schema advanced — the rollback DB restore MUST run with the
#     app STOPPED, clear stale -wal/-shm sidecars, and force-recreate so the app
#     reopens the restored file (data-safety: never cp over an open DB).
# ==========================================================================
echo "TEST: unhealthy path with a schema delta (safe DB restore)..."
: > "${DOCKER_CALLS}"
export FAKE_HEALTHY=0
SCHEMA_COUNTER_FILE="${TEMP_DIR}/schema-counter"
: > "${SCHEMA_COUNTER_FILE}"
export SCHEMA_DELTA_COUNTER="${SCHEMA_COUNTER_FILE}"
write_request "req-schemadelta-001" "v9.9.9"
DELTA_OUT="${TEMP_DIR}/delta.out"
run_updater 2 > "${DELTA_OUT}" 2>&1
unset SCHEMA_DELTA_COUNTER

if [ "$(status_state)" != "failed" ]; then
    echo "FAIL: schema-delta path must end 'failed' (got '$(status_state)')" >&2; cat "${DELTA_OUT}" >&2; exit 1
fi
if ! grep -q 'compose .* stop flowfolio' "${DOCKER_CALLS}"; then
    echo "FAIL: restore did not stop the app before overwriting the DB" >&2; cat "${DOCKER_CALLS}" >&2; exit 1
fi
if ! grep -q 'compose .* run .* flowfolio' "${DOCKER_CALLS}"; then
    echo "FAIL: restore did not run from a one-off (app-down) container" >&2; cat "${DOCKER_CALLS}" >&2; exit 1
fi
if ! grep -q 'flowfolio.db-wal' "${DOCKER_CALLS}"; then
    echo "FAIL: restore did not clear the stale -wal sidecar" >&2; cat "${DOCKER_CALLS}" >&2; exit 1
fi
if ! grep -q 'compose .* up -d --force-recreate flowfolio' "${DOCKER_CALLS}"; then
    echo "FAIL: restore did not force-recreate the app onto the restored DB" >&2; cat "${DOCKER_CALLS}" >&2; exit 1
fi
# Ordering: stop BEFORE the one-off restore BEFORE the force-recreate.
STOP_LINE="$(grep -n 'compose .* stop flowfolio' "${DOCKER_CALLS}" | head -n1 | cut -d: -f1)"
RUN_LINE="$(grep -n 'compose .* run .* flowfolio' "${DOCKER_CALLS}" | head -n1 | cut -d: -f1)"
FR_LINE="$(grep -n 'compose .* up -d --force-recreate flowfolio' "${DOCKER_CALLS}" | head -n1 | cut -d: -f1)"
if [ "${STOP_LINE}" -ge "${RUN_LINE}" ] || [ "${RUN_LINE}" -ge "${FR_LINE}" ]; then
    echo "FAIL: restore order must be stop -> run -> force-recreate (got ${STOP_LINE}/${RUN_LINE}/${FR_LINE})" >&2; cat "${DOCKER_CALLS}" >&2; exit 1
fi
echo "PASS: schema-delta rollback stopped the app, cleared WAL, restored, and force-recreated."

# ==========================================================================
# (4) regression guard — the app pre-seeds status.json with THIS run's
#     request_id BEFORE the updater sees the request (update_apply
#     ._write_preparing_status). The updater must STILL process it: dedup is
#     keyed on its own processed_id marker, not status.json. (With the old
#     status.json-keyed dedup this wedged at 'preparing' with no docker calls.)
# ==========================================================================
echo "TEST: regression — pre-seeded status.json must not wedge the updater..."
: > "${DOCKER_CALLS}"
export FAKE_HEALTHY=1
PRESEED_RID="req-preseeded-001"
write_request "${PRESEED_RID}" "v9.9.9"
# Mirror update_apply._write_preparing_status: same request_id, state preparing.
cat > "${CHANNEL}/status.json" <<EOF
{ "request_id": "${PRESEED_RID}", "state": "preparing", "message": "Preparing the update.", "log_tail": null, "updated_at": "2026-06-26T00:00:00Z" }
EOF
# No prior claim for this id.
rm -f "${CHANNEL}/processed_id"
PRESEED_OUT="${TEMP_DIR}/preseed.out"
run_updater 4 > "${PRESEED_OUT}" 2>&1

if [ "$(status_state)" != "success" ]; then
    echo "FAIL: pre-seeded status.json wedged the updater (state='$(status_state)', expected success)" >&2; cat "${PRESEED_OUT}" >&2; exit 1
fi
if ! grep -q 'compose .* pull flowfolio' "${DOCKER_CALLS}"; then
    echo "FAIL: updater did not act on the pre-seeded request (no pull)" >&2; cat "${DOCKER_CALLS}" >&2; exit 1
fi
echo "PASS: pre-seeded status.json did not wedge the updater (regression guard)."

# ==========================================================================
# (5) Idempotent re-attach — now that the updater has recorded req-preseeded-001
#     in processed_id, re-running the SAME request.json is a no-op.
# ==========================================================================
echo "TEST: idempotent re-attach (same request_id) is a no-op..."
: > "${DOCKER_CALLS}"
REATTACH_OUT="${TEMP_DIR}/reattach.out"
run_updater 4 > "${REATTACH_OUT}" 2>&1
if [ -s "${DOCKER_CALLS}" ]; then
    echo "FAIL: re-running the same request_id triggered docker calls (not a no-op)" >&2; cat "${DOCKER_CALLS}" >&2; exit 1
fi
echo "PASS: re-running the same request_id was a no-op (re-attach)."

echo "PASS: updater dry-run test passed (happy + rollback + regression paths)."
