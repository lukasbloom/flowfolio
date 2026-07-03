#!/usr/bin/env sh
# Restore a Flowfolio backup from off-host storage.
# Usage: ./restore.sh <backup-filename> <restore-target-path>
# Example: ./restore.sh flowfolio-2026-04-30T020000Z.db.age /tmp/restored.db
#
# Required env vars:
#   BACKUP_DEST           rclone source (same as backup destination)
#   BACKUP_ENCRYPTION_KEY symmetric passphrase used during backup

set -eu

BACKUP_NAME="${1:-}"
RESTORE_TARGET="${2:-}"

if [ -z "${BACKUP_NAME}" ] || [ -z "${RESTORE_TARGET}" ]; then
    echo "Usage: $0 <backup-filename> <restore-target-path>" >&2
    echo "Example: $0 flowfolio-2026-04-30T020000Z.db.age /tmp/restored.db" >&2
    exit 1
fi

if [ -z "${BACKUP_DEST:-}" ]; then
    echo "ERROR: BACKUP_DEST is not set." >&2
    exit 1
fi

if [ -z "${BACKUP_ENCRYPTION_KEY:-}" ]; then
    echo "ERROR: BACKUP_ENCRYPTION_KEY is not set." >&2
    exit 1
fi

TEMP_DIR="$(mktemp -d)"
TEMP_ENCRYPTED="${TEMP_DIR}/${BACKUP_NAME}"

cleanup() {
    rm -rf "${TEMP_DIR}"
}
trap cleanup EXIT

echo "INFO: Downloading ${BACKUP_NAME} from ${BACKUP_DEST}..."
rclone copy "${BACKUP_DEST}${BACKUP_NAME}" "${TEMP_DIR}/" --progress

if [ ! -f "${TEMP_ENCRYPTED}" ]; then
    echo "ERROR: Downloaded file not found at ${TEMP_ENCRYPTED}" >&2
    exit 1
fi

echo "INFO: Decrypting backup..."
# age reads passphrase from /dev/tty — we use `script` to allocate a pty and
# pipe the passphrase into it. The passphrase is held in an env var so it
# never appears in `ps` output. Decrypt only prompts once.
AGE_PASSPHRASE="${BACKUP_ENCRYPTION_KEY}" \
    sh -c 'printf "%s\n" "${AGE_PASSPHRASE}" | \
           script -q -c "age --decrypt --output \"$1\" \"$2\"" /dev/null >/dev/null' \
    _ "${RESTORE_TARGET}" "${TEMP_ENCRYPTED}"
if [ ! -s "${RESTORE_TARGET}" ]; then
    echo "ERROR: age decryption produced no output — wrong passphrase, or 'age'/'script' missing" >&2
    exit 1
fi

echo "INFO: Verifying restored database..."
if sqlite3 "${RESTORE_TARGET}" "PRAGMA integrity_check;" | grep -q "^ok$"; then
    echo "INFO: Database integrity check: OK"
else
    echo "ERROR: Database integrity check FAILED" >&2
    exit 1
fi

echo "INFO: Restore complete: ${RESTORE_TARGET}"
echo "WARN: The app is still running against the OLD database. To switch:"
echo "WARN:   1. Stop the api container: docker compose stop api"
echo "WARN:   2. Copy restored DB to volume: docker cp ${RESTORE_TARGET} flowfolio-api-1:/data/flowfolio.db"
echo "WARN:   3. Restart: docker compose start api"
