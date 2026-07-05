"""Auth router: POST /api/auth/login, POST /api/auth/logout.

Both endpoints are exempt from AuthMiddleware (see middleware/auth_middleware.py).
The login endpoint sets an HTTP-only session cookie; logout clears it.
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import (
    SESSION_COOKIE_NAME,
    check_password,
    create_session_token,
    hash_password,
)
from app.core.config import settings
from app.core.database import get_db
from app.services.setup_state import bump_token_epoch, get_token_epoch, set_admin_password_hash

router = APIRouter(prefix="/api/auth", tags=["auth"])

# Login brute-force throttle (single-user, single-worker — module state is safe;
# see scheduler.py WEB_CONCURRENCY=1 enforcement). A global counter is correct
# here: there is exactly one legitimate user, so per-IP tracking only adds
# X-Forwarded-For parsing complexity and lets an attacker rotate source IPs.
#
# Defense-in-depth limitation (accepted): this throttle is BEST-EFFORT
# and NOT restart-durable. The counter and lockout live in module globals, so
# any process restart (the documented `uvicorn --reload` dev loop, a crash, a
# redeploy, or a container restart) resets them. The real protection is the
# bcrypt-hashed password; this lockout only slows online guessing between
# restarts. A DB-backed counter (a user_setting row) would make it durable and
# is the natural future hardening, but is deferred to avoid adding a write on
# every failed login for a single-user box.
_FAILURE_WINDOW_SECONDS = 600   # forget stale failures older than this
_LOCKOUT_THRESHOLD = 5          # consecutive failures before lockout
_LOCKOUT_SECONDS = 60           # base cooldown; doubles each subsequent failure
_MAX_BACKOFF_EXPONENT = 6       # cap doubling at ~64 min

_failed_attempts: int = 0
_locked_until: float = 0.0
_last_failure_at: float = 0.0


def _reset_rate_limiter() -> None:
    """Clear all login-throttle module state. For tests and successful logins."""
    global _failed_attempts, _locked_until, _last_failure_at
    _failed_attempts = 0
    _locked_until = 0.0
    _last_failure_at = 0.0


def _register_failure() -> None:
    """Record a failed login attempt and arm the lockout once the threshold trips."""
    global _failed_attempts, _locked_until, _last_failure_at
    now = time.monotonic()
    # Forget a stale failure streak so a slow trickle never accumulates a lockout.
    if now - _last_failure_at > _FAILURE_WINDOW_SECONDS:
        _failed_attempts = 0
    _last_failure_at = now
    _failed_attempts += 1
    if _failed_attempts >= _LOCKOUT_THRESHOLD:
        exponent = min(_failed_attempts - _LOCKOUT_THRESHOLD, _MAX_BACKOFF_EXPONENT)
        _locked_until = now + _LOCKOUT_SECONDS * (2 ** exponent)


class LoginRequest(BaseModel):
    password: str


@router.post("/login")
async def login(
    body: LoginRequest,
    response: Response,
    session: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Verify password; on success set HTTP-only session cookie."""
    now = time.monotonic()
    if now < _locked_until:
        retry_after = int(_locked_until - now) + 1
        raise HTTPException(
            status_code=429,
            detail="Too many failed attempts; try again later",
            headers={"Retry-After": str(retry_after)},
        )

    if not await check_password(session, body.password):
        # Do NOT echo the attempted password in any log line.
        _register_failure()
        raise HTTPException(status_code=401, detail="Invalid password")

    # Successful login clears the brute-force counter and any active lockout.
    _reset_rate_limiter()
    epoch = await get_token_epoch(session)
    token = create_session_token(epoch)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,                         # XSS: JS cannot read cookie
        samesite="strict",                     # CSRF protection (strict: no cross-site send)
        secure=settings.app_env == "production",  # HTTPS-only in prod (Caddy)
        max_age=settings.session_expire_seconds,
    )
    return {"status": "ok"}


class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str


@router.post("/password")
async def change_password(
    body: PasswordChangeRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Change the admin password and revoke every other session.

    Requires an already-valid session cookie (this endpoint is not in
    AUTH_EXEMPT_PATHS, so AuthMiddleware gates it) plus the current password,
    so an attacker who steals a live session still cannot take over the
    account without knowing the password. Bumping token_epoch invalidates
    every session minted before the change; the caller's own cookie is
    re-issued at the new epoch in the same response so this endpoint does
    not log the caller out of their own change.
    """
    if not await check_password(session, body.current_password):
        raise HTTPException(status_code=401, detail="Current password is incorrect")
    if len(body.new_password) < 8:
        raise HTTPException(
            status_code=422, detail="New password must be at least 8 characters"
        )
    await set_admin_password_hash(session, hash_password(body.new_password))
    new_epoch = await bump_token_epoch(session)
    await session.commit()
    # Keep the middleware's cached epoch coherent without a process restart.
    request.app.state.token_epoch = new_epoch
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=create_session_token(new_epoch),
        httponly=True,
        samesite="strict",
        secure=settings.app_env == "production",
        max_age=settings.session_expire_seconds,
    )
    return {"status": "ok"}


@router.get("/demo-login")
async def demo_login() -> RedirectResponse:
    """Mint the shared demo session and redirect to /track (demo mode only).

    Scoped strictly to demo mode: 404 when settings.demo_mode is false, so the
    credential-free entry can never leak into a normal single-password install.
    Mints the STANDARD session JWT (create_session_token) with the SAME cookie
    attributes as the password login, so the entire downstream auth model is
    byte-for-byte identical to normal mode. The password POST /api/auth/login
    path is never touched (no check_password, no throttle interaction). Uses
    epoch 0 (no session dependency here; the demo never changes its password,
    so its epoch never bumps).
    """
    if not settings.demo_mode:
        raise HTTPException(status_code=404, detail="Not found")

    token = create_session_token(0)
    response = RedirectResponse(url="/track", status_code=303)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="strict",
        secure=settings.app_env == "production",
        max_age=settings.session_expire_seconds,
    )
    return response


@router.post("/logout")
async def logout(response: Response) -> dict[str, str]:
    """Clear the session cookie.

    The deletion cookie must carry the SAME attributes the login cookie was set
    with (path, httponly, samesite, and secure), or browsers may not
    treat it as overwriting the existing secure/strict cookie, leaving the user
    still authenticated after a "successful" logout.
    """
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/",
        httponly=True,
        samesite="strict",
        secure=settings.app_env == "production",
    )
    return {"status": "ok"}
