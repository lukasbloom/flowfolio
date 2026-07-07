"""Boot-flags config API.

GET /api/config returns the unauthenticated boot flags the frontend reads
before any session exists: the demo flag (banner, login auto-route, update-panel
hide-logic) and the build version. Auth-exempt and DB-free, it reads only the
settings singleton. It MUST never expose a secret, key, password, or any
DB-derived sensitive value.
"""
from __future__ import annotations

from fastapi import APIRouter

from app.core.config import settings

router = APIRouter(prefix="/api/config", tags=["config"])


@router.get("")
async def get_config() -> dict[str, object]:
    """Return the public boot flags read straight off the settings singleton."""
    return {"demo": settings.demo_mode, "app_version": settings.app_version}
