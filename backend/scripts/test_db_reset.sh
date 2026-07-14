#!/bin/sh
# Reset the active SQLite DB to the golden snapshot.
# Strategy: copy to a temp file in the same dir, then mv. Avoids partial reads
# if a connection is opened mid-copy.
#
# Connection-pool note: compose.test.yml sets FLOWFOLIO_NULL_POOL=true,
# which configures the SQLAlchemy engine with NullPool. This means every API
# request opens a fresh DB connection pointing to whatever /data/flowfolio.db
# resolves to at that moment — so the mv-based file swap is fully visible on
# the next request without stale pool fd issues.
set -eu
TMP="/data/flowfolio.db.reset-tmp"
cp /golden/golden.sqlite "$TMP"
mv -f "$TMP" /data/flowfolio.db
# SQLite WAL: remove stale WAL/SHM if present (golden is committed in
# journal=DELETE mode; WAL files would shadow).
rm -f /data/flowfolio.db-wal /data/flowfolio.db-shm

# Re-establish the admin claim. The committed golden.sqlite is UNCLAIMED
# (no setup_complete / admin_password_hash row) and the lifespan APP_PASSWORD
# pre-seed only runs once at boot, so a reset wipes the claim (the storageState
# session would then 401). We write the claim rows directly with sqlite3 (fast,
# no python interpreter spawn, stays inside the 200ms reset budget). The
# bcrypt hash below is a one-time `hash_password("test-password-e2e")` value
# (verify is stable for any valid bcrypt hash); updated_at is pinned to the
# golden's frozen 2024-01-01 so the reset stays deterministic. This mirrors
# the lifespan pre-seed, which sets the same "test-password-e2e" password
# directly (compose.test.yml, above the 8-char floor; global-setup login is
# unchanged).
# ADMIN_HASH is a TRUSTED, code-controlled constant, a one-time
# hash_password("test-password-e2e") value. It is interpolated into the SQL
# heredoc below, which is safe ONLY because the value is fixed here and
# bcrypt's alphabet excludes the single-quote that would break/escape the
# string. This value must NEVER be derived from external/user input; if it
# ever needs to come from a variable input, switch to a parameterized
# statement (sqlite3 bind / .param) instead of string interpolation.
ADMIN_HASH='$2b$12$RnldAxd5dKYdezbvfpOqWea3oHOAU6umoWK3RtQaMSnkSDuDgImQu'
sqlite3 /data/flowfolio.db <<SQL
INSERT INTO user_setting("key", value, updated_at)
  VALUES ('setup_complete', 'true', '2024-01-01 00:00:00.000000')
  ON CONFLICT("key") DO UPDATE SET value = excluded.value;
INSERT INTO user_setting("key", value, updated_at)
  VALUES ('admin_password_hash', '${ADMIN_HASH}', '2024-01-01 00:00:00.000000')
  ON CONFLICT("key") DO UPDATE SET value = excluded.value;
SQL
