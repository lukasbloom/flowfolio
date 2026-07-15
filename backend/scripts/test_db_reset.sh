#!/bin/sh
# Reset the active SQLite DB to the golden snapshot.
# Strategy: copy to a temp file in the same dir, then mv. Avoids partial reads
# if a connection is opened mid-copy.
#
# The golden snapshot is baked CLAIMED (setup_complete + admin_password_hash
# for the e2e password, see SEED_BAKE_CLAIM in scripts/seed-golden.py), so the
# file swap alone keeps the storageState session valid. No post-swap SQL.
#
# Connection-pool note: compose.test.yml sets FLOWFOLIO_NULL_POOL=true,
# which configures the SQLAlchemy engine with NullPool. This means every API
# request opens a fresh DB connection pointing to whatever /data/flowfolio.db
# resolves to at that moment, so the mv-based file swap is fully visible on
# the next request without stale pool fd issues.
set -eu
TMP="/data/flowfolio.db.reset-tmp"
cp /golden/golden.sqlite "$TMP"
mv -f "$TMP" /data/flowfolio.db
# SQLite WAL: remove stale WAL/SHM if present (golden is committed in
# journal=DELETE mode, WAL files would shadow).
rm -f /data/flowfolio.db-wal /data/flowfolio.db-shm
