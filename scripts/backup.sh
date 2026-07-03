#!/usr/bin/env sh
# Daily SQLite backup: hot export → age encrypt → local snapshot (+ optional rclone upload)
# Contract:
#   BACKUP_ENCRYPTION_KEY unset → SKIP with a loud warning, exit 75 (never write
#                                 plaintext financial data). 75 is the machine-
#                                 stable skip signal: the scheduler treats exit
#                                 75 as a terminal "ok" skip and records a skip
#                                 note. Do NOT reuse this code for a real error.
#   BACKUP_DEST unset           → local-only: write the encrypted .db.age to
#                                 BACKUP_DIR, skip the off-host rclone upload.
#   both set                    → local snapshot + off-host upload + retention prune.
# Env vars:
#   DB_PATH           path to the SQLite database file (default: /data/flowfolio.db)
#   BACKUP_DIR        local encrypted-snapshot dir, a SEPARATE mount from the DB
#                     volume (default: /backups) — never inside /data (correlated loss).
#   BACKUP_ENCRYPTION_KEY  symmetric passphrase for age encryption (required to back up)
#   BACKUP_DEST       rclone destination for off-host upload (e.g. "b2:my-bucket/flowfolio/")
#   RCLONE_CONFIG     path to rclone config (default: /root/.config/rclone/rclone.conf)
# Optional:
#   BACKUP_RETAIN_DAYS  days of daily backups to retain locally + remotely (default: 30)
#   BACKUP_LABEL        when set, the artifact is named with a label segment and
#                       written under a dedicated subdir ${BACKUP_DIR}/${BACKUP_LABEL}
#                       so it is physically separated from daily snapshots. Used by
#                       the self-update flow with BACKUP_LABEL=preupdate.
#   BACKUP_NO_PRUNE     when "1", skip the off-host upload + all retention pruning,
#                       so the artifact survives untouched (the pre-update snapshot
#                       must be recoverable to the exact pre-update state).

set -eu

DB_PATH="${DB_PATH:-/data/flowfolio.db}"
BACKUP_DIR="${BACKUP_DIR:-/backups}"
BACKUP_RETAIN_DAYS="${BACKUP_RETAIN_DAYS:-30}"
BACKUP_LABEL="${BACKUP_LABEL:-}"
BACKUP_NO_PRUNE="${BACKUP_NO_PRUNE:-0}"
TIMESTAMP="$(date -u +%Y-%m-%dT%H%M%SZ)"
if [ -n "${BACKUP_LABEL}" ]; then
    # Labelled (e.g. pre-update) snapshot: separate name + subdir so the daily
    # maxdepth-1 prune can never reach it (retention-exempt).
    BACKUP_DEST_DIR="${BACKUP_DIR}/${BACKUP_LABEL}"
    BACKUP_NAME="flowfolio-${BACKUP_LABEL}-${TIMESTAMP}.db.age"
else
    BACKUP_DEST_DIR="${BACKUP_DIR}"
    BACKUP_NAME="flowfolio-${TIMESTAMP}.db.age"
fi
TEMP_DIR="$(mktemp -d)"
TEMP_BACKUP="${TEMP_DIR}/flowfolio.db"
TEMP_ENCRYPTED="${TEMP_DIR}/${BACKUP_NAME}"

cleanup() {
    rm -rf "${TEMP_DIR}"
}
trap cleanup EXIT

# Refuse to write unencrypted financial data: without a key, skip cleanly.
# Exit 75 is the machine-stable skip signal the scheduler keys on — a
# terminal, idempotent-per-day "ok" skip rather than a failure that retries all
# day. The warning text is for humans only and is NOT parsed by the scheduler.
BACKUP_SKIP_EXIT_CODE=75
if [ -z "${BACKUP_ENCRYPTION_KEY:-}" ]; then
    echo "WARNING: BACKUP_ENCRYPTION_KEY not set — backups disabled (refusing to write unencrypted financial data). Set BACKUP_ENCRYPTION_KEY to enable encrypted backups." >&2
    exit "${BACKUP_SKIP_EXIT_CODE}"
fi

# Validate DB file exists
if [ ! -f "${DB_PATH}" ]; then
    echo "ERROR: Database file not found at ${DB_PATH}" >&2
    exit 1
fi

echo "INFO: Starting backup at ${TIMESTAMP}"
echo "INFO: Source: ${DB_PATH}"
echo "INFO: Local snapshot dir: ${BACKUP_DIR}"
if [ -n "${BACKUP_DEST:-}" ]; then
    echo "INFO: Off-host destination: ${BACKUP_DEST}"
else
    echo "INFO: BACKUP_DEST not set — local snapshot only (no off-host upload)."
fi

# Step 1: Hot backup using sqlite3 .backup (WAL-safe, does not lock main DB)
echo "INFO: Creating SQLite hot backup..."
sqlite3 "${DB_PATH}" ".backup '${TEMP_BACKUP}'"
echo "INFO: Hot backup created at ${TEMP_BACKUP} ($(du -sh "${TEMP_BACKUP}" | cut -f1))"

# Step 2: Encrypt with age (symmetric passphrase)
# age --passphrase reads the passphrase from /dev/tty (NOT stdin) for security,
# so the canonical scripted invocation pipes the passphrase to `script` which
# allocates a pty for age to read from. The passphrase is passed via the
# AGE_PASSPHRASE env var (and printf-substituted under the new shell) so it
# never appears in `ps` output. age prompts twice when encrypting (confirmation),
# so the passphrase is sent twice.
echo "INFO: Encrypting backup..."
AGE_PASSPHRASE="${BACKUP_ENCRYPTION_KEY}" \
    sh -c 'printf "%s\n%s\n" "${AGE_PASSPHRASE}" "${AGE_PASSPHRASE}" | \
           script -q -c "age --encrypt --passphrase --output \"$1\" \"$2\"" /dev/null >/dev/null' \
    _ "${TEMP_ENCRYPTED}" "${TEMP_BACKUP}"
if [ ! -s "${TEMP_ENCRYPTED}" ]; then
    echo "ERROR: age encryption produced no output — check that 'age' and 'script' (util-linux) are installed" >&2
    exit 1
fi
echo "INFO: Encrypted archive: ${TEMP_ENCRYPTED}"

# Step 3: Land the encrypted snapshot in the local destination dir (the default,
# always-on backup target — a SEPARATE mount from the DB volume). For a labelled
# run this is the dedicated ${BACKUP_DIR}/${BACKUP_LABEL} subdir.
mkdir -p "${BACKUP_DEST_DIR}"
cp "${TEMP_ENCRYPTED}" "${BACKUP_DEST_DIR}/${BACKUP_NAME}"
echo "INFO: Local snapshot written: ${BACKUP_DEST_DIR}/${BACKUP_NAME}"

# Step 4: Off-host upload via rclone — only when BACKUP_DEST is set. A
# retention-exempt run (BACKUP_NO_PRUNE=1) skips the off-host path entirely: the
# pre-update snapshot is a fast LOCAL rollback artifact and must not block
# the update on an upload nor be subject to remote pruning.
if [ "${BACKUP_NO_PRUNE}" = "1" ]; then
    echo "INFO: BACKUP_NO_PRUNE=1 — retention-exempt snapshot; skipping off-host upload + all pruning."
elif [ -n "${BACKUP_DEST:-}" ]; then
    echo "INFO: Uploading to ${BACKUP_DEST}..."
    rclone copy "${BACKUP_DEST_DIR}/${BACKUP_NAME}" "${BACKUP_DEST}" --progress
    echo "INFO: Backup complete: ${BACKUP_NAME} uploaded to ${BACKUP_DEST}"

    # Prune remote backups older than BACKUP_RETAIN_DAYS (safe via --min-age).
    echo "INFO: Pruning remote backups older than ${BACKUP_RETAIN_DAYS} days..."
    rclone delete "${BACKUP_DEST}" --min-age "${BACKUP_RETAIN_DAYS}d" --include "flowfolio-*.db.age" || true
else
    echo "INFO: BACKUP_DEST not set — skipping off-host upload."
fi

# Step 5: Prune local snapshots older than BACKUP_RETAIN_DAYS so BACKUP_DIR
# doesn't grow unbounded. Scoped to -maxdepth 1 so it ONLY prunes the
# top-level daily snapshots and never recurses into a label subdir (e.g.
# preupdate/), keeping the pre-update artifact retention-exempt.
if [ "${BACKUP_NO_PRUNE}" = "1" ]; then
    echo "INFO: BACKUP_NO_PRUNE=1 — skipping local retention prune."
else
    echo "INFO: Pruning top-level local snapshots older than ${BACKUP_RETAIN_DAYS} days..."
    find "${BACKUP_DIR}" -maxdepth 1 -name 'flowfolio-*.db.age' -mtime +"${BACKUP_RETAIN_DAYS}" -delete || true
fi

echo "INFO: Backup process finished successfully."
