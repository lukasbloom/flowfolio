#!/usr/bin/env sh
# Decrypt a LOCAL encrypted snapshot into a target DB path (rollback restore).
# Usage: ./restore_local.sh <encrypted-snapshot-path> <target-db-path>
# Example: ./restore_local.sh /backups/preupdate/flowfolio-preupdate-….db.age /data/flowfolio.db
#
# Unlike restore.sh (which pulls from an off-host rclone remote), this decrypts a
# snapshot that already exists on the local filesystem. It is a manual
# local-recovery helper, run via `docker exec` into the app container to
# restore a known-good snapshot.
#
# Required env:
#   BACKUP_ENCRYPTION_KEY  symmetric age passphrase used during backup

set -eu

ENCRYPTED_PATH="${1:-}"
TARGET_DB="${2:-}"

if [ -z "${ENCRYPTED_PATH}" ] || [ -z "${TARGET_DB}" ]; then
    echo "Usage: $0 <encrypted-snapshot-path> <target-db-path>" >&2
    exit 1
fi

if [ -z "${BACKUP_ENCRYPTION_KEY:-}" ]; then
    echo "ERROR: BACKUP_ENCRYPTION_KEY is not set." >&2
    exit 1
fi

if [ ! -f "${ENCRYPTED_PATH}" ]; then
    echo "ERROR: encrypted snapshot not found at ${ENCRYPTED_PATH}" >&2
    exit 1
fi

TEMP_DIR="$(mktemp -d)"
TEMP_PLAIN="${TEMP_DIR}/restored.db"

cleanup() {
    rm -rf "${TEMP_DIR}"
}
trap cleanup EXIT

echo "INFO: Decrypting ${ENCRYPTED_PATH}..."
# age reads the passphrase from /dev/tty — pipe via `script` to allocate a pty
# (same convention backup.sh/restore.sh use). The passphrase is held in an env
# var so it never appears in `ps`. Decrypt prompts once.
AGE_PASSPHRASE="${BACKUP_ENCRYPTION_KEY}" \
    sh -c 'printf "%s\n" "${AGE_PASSPHRASE}" | \
           script -q -c "age --decrypt --output \"$1\" \"$2\"" /dev/null >/dev/null' \
    _ "${TEMP_PLAIN}" "${ENCRYPTED_PATH}"
if [ ! -s "${TEMP_PLAIN}" ]; then
    echo "ERROR: age decryption produced no output — wrong passphrase, or 'age'/'script' missing" >&2
    exit 1
fi

echo "INFO: Verifying restored database integrity..."
if sqlite3 "${TEMP_PLAIN}" "PRAGMA integrity_check;" | grep -q "^ok$"; then
    echo "INFO: Database integrity check: OK"
else
    echo "ERROR: Database integrity check FAILED — refusing to overwrite ${TARGET_DB}" >&2
    exit 1
fi

# Land the verified plaintext DB at the target path (caller stops the app around
# this so a plain cp is safe). cp (not mv) tolerates a cross-filesystem temp dir.
mkdir -p "$(dirname "${TARGET_DB}")"
cp "${TEMP_PLAIN}" "${TARGET_DB}"
echo "INFO: Restored database written to ${TARGET_DB}"
