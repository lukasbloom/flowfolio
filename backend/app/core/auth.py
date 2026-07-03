"""Single-user auth: DB-backed bcrypt password check + signed JWT session token.

Approach:
- The admin password is stored in the DB as `user_setting.admin_password_hash`
  (bcrypt). The DB is the single source of truth. APP_PASSWORD, when set, is a
  boot-time pre-seed that materializes into that row, it is NOT a
  runtime fallback inside check_password.
- check_password reads the hash from the DB and bcrypt-verifies the candidate.
  When no hash is configured (unclaimed instance) it returns False.
- A successful login returns a signed JWT (HS256, signed with settings.secret_key)
  carrying `sub="user"` and an `exp` claim. The JWT is set as an HTTP-only cookie
  so JavaScript on the page cannot exfiltrate it (XSS protection).
- AuthMiddleware validates the cookie on every request to a non-exempt path.

To rotate the password: claim a new one via the setup API (or clear the row and
re-seed via APP_PASSWORD at boot).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import hash_password, verify_password  # noqa: F401  (re-exported)
from app.services.setup_state import get_admin_password_hash

SESSION_COOKIE_NAME = "session"
ALGORITHM = "HS256"


async def check_password(session: AsyncSession, plain_password: str) -> bool:
    """Verify a candidate password against the DB-stored admin password hash.

    Reads `admin_password_hash` from user_setting (the DB is the store). Returns
    False when no hash is configured (unclaimed instance) so callers treat
    "wrong password" and "not yet set up" identically.
    """
    stored = await get_admin_password_hash(session)
    if not stored:
        return False
    return verify_password(plain_password, stored)


def create_session_token() -> str:
    """Create a signed JWT session token with `exp` set per settings."""
    expire = datetime.now(timezone.utc) + timedelta(
        seconds=settings.session_expire_seconds
    )
    payload = {"sub": "user", "exp": expire}
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def validate_session_token(token: str) -> bool:
    """Return True if token is a valid, correctly signed, non-expired session.

    Accepted limitation: a token's only expiry guard is its JWT `exp`
    (the 7-day session window). There is no server-side revocation — no `iat`
    floor and no token epoch — so logout clears only the client cookie and a
    password change does NOT invalidate existing sessions. Rotating SECRET_KEY
    is the blunt "log out everywhere" lever. For a single-user self-hosted box
    this is acceptable; a per-instance `token_epoch` claim (bumped on password
    change) is the future hardening if revocation is ever needed.
    """
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    except JWTError:
        return False
    return payload.get("sub") == "user"
