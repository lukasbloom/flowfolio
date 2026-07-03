"""Session-cookie auth middleware.

Every request to a non-exempt path must carry a valid session cookie or
the middleware returns HTTP 401 before the route handler is invoked.

CRITICAL: /api/healthcheck must remain exempt. Docker Compose uses
`depends_on.condition: service_healthy` against the api container's
healthcheck — a 401 there would prevent the web container from ever
starting at boot.
"""
from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.core.auth import SESSION_COOKIE_NAME, validate_session_token

# Paths that bypass the session-cookie check.
# /api/docs and /api/openapi.json are NOT listed here — they are gated at
# the FastAPI app level via APP_ENV (disabled when APP_ENV=production).
AUTH_EXEMPT_PATHS: frozenset[str] = frozenset(
    {
        "/api/auth/login",
        "/api/auth/logout",
        "/api/auth/demo-login",  # demo-only credential-free entry; 404s outside demo

        "/api/healthcheck",  # MUST stay public for Docker depends_on health
        "/api/setup/status",  # harmless boolean; needed before the wizard claims
        "/api/config",  # boot flags {demo, app_version}; read before any session
    }
)

# /api/setup/claim is the first-run bootstrap entry point, so it must be
# reachable while the instance is still unclaimed (there is no session yet).
# It is intentionally NOT session-gated: the router itself returns 409 once
# setup_complete is true (first-claim-wins), which is the real lock.
SETUP_CLAIM_PATH = "/api/setup/claim"


class AuthMiddleware(BaseHTTPMiddleware):
    """Reject any request to a non-exempt path that lacks a valid session cookie."""

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        if request.url.path in AUTH_EXEMPT_PATHS:
            return await call_next(request)

        # Bootstrap entry: let claim through; the 409-after-claim lock in the
        # router prevents re-claim, so no session gate is needed here.
        if request.url.path == SETUP_CLAIM_PATH:
            return await call_next(request)

        token = request.cookies.get(SESSION_COOKIE_NAME)
        if not token or not validate_session_token(token):
            return JSONResponse(
                status_code=401,
                content={"detail": "Authentication required"},
            )

        return await call_next(request)
