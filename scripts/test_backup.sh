#!/usr/bin/env sh
# Smoke test: creates a minimal SQLite DB, backs it up locally, restores, verifies.
# Requires: sqlite3, age — does NOT require rclone (uses local filesystem as BACKUP_DEST).
# Run: sh scripts/test_backup.sh

set -eu

TEMP_DIR="$(mktemp -d)"
TEST_DB="${TEMP_DIR}/test.db"
TEST_BACKUP_DIR="${TEMP_DIR}/backups/"
TEST_RESTORE="${TEMP_DIR}/restored.db"
TEST_KEY="test-encryption-key-$(date +%s)"

mkdir -p "${TEST_BACKUP_DIR}"

cleanup() {
    rm -rf "${TEMP_DIR}"
    echo "CLEANUP: Removed ${TEMP_DIR}"
}
trap cleanup EXIT

# Pre-flight: required tools (age needs `script` from util-linux for non-interactive use)
for tool in sqlite3 age script; do
    if ! command -v "${tool}" >/dev/null 2>&1; then
        echo "ERROR: required tool not found: ${tool}" >&2
        echo "       Install with: 'apt-get install age util-linux sqlite3' (Debian)" >&2
        echo "                  or 'brew install age util-linux sqlite' (macOS)" >&2
        exit 1
    fi
done

echo "TEST: Creating sample SQLite database..."
sqlite3 "${TEST_DB}" "
CREATE TABLE test_table (id INTEGER PRIMARY KEY, value TEXT);
INSERT INTO test_table VALUES (1, 'hello');
INSERT INTO test_table VALUES (2, 'world');
"

echo "TEST: Running backup..."
TIMESTAMP="$(date -u +%Y-%m-%dT%H%M%SZ)"
BACKUP_NAME="flowfolio-test-${TIMESTAMP}.db.age"
TEMP_BACKUP="${TEMP_DIR}/flowfolio.db"

# Step 1: Hot backup (same call backup.sh makes)
sqlite3 "${TEST_DB}" ".backup '${TEMP_BACKUP}'"

# Step 2: Encrypt with age (same call backup.sh makes — see backup.sh for why
# we use `script` here rather than a plain stdin pipe).
AGE_PASSPHRASE="${TEST_KEY}" \
    sh -c 'printf "%s\n%s\n" "${AGE_PASSPHRASE}" "${AGE_PASSPHRASE}" | \
           script -q -c "age --encrypt --passphrase --output \"$1\" \"$2\"" /dev/null >/dev/null' \
    _ "${TEST_BACKUP_DIR}${BACKUP_NAME}" "${TEMP_BACKUP}"

if [ ! -s "${TEST_BACKUP_DIR}${BACKUP_NAME}" ]; then
    echo "FAIL: age encryption produced no output" >&2
    exit 1
fi

echo "TEST: Backup created at ${TEST_BACKUP_DIR}${BACKUP_NAME}"

echo "TEST: Decrypting and restoring..."
AGE_PASSPHRASE="${TEST_KEY}" \
    sh -c 'printf "%s\n" "${AGE_PASSPHRASE}" | \
           script -q -c "age --decrypt --output \"$1\" \"$2\"" /dev/null >/dev/null' \
    _ "${TEST_RESTORE}" "${TEST_BACKUP_DIR}${BACKUP_NAME}"

echo "TEST: Verifying restored data..."
INTEGRITY=$(sqlite3 "${TEST_RESTORE}" "PRAGMA integrity_check;")
if [ "${INTEGRITY}" != "ok" ]; then
    echo "FAIL: Integrity check failed: ${INTEGRITY}" >&2
    exit 1
fi

ROW_COUNT=$(sqlite3 "${TEST_RESTORE}" "SELECT COUNT(*) FROM test_table;")
if [ "${ROW_COUNT}" != "2" ]; then
    echo "FAIL: Expected 2 rows, got ${ROW_COUNT}" >&2
    exit 1
fi

VALUE=$(sqlite3 "${TEST_RESTORE}" "SELECT value FROM test_table WHERE id=1;")
if [ "${VALUE}" != "hello" ]; then
    echo "FAIL: Expected 'hello', got '${VALUE}'" >&2
    exit 1
fi

echo "PASS: Round-trip encrypt/restore smoke test passed."

# --------------------------------------------------------------------------
# Local-default contract: backup.sh writes an encrypted local snapshot
# to BACKUP_DIR with the key set and BACKUP_DEST unset, and skips cleanly
# (exit 75 — the BACKUP_SKIP_EXIT_CODE skip contract and no .db.age)
# with the key unset.
BACKUP_SKIP_EXIT_CODE=75
# --------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKUP_SH="${SCRIPT_DIR}/backup.sh"

# (a) Key set, BACKUP_DEST unset → local snapshot produced, exit 0.
echo "TEST: backup.sh local-default (key set, BACKUP_DEST unset)..."
LOCAL_BACKUP_DIR="${TEMP_DIR}/local-backups"
DB_PATH="${TEST_DB}" \
    BACKUP_DIR="${LOCAL_BACKUP_DIR}" \
    BACKUP_ENCRYPTION_KEY="${TEST_KEY}" \
    sh "${BACKUP_SH}"
# shellcheck disable=SC2012
SNAPSHOT_COUNT=$(find "${LOCAL_BACKUP_DIR}" -name 'flowfolio-*.db.age' | wc -l | tr -d ' ')
if [ "${SNAPSHOT_COUNT}" != "1" ]; then
    echo "FAIL: expected exactly 1 local snapshot in BACKUP_DIR, got ${SNAPSHOT_COUNT}" >&2
    exit 1
fi
echo "PASS: local-default produced a single encrypted snapshot."

# (b) Key unset → skip path, exit 75 (BACKUP_SKIP_EXIT_CODE), NO .db.age written.
echo "TEST: backup.sh skip-without-key (BACKUP_ENCRYPTION_KEY unset)..."
SKIP_BACKUP_DIR="${TEMP_DIR}/skip-backups"
mkdir -p "${SKIP_BACKUP_DIR}"
# Run in a subshell with the key explicitly cleared so it skips and writes nothing.
# The skip path now exits nonzero (75), so capture the code without tripping
# `set -e` by swallowing the failure in the condition itself.
SKIP_EXIT=0
( unset BACKUP_ENCRYPTION_KEY
  DB_PATH="${TEST_DB}" BACKUP_DIR="${SKIP_BACKUP_DIR}" sh "${BACKUP_SH}" ) || SKIP_EXIT=$?
if [ "${SKIP_EXIT}" != "${BACKUP_SKIP_EXIT_CODE}" ]; then
    echo "FAIL: skip-without-key must exit ${BACKUP_SKIP_EXIT_CODE}, got ${SKIP_EXIT}" >&2
    exit 1
fi
# shellcheck disable=SC2012
SKIP_COUNT=$(find "${SKIP_BACKUP_DIR}" -name 'flowfolio-*.db.age' | wc -l | tr -d ' ')
if [ "${SKIP_COUNT}" != "0" ]; then
    echo "FAIL: skip-without-key must write NO .db.age, found ${SKIP_COUNT}" >&2
    exit 1
fi
echo "PASS: skip-without-key exited ${BACKUP_SKIP_EXIT_CODE} and wrote no backup."

# --------------------------------------------------------------------------
# Pre-update snapshot contract: a BACKUP_LABEL=preupdate BACKUP_NO_PRUNE=1
# run lands the artifact under a dedicated subdir, and a SUBSEQUENT daily run
# (whose prune is scoped to -maxdepth 1) must NOT delete it.
# --------------------------------------------------------------------------
echo "TEST: backup.sh pre-update snapshot (BACKUP_LABEL=preupdate BACKUP_NO_PRUNE=1)..."
PREUPDATE_BACKUP_DIR="${TEMP_DIR}/preupdate-test"
BACKUP_LABEL=preupdate BACKUP_NO_PRUNE=1 \
    DB_PATH="${TEST_DB}" \
    BACKUP_DIR="${PREUPDATE_BACKUP_DIR}" \
    BACKUP_ENCRYPTION_KEY="${TEST_KEY}" \
    sh "${BACKUP_SH}"
# shellcheck disable=SC2012
PRE_COUNT=$(find "${PREUPDATE_BACKUP_DIR}/preupdate" -name 'flowfolio-preupdate-*.db.age' 2>/dev/null | wc -l | tr -d ' ')
if [ "${PRE_COUNT}" != "1" ]; then
    echo "FAIL: expected exactly 1 pre-update snapshot under preupdate/, got ${PRE_COUNT}" >&2
    exit 1
fi
PRE_ARTIFACT=$(find "${PREUPDATE_BACKUP_DIR}/preupdate" -name 'flowfolio-preupdate-*.db.age')
echo "PASS: pre-update snapshot landed at ${PRE_ARTIFACT}"

# Age the artifact well past BACKUP_RETAIN_DAYS so a recursive prune WOULD delete
# it, proving the -maxdepth 1 + subdir exemption is what keeps it.
# -t [[CC]YY]MMDDhhmm is portable across GNU and BSD touch.
touch -t 202001010000 "${PRE_ARTIFACT}"

echo "TEST: a subsequent daily backup must NOT prune the pre-update snapshot..."
DB_PATH="${TEST_DB}" \
    BACKUP_DIR="${PREUPDATE_BACKUP_DIR}" \
    BACKUP_ENCRYPTION_KEY="${TEST_KEY}" \
    BACKUP_RETAIN_DAYS=30 \
    sh "${BACKUP_SH}"
if [ ! -f "${PRE_ARTIFACT}" ]; then
    echo "FAIL: daily prune deleted the pre-update snapshot (must be retention-exempt)" >&2
    exit 1
fi
echo "PASS: daily prune (maxdepth 1) left the pre-update snapshot intact."

# Round-trip: restore_local.sh decrypts the pre-update snapshot back to a DB.
echo "TEST: restore_local.sh decrypts the pre-update snapshot..."
RESTORE_LOCAL_SH="${SCRIPT_DIR}/restore_local.sh"
LOCAL_RESTORE_TARGET="${TEMP_DIR}/local-restored.db"
BACKUP_ENCRYPTION_KEY="${TEST_KEY}" \
    sh "${RESTORE_LOCAL_SH}" "${PRE_ARTIFACT}" "${LOCAL_RESTORE_TARGET}"
LOCAL_ROWS=$(sqlite3 "${LOCAL_RESTORE_TARGET}" "SELECT COUNT(*) FROM test_table;")
if [ "${LOCAL_ROWS}" != "2" ]; then
    echo "FAIL: restore_local.sh expected 2 rows, got ${LOCAL_ROWS}" >&2
    exit 1
fi
echo "PASS: restore_local.sh round-trip verified (${LOCAL_ROWS} rows)."

echo "PASS: Backup smoke test passed."
echo "PASS: Encrypted backup created, decrypted, integrity verified, data verified."
