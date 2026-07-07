"""Update-check endpoints.

`version_router` exposes the standalone GET /api/version and the cached
GET /api/update-status, both un-prefixed so /api/update-status resolves
literally (it is NOT under /api/update). `router` is the /api/update-prefixed
surface carrying POST /api/update/check and PUT /api/update/dismiss.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.schemas.update import (
    CheckResponse,
    DismissBody,
    UpdateStatusResponse,
    VersionResponse,
)
from app.services.update_check import run_version_check
from app.services.update_store import (
    get_cached_release,
    get_dismissed_version,
    set_dismissed_version,
)
from app.services.update_version import is_newer_release

router = APIRouter(prefix="/api/update", tags=["update"])

version_router = APIRouter(tags=["update"])


@version_router.get("/api/version", response_model=VersionResponse)
async def get_version() -> VersionResponse:
    return VersionResponse(version=settings.app_version)


@version_router.get("/api/update-status", response_model=UpdateStatusResponse)
async def get_update_status(
    db: AsyncSession = Depends(get_db),
) -> UpdateStatusResponse:
    """Cached current-vs-latest + dismissal state. Reads the DB only,
    never makes an outbound GitHub call."""
    current = settings.app_version
    cached = await get_cached_release(db)
    dismissed_version = await get_dismissed_version(db)
    latest = cached.latest_version

    # A dev build is source-mounted: no image to pull, and "dev" is not comparable
    # to a release (the working tree is usually AHEAD of the latest tag). Suppress
    # the update prompt entirely and surface is_dev so the UI can explain why.
    is_dev = current == "dev"
    dismissed = latest is not None and dismissed_version == latest
    # Only a strictly newer release is actionable. A plain `latest != current`
    # would flag the older cached release as an update while the running version
    # leads it (the daily check has not refreshed yet after a release).
    update_available = (
        is_newer_release(latest, current) and not dismissed and not is_dev
    )

    return UpdateStatusResponse(
        current_version=current,
        latest_version=latest,
        update_available=update_available,
        is_dev=is_dev,
        release_notes_url=cached.notes_url,
        dismissed=dismissed,
        last_checked=cached.last_checked,
        check_failed=cached.last_status == "failed",
        backups_configured=bool(settings.backup_encryption_key),
    )


@router.post("/check", response_model=CheckResponse)
async def check_for_update(
    db: AsyncSession = Depends(get_db),
) -> CheckResponse:
    """Force an immediate GitHub release check, bypassing the once-per-UTC-day
    cron cadence. Refreshes the cached latest release so the UI can reflect a
    release published since the last daily run. Soft-fails: a network/HTTP error
    records status=failed rather than raising, mirroring the scheduled job.
    Session-gated by AuthMiddleware like the rest of /api/update/*."""
    result = await run_version_check(db)
    await db.commit()
    return CheckResponse(
        status=str(result["status"]),
        latest_version=result["latest"],
    )


@router.put("/dismiss", status_code=204)
async def dismiss_version(
    body: DismissBody,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Persist the dismiss-until-next-version marker. Writes via
    update_store, NOT /api/settings, the key stays off SETTING_KEYS_ALLOWLIST."""
    try:
        await set_dismissed_version(db, body.version)
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await db.commit()
