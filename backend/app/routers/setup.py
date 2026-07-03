"""First-run setup router: GET /api/setup/status, POST /api/setup/claim.

The instance is unclaimed between first boot and the first password claim.
`/status` reports only a boolean. `/claim` sets the admin password
on the FIRST call and issues a session cookie; any later claim returns 409
(first visitor wins, the open-claim-window mitigation).

Middleware: `/status` is permanently exempt (harmless boolean); `/claim` is
reachable while unclaimed (it is the bootstrap entry point) and self-gates via
the 409 lock once claimed (see middleware/auth_middleware.py).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import SESSION_COOKIE_NAME, create_session_token
from app.core.config import settings
from app.core.database import get_db
from app.services.setup_state import claim_admin_password, is_setup_complete

router = APIRouter(prefix="/api/setup", tags=["setup"])


class ClaimRequest(BaseModel):
    # V5 input validation: minimum length mirrors the claim-flow contract.
    password: str = Field(min_length=8)


@router.get("/status")
async def status(session: AsyncSession = Depends(get_db)) -> dict[str, bool]:
    """Report whether the instance has been claimed (leaks only a boolean)."""
    return {"claimed": await is_setup_complete(session)}


@router.post("/claim")
async def claim(
    body: ClaimRequest,
    response: Response,
    session: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Claim the admin password on first call; reject any later claim with 409.

    The claim is atomic at the DB level: `claim_admin_password` returns
    False for the loser of a concurrent first-claim race instead of colliding
    into an IntegrityError 500, so the second visitor deterministically gets a
    409 and the winner's password hash is never overwritten. The early
    `is_setup_complete` check is a cheap fast-path for the common already-claimed
    case; the atomic gate is the real lock.
    """
    if await is_setup_complete(session):
        # First-claim-wins: once claimed, the bootstrap entry is shut.
        raise HTTPException(status_code=409, detail="Setup already complete")

    won = await claim_admin_password(session, body.password)
    await session.commit()
    if not won:
        # Lost the atomic claim race to a concurrent first visitor.
        raise HTTPException(status_code=409, detail="Setup already complete")

    token = create_session_token()
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,                         # XSS: JS cannot read cookie
        samesite="strict",                     # CSRF protection (strict: no cross-site send)
        secure=settings.app_env == "production",  # HTTPS-only in prod (Caddy)
        max_age=settings.session_expire_seconds,
    )
    return {"status": "ok"}
