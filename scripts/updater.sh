#!/usr/bin/env sh
# Flowfolio self-update agent.
#
# Runs in the `updater` sidecar — the SAME flowfolio image, a different
# entrypoint. It is the ONLY component that holds the Docker socket. It watches
# a shared-volume request file the app writes, and drives a least-privilege
# update: pre-update snapshot -> pull -> recreate -> healthcheck -> success, or
# an automatic image-rollback (with a conditional forward-only DB restore) on a
# failed healthcheck. App<->updater is file-only; there is no listening socket.
#
# Shared-volume file contract (UPDATE_CHANNEL_DIR, default /update):
#   request.json (app -> updater): { request_id, target_version, requested_at }
#   status.json  (updater -> app): { request_id, state, message, log_tail, updated_at }
#   processed_id (updater-owned):  the request_id this updater has already claimed
#   state ::= preparing | pulling | restarting | success | failed
#
# Dedup channel separation: the app pre-seeds status.json with THIS run's
# request_id at request time (a fresh `preparing` for the UI). So the
# updater must NOT key "already processed" off status.json — that id is the app's,
# not proof the updater ran. It tracks claimed requests in its OWN processed_id
# file instead (see process_once).
#
# Safety invariants:
#   - target_version is NEVER interpolated into an image ref; the updater pulls
#     ONLY the compose-pinned `flowfolio` service (tag-injection-safe).
#   - The updater holds neither db_data nor backups; the pre-update snapshot and
#     the rollback restore run via `docker exec` INTO the app container.
#   - Recovery is forward-only: a failed migration is recovered by restoring the
#     pre-update snapshot + re-pinning the old image, never a schema downgrade.
#   - Reprocessing the same request_id is a no-op (idempotent re-attach).

set -eu

# ---- configuration (env-overridable; defaults match the compose service) ----
UPDATE_CHANNEL_DIR="${UPDATE_CHANNEL_DIR:-/update}"
COMPOSE_FILE="${COMPOSE_FILE:-/project/compose.yml}"
APP_SERVICE="${APP_SERVICE:-flowfolio}"
APP_IMAGE_REF="${APP_IMAGE_REF:-ghcr.io/lukasbloom/flowfolio:latest}"
APP_DB_PATH="${APP_DB_PATH:-/data/flowfolio.db}"
APP_HEALTHCHECK_URL="${APP_HEALTHCHECK_URL:-http://localhost:8080/api/healthcheck}"
HEALTHCHECK_TIMEOUT="${HEALTHCHECK_TIMEOUT:-90}"   # rollback gate window (s)
HEALTHCHECK_INTERVAL="${HEALTHCHECK_INTERVAL:-3}"  # poll interval (s)
POLL_INTERVAL="${POLL_INTERVAL:-5}"                # request-file watch interval (s)
UPDATER_LOG_FILE="${UPDATER_LOG_FILE:-/tmp/flowfolio-updater.log}"

REQUEST_FILE="${UPDATE_CHANNEL_DIR}/request.json"
STATUS_FILE="${UPDATE_CHANNEL_DIR}/status.json"
STATUS_TMP="${STATUS_FILE}.tmp"
# Updater-owned dedup marker: the request_id we have already claimed. Kept
# separate from status.json because the app pre-seeds status.json with this run's
# request_id before we ever see it — see process_once.
PROCESSED_FILE="${UPDATE_CHANNEL_DIR}/processed_id"
LOG_FILE="${UPDATER_LOG_FILE}"

REQUEST_ID=""
TARGET_VERSION=""

now_s() { date +%s; }

log() {
    _l="$(date -u +%H:%M:%SZ) ${1}"
    echo "${_l}"
    echo "${_l}" >> "${LOG_FILE}" 2>/dev/null || true
}

# JSON-safe single-line string: collapse newlines to " | ", escape \ and ".
json_escape() {
    printf '%s' "${1}" | tr '\n' '\036' \
        | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' -e 's/\036/ | /g'
}

# Naive string-field extractor for our own well-formed request/status files.
read_json_field() {
    sed -n 's/.*"'"${2}"'"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "${1}" 2>/dev/null | head -n1
}

write_status() {
    _state="${1}"
    _msg="${2}"
    _logtail="$(tail -n 20 "${LOG_FILE}" 2>/dev/null || true)"
    _now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    mkdir -p "${UPDATE_CHANNEL_DIR}"
    cat > "${STATUS_TMP}" <<EOF
{
  "request_id": "$(json_escape "${REQUEST_ID}")",
  "state": "$(json_escape "${_state}")",
  "message": "$(json_escape "${_msg}")",
  "log_tail": "$(json_escape "${_logtail}")",
  "updated_at": "${_now}"
}
EOF
    mv "${STATUS_TMP}" "${STATUS_FILE}"
    log "STATUS: ${_state} — ${_msg}"
}

# docker compose against the project compose file.
dc() { docker compose -f "${COMPOSE_FILE}" "$@"; }

app_container() { dc ps -q "${APP_SERVICE}" 2>/dev/null | head -n1; }

latest_preupdate_snapshot() {
    # Newest retention-exempt pre-update artifact, listed INSIDE the app container
    # (the updater does not mount /backups).
    docker exec "${1}" sh -c \
        'ls -1t /backups/preupdate/flowfolio-preupdate-*.db.age 2>/dev/null | head -n1' \
        2>/dev/null | head -n1
}

wait_healthy() {
    _deadline=$(( $(now_s) + HEALTHCHECK_TIMEOUT ))
    while [ "$(now_s)" -lt "${_deadline}" ]; do
        _cid="$(app_container || true)"
        if [ -n "${_cid}" ] \
           && docker exec "${_cid}" curl -fsS "${APP_HEALTHCHECK_URL}" >/dev/null 2>&1; then
            return 0
        fi
        sleep "${HEALTHCHECK_INTERVAL}"
    done
    return 1
}

# Auto image-rollback with conditional forward-only DB restore.
rollback() {
    _old_image="${1}"
    _old_head="${2}"
    log "ROLLBACK: re-pinning the previous image and recreating ${APP_SERVICE}."
    if [ -n "${_old_image}" ]; then
        docker tag "${_old_image}" "${APP_IMAGE_REF}" >> "${LOG_FILE}" 2>&1 \
            || log "WARN: could not re-pin the previous image."
    fi
    dc up -d "${APP_SERVICE}" >> "${LOG_FILE}" 2>&1 \
        || log "WARN: recreate during rollback reported an error."

    _cid="$(app_container || true)"
    _new_head=""
    if [ -n "${_cid}" ]; then
        _new_head="$(docker exec "${_cid}" sqlite3 "${APP_DB_PATH}" \
            'SELECT version_num FROM alembic_version' 2>/dev/null | head -n1 || true)"
    fi
    if [ -n "${_old_head}" ] && [ -n "${_new_head}" ] && [ "${_old_head}" != "${_new_head}" ]; then
        # Migrations ran during the failed attempt. Recover forward-only: restore
        # the pre-update snapshot. We NEVER run a schema down-migration.
        log "Schema advanced (${_old_head} -> ${_new_head}); restoring the pre-update snapshot."
        # Resolve the snapshot path while the app container is still up (the
        # listing runs via `exec` into it); the restore itself runs with the app
        # DOWN, below.
        _snap="$(latest_preupdate_snapshot "${_cid}")"
        if [ -n "${_snap}" ]; then
            # restore SAFELY. restore_local.sh does a plain `cp` over the
            # main DB file and documents that the caller MUST stop the app first —
            # overwriting an open SQLite file (with its -wal/-shm sidecars still
            # present to be replayed against the swapped file) is a classic
            # corruption path. So: stop the app, run the restore + clear the stale
            # WAL sidecars from a one-off container (no app holding the file open),
            # then force-recreate so the app reopens the restored DB cleanly.
            # `--entrypoint sh` bypasses the image's supervised entrypoint so the
            # one-off command runs directly.
            dc stop "${APP_SERVICE}" >> "${LOG_FILE}" 2>&1 || true
            if dc run --rm --no-deps -T --entrypoint sh "${APP_SERVICE}" -c \
                    "rm -f '${APP_DB_PATH}-wal' '${APP_DB_PATH}-shm'; \
                     sh /app/scripts/restore_local.sh '${_snap}' '${APP_DB_PATH}'" \
                    >> "${LOG_FILE}" 2>&1; then
                log "Pre-update snapshot restored."
            else
                log "ERROR: snapshot restore failed; manual recovery may be required."
            fi
            dc up -d --force-recreate "${APP_SERVICE}" >> "${LOG_FILE}" 2>&1 \
                || log "WARN: post-restore recreate reported an error."
        else
            log "ERROR: no pre-update snapshot found to restore."
        fi
    else
        log "No schema delta (old=${_old_head:-none} new=${_new_head:-none}); DB restore not required."
    fi
}

process_request() {
    REQUEST_ID="${1}"
    : > "${LOG_FILE}" 2>/dev/null || true
    log "Processing update request ${REQUEST_ID} (target=${TARGET_VERSION:-?})"

    # target_version is informational only (semver-validated for logging). It is
    # NEVER used to build an image ref — the pull is always compose-pinned.
    case "${TARGET_VERSION}" in
        v[0-9]*.[0-9]*.[0-9]*)
            log "target_version ${TARGET_VERSION} (informational; the pull is compose-pinned)." ;;
        *)
            log "WARN: target_version '${TARGET_VERSION:-}' is not vX.Y.Z; ignoring (pull stays compose-pinned)." ;;
    esac

    write_status "preparing" "Preparing update: recording the current version and taking a pre-update snapshot."

    APP_CID="$(app_container || true)"
    if [ -z "${APP_CID}" ]; then
        log "ERROR: could not resolve the running ${APP_SERVICE} container."
        write_status "failed" "Could not find the running Flowfolio container to update."
        return 1
    fi

    OLD_IMAGE_ID="$(docker inspect --format '{{.Image}}' "${APP_CID}" 2>/dev/null || true)"
    OLD_HEAD="$(docker exec "${APP_CID}" sqlite3 "${APP_DB_PATH}" \
        'SELECT version_num FROM alembic_version' 2>/dev/null | head -n1 || true)"
    log "Recorded current image=${OLD_IMAGE_ID:-unknown} schema=${OLD_HEAD:-unknown}."

    # Mandatory retention-exempt pre-update snapshot, via the app container
    # (the updater holds neither /data nor /backups).
    if docker exec -e BACKUP_LABEL=preupdate -e BACKUP_NO_PRUNE=1 "${APP_CID}" \
            sh /app/scripts/backup.sh >> "${LOG_FILE}" 2>&1; then
        log "Pre-update snapshot complete."
    else
        _rc=$?
        if [ "${_rc}" = "75" ]; then
            log "WARN: backup skipped (no BACKUP_ENCRYPTION_KEY); proceeding without a snapshot."
        else
            log "ERROR: pre-update snapshot failed (rc=${_rc}); aborting before any recreate."
            write_status "failed" "Pre-update snapshot failed; no changes were made."
            return 1
        fi
    fi

    write_status "pulling" "Downloading the new Flowfolio image."
    if ! dc pull "${APP_SERVICE}" >> "${LOG_FILE}" 2>&1; then
        log "ERROR: image pull failed."
        write_status "failed" "Failed to download the new image; your current version is unchanged."
        return 1
    fi

    write_status "restarting" "Restarting Flowfolio on the new version."
    if ! dc up -d "${APP_SERVICE}" >> "${LOG_FILE}" 2>&1; then
        log "ERROR: recreate failed; rolling back."
        rollback "${OLD_IMAGE_ID}" "${OLD_HEAD}"
        write_status "failed" "Failed to start the new version. Rolled back to the previous version."
        return 1
    fi

    if wait_healthy; then
        write_status "success" "Update complete. Flowfolio is running the new version."
        log "Update succeeded."
        return 0
    fi

    log "New version did not pass the healthcheck within ${HEALTHCHECK_TIMEOUT}s; rolling back."
    rollback "${OLD_IMAGE_ID}" "${OLD_HEAD}"
    write_status "failed" "The new version did not become healthy in time. Rolled back to the previous version."
    return 1
}

process_once() {
    [ -f "${REQUEST_FILE}" ] || return 0
    _req_id="$(read_json_field "${REQUEST_FILE}" request_id)"
    [ -n "${_req_id}" ] || return 0

    # Dedup on our OWN processed marker, NOT status.json: the app pre-seeds
    # status.json with this run's request_id at request time, so keying
    # off status.json would make every fresh request look already-done and the
    # updater would never run.
    _processed_id=""
    [ -f "${PROCESSED_FILE}" ] && _processed_id="$(cat "${PROCESSED_FILE}" 2>/dev/null | head -n1)"
    if [ "${_req_id}" = "${_processed_id}" ]; then
        return 0   # already processed by this updater, idempotent re-attach.
    fi

    # Claim BEFORE processing so a re-poll mid-run (or after) re-attaches as a no-op.
    printf '%s' "${_req_id}" > "${PROCESSED_FILE}" 2>/dev/null || true

    TARGET_VERSION="$(read_json_field "${REQUEST_FILE}" target_version)"
    process_request "${_req_id}" || true
}

main() {
    mkdir -p "${UPDATE_CHANNEL_DIR}"
    log "updater started (channel=${UPDATE_CHANNEL_DIR}, compose=${COMPOSE_FILE}, project=${COMPOSE_PROJECT_NAME:-unset})."
    if [ "${UPDATER_ONESHOT:-0}" = "1" ]; then
        process_once
        return 0
    fi
    while true; do
        process_once
        sleep "${POLL_INTERVAL}"
    done
}

main "$@"
