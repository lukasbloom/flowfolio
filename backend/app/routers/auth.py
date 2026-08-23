"""Auth router: login, logout, password change, and TOTP 2FA management.

Endpoints: POST /login (+ two-step POST /login/2fa when 2FA is on), POST
/logout, GET /demo-login, POST /password, and the 2FA lifecycle (GET /2fa,
POST /2fa/setup, POST /2fa/enable, POST /2fa/disable).

/password, /2fa/setup, /2fa/enable, and /2fa/disable are forbidden in demo
mode (forbid_in_demo). /login, /logout, /demo-login, and /login/2fa are
exempt from AuthMiddleware (see middleware/auth_middleware.py) since they
run before a session cookie exists.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import (
    check_password,
    clear_session_cookie,
    create_pre_auth_token,
    create_session_token,
    hash_password,
    set_session_cookie,
    validate_pre_auth_token,
)
from app.core.config import settings
from app.core.constants import MIN_PASSWORD_LENGTH
from app.core.database import get_db
from app.core.deps import forbid_in_demo
from app.core.throttle import login_throttle
from app.services import totp
from app.services.setup_state import (
    bump_token_epoch,
    get_token_epoch,
    get_totp_secret,
    is_totp_enabled,
    set_admin_password_hash,
    set_totp_enabled,
    set_totp_secret,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _client_ip(request: Request) -> str:
    """Client IP for rate-limit keying. Caddy fronts the app on loopback in
    every supported deployment and appends the real peer to X-Forwarded-For, so
    the LAST entry is the address Caddy saw. Only Caddy can reach the API in the
    shipped topologies, so that last hop is trustworthy. Fall back to the socket
    peer when the header is absent (direct dev access, tests). A user who chains
    their own proxy in front collapses all logins to that proxy's IP, degrading
    to the old global limiter, which is no worse."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


def _throttled_ip(request: Request) -> str:
    """Dependency: resolve the client IP and reject with 429 while its login
    lockout is active. Returns the IP for record_failure/reset calls."""
    ip = _client_ip(request)
    retry_after = login_throttle.retry_after(ip)
    if retry_after is not None:
        raise HTTPException(
            status_code=429,
            detail="Too many failed attempts; try again later",
            headers={"Retry-After": str(retry_after)},
        )
    return ip


class LoginRequest(BaseModel):
    password: str


@router.post("/login")
async def login(
    body: LoginRequest,
    response: Response,
    ip: str = Depends(_throttled_ip),
    session: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Verify password; on success set HTTP-only session cookie."""
    if not await check_password(session, body.password):
        # Do NOT echo the attempted password in any log line.
        login_throttle.record_failure(ip)
        raise HTTPException(status_code=401, detail="Invalid password")

    if await is_totp_enabled(session):
        # Password OK but 2FA is required: hand back a short-lived pre-auth
        # token proving the password check passed. NO session cookie yet,
        # the browser is not authenticated until POST /login/2fa succeeds.
        # Do NOT reset the rate limiter here: login is not yet complete, and
        # resetting on a correct password would let an attacker who knows the
        # password wipe an in-progress TOTP brute-force lockout between
        # batches of /login/2fa guesses. The limiter only resets once login
        # actually completes (below, or in login_2fa on a correct code).
        return {"twofa_required": "true", "pre_auth_token": create_pre_auth_token()}

    # Successful login clears this IP's brute-force counter and lockout. Only
    # the caller's entry is dropped, so an attacker's armed lockout on another
    # source IP is untouched by the owner logging in.
    login_throttle.reset(ip)

    epoch = await get_token_epoch(session)
    set_session_cookie(response, create_session_token(epoch))
    return {"status": "ok"}


class TwoFactorLoginRequest(BaseModel):
    pre_auth_token: str
    code: str


@router.post("/login/2fa")
async def login_2fa(
    body: TwoFactorLoginRequest,
    response: Response,
    ip: str = Depends(_throttled_ip),
    session: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Second step of two-step login: verify the pre-auth token + TOTP code.

    Shares the same per-IP brute-force throttle as /login (login_throttle),
    so within one source IP a lockout armed by repeated wrong codes here also
    blocks the password step, and vice versa. This route is listed in
    AUTH_EXEMPT_PATHS.
    """
    secret = await get_totp_secret(session)
    if (
        not validate_pre_auth_token(body.pre_auth_token)
        or not secret
        or not totp.verify_code(secret, body.code)
    ):
        login_throttle.record_failure(ip)
        raise HTTPException(status_code=401, detail="Invalid code")

    login_throttle.reset(ip)
    epoch = await get_token_epoch(session)
    set_session_cookie(response, create_session_token(epoch))
    return {"status": "ok"}


class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str


@router.post("/password", dependencies=[Depends(forbid_in_demo)])
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

    forbid_in_demo returns 403 before the body runs: an unconditional
    token_epoch bump here would break the epoch-0 invariant demo_login
    depends on (see its docstring).
    """
    if not await check_password(session, body.current_password):
        raise HTTPException(status_code=401, detail="Current password is incorrect")
    if len(body.new_password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=422,
            detail=f"New password must be at least {MIN_PASSWORD_LENGTH} characters",
        )
    await set_admin_password_hash(session, hash_password(body.new_password))
    new_epoch = await bump_token_epoch(session)
    await session.commit()
    # Keep the middleware's cached epoch coherent without a process restart.
    request.app.state.token_epoch = new_epoch
    set_session_cookie(response, create_session_token(new_epoch))
    return {"status": "ok"}


class CodeRequest(BaseModel):
    code: str


class PasswordConfirm(BaseModel):
    password: str


@router.get("/2fa")
async def twofa_status(session: AsyncSession = Depends(get_db)) -> dict[str, bool]:
    """Whether TOTP 2FA is currently enabled."""
    return {"enabled": await is_totp_enabled(session)}


@router.post("/2fa/setup", dependencies=[Depends(forbid_in_demo)])
async def twofa_setup(session: AsyncSession = Depends(get_db)) -> dict[str, str]:
    """Generate a new pending TOTP secret and return its enrollment material.

    Overwrites any prior pending secret and resets enabled to false. A fresh
    setup call always starts a clean enrollment (the old secret, if any, is
    discarded and not reusable to enable 2FA). Refuses to run while 2FA is
    already enabled, since overwriting the secret would silently disable it
    without the password check that /2fa/disable requires.

    forbid_in_demo returns 403 before the body runs: 2FA setup needs no
    password, so an unguarded demo visitor could enable it and then be
    unable to disable it.
    """
    if await is_totp_enabled(session):
        raise HTTPException(
            status_code=409,
            detail="Disable two-factor authentication before re-enrolling.",
        )
    secret = totp.generate_secret()
    await set_totp_secret(session, secret)
    await set_totp_enabled(session, False)
    await session.commit()
    uri = totp.provisioning_uri(secret)
    return {"secret": secret, "otpauth_uri": uri, "qr_svg": totp.qr_svg_data_uri(uri)}


@router.post("/2fa/enable", dependencies=[Depends(forbid_in_demo)])
async def twofa_enable(
    body: CodeRequest, session: AsyncSession = Depends(get_db)
) -> dict[str, str]:
    """Verify a TOTP code against the pending secret, then enable 2FA.

    forbid_in_demo returns 403 before the body runs (see twofa_setup).
    """
    secret = await get_totp_secret(session)
    if not secret or not totp.verify_code(secret, body.code):
        raise HTTPException(status_code=400, detail="Invalid code")
    await set_totp_enabled(session, True)
    await session.commit()
    return {"status": "ok"}


@router.post("/2fa/disable", dependencies=[Depends(forbid_in_demo)])
async def twofa_disable(
    body: PasswordConfirm, session: AsyncSession = Depends(get_db)
) -> dict[str, str]:
    """Password-confirmed disable: clears the stored secret and turns 2FA off.

    forbid_in_demo returns 403 before the body runs (see twofa_setup).
    """
    if not await check_password(session, body.password):
        raise HTTPException(status_code=401, detail="Password is incorrect")
    await set_totp_secret(session, None)
    await set_totp_enabled(session, False)
    await session.commit()
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

    response = RedirectResponse(url="/track", status_code=303)
    set_session_cookie(response, create_session_token(0))
    return response


@router.post("/logout")
async def logout(response: Response) -> dict[str, str]:
    """Clear the session cookie (deletion attributes mirror the set path
    structurally, see core.auth._session_cookie_attrs)."""
    clear_session_cookie(response)
    return {"status": "ok"}
