"""bcrypt password hashing primitives.

Used by app.core.auth to verify the single-user login password.
No business logic here — pure crypto helpers.

Uses the `bcrypt` package directly. The former CryptContext wrapper library
was dropped: it had no release since 2020 and forced a `bcrypt<5` pin (its
1.7 line is incompatible with bcrypt 5.x). For a single hash/verify pair on a
never-persisted hash (see app.core.auth — the app-password hash is cached in
memory only), that abandoned dependency bought nothing.
"""
from __future__ import annotations

import bcrypt


def hash_password(password: str) -> str:
    """Return a bcrypt hash for the given plaintext password."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Constant-time bcrypt verification of plaintext against a stored hash.

    Returns False (rather than raising) on a malformed hash so callers can
    treat "bad password" and "garbage hash" identically.
    """
    try:
        return bcrypt.checkpw(
            plain_password.encode("utf-8"), hashed_password.encode("utf-8")
        )
    except ValueError:
        return False
