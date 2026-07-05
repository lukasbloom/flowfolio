"""Single-user auth: DB-backed bcrypt password check + signed JWT session token.

Approach:
- The admin password is stored in the DB as `user_setting.admin_password_hash`
  (bcrypt). The DB is the single source of truth. APP_PASSWORD, when set, is a
  boot-time pre-seed that materializes into that row, it is NOT a
  runtime fallback inside check_password.
- check_password reads the hash from the DB and bcrypt-verifies the candidate.
  When no hash is configured (unclaimed instance) it returns False.
- A successful login returns a signed JWT (HS256, signed with settings.secret_key)
  carrying `sub="user"`, an `exp` claim, and a `token_epoch` claim. The JWT is
  set as an HTTP-only cookie so JavaScript on the page cannot exfiltrate it
  (XSS protection).
- AuthMiddleware validates the cookie on every request to a non-exempt path,
  checking the token's epoch against the current stored epoch (cached on
  app.state, see main.py's lifespan). Bumping the stored epoch (on password
  change) invalidates every session minted before the bump, giving
  server-side revocation without a session store.
- A pre-auth token (create_pre_auth_token/validate_pre_auth_token) is a
  separate, short-lived JWT proving password verification while 2FA is
  pending. It carries a `stage="2fa"` claim instead of `sub`/`token_epoch`,
  so it is never accepted as a session token and vice versa.

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


_PRE_AUTH_TTL_SECONDS = 300  # 5 min


def create_session_token(token_epoch: int) -> str:
    """Create a signed JWT session token with `exp` and `token_epoch` set.

    `token_epoch` must match the current stored epoch (see
    app.services.setup_state.get_token_epoch/bump_token_epoch) for the token
    to validate. Bumping the epoch (on password change) invalidates every
    session minted before the bump, without needing a server-side session
    store.
    """
    expire = datetime.now(timezone.utc) + timedelta(
        seconds=settings.session_expire_seconds
    )
    payload = {"sub": "user", "exp": expire, "token_epoch": token_epoch}
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def session_token_epoch(token: str) -> int | None:
    """Return the token's `token_epoch` claim, or None if invalid/expired.

    A token with no `token_epoch` claim (minted before this feature) reads
    as epoch 0, so pre-existing sessions survive against the default stored
    epoch of 0.
    """
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    except JWTError:
        return None
    if payload.get("sub") != "user":
        return None
    return int(payload.get("token_epoch", 0))


def validate_session_token(token: str, current_epoch: int) -> bool:
    """Return True if token is valid, correctly signed, non-expired, and its
    epoch matches current_epoch (server-side revocation via epoch bump)."""
    epoch = session_token_epoch(token)
    return epoch is not None and epoch == current_epoch


def create_pre_auth_token() -> str:
    """Create a short-lived token proving password verification, pending 2FA."""
    expire = datetime.now(timezone.utc) + timedelta(seconds=_PRE_AUTH_TTL_SECONDS)
    return jwt.encode(
        {"stage": "2fa", "exp": expire}, settings.secret_key, algorithm=ALGORITHM
    )


def validate_pre_auth_token(token: str) -> bool:
    """Return True if token is a valid, unexpired pre-auth (2fa stage) token."""
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    except JWTError:
        return False
    return payload.get("stage") == "2fa"
