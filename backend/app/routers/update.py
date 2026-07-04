"""Self-update endpoints.

`version_router` exposes the standalone GET /api/version and the cached
GET /api/update-status, both un-prefixed so /api/update-status resolves
literally (it is NOT under /api/update). `router` is the /api/update-prefixed
surface carrying PUT /api/update/dismiss and the apply endpoint.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import forbid_in_demo
from app.schemas.update import (
    ApplyResponse,
    DismissBody,
    UpdateStatusResponse,
    VersionResponse,
)
from app.services.update_apply import (
    IN_FLIGHT_STATES,
    read_update_status,
    request_update,
)
from app.services.update_store import (
    get_cached_release,
    get_dismissed_version,
    set_dismissed_version,
)

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
    update_available = (
        latest is not None and latest != current and not dismissed and not is_dev
    )

    # Merge the updater's live progress from the shared-volume status.json.
    # Pure file read — no Docker, no outbound call.
    status = read_update_status()

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
        update_in_progress=status["state"] in IN_FLIGHT_STATES,
        update_state=status["state"],
        update_message=status["message"],
        update_log_tail=status["log_tail"],
    )


@router.post(
    "/apply",
    response_model=ApplyResponse,
    dependencies=[Depends(forbid_in_demo)],
)
async def apply_update(
    db: AsyncSession = Depends(get_db),
) -> ApplyResponse:
    """Trigger the self-update by dropping the shared-volume request file.

    The app NEVER touches the container engine. It only writes request.json; the
    socket-holding updater sidecar acts on it. Idempotent under the
    in-flight lock: a second apply while a run is non-terminal re-attaches to the
    same request_id instead of starting a second recreate. The target is
    the server-side cached latest release, semver-validated.
    Session-gated by AuthMiddleware like the rest of /api/update/*.

    forbid_in_demo returns 403 before the body runs, so a demo visitor (or a
    direct API call) can never trigger a container recreate (defense in
    depth independent of any UI hiding).
    """
    # A dev build has no image to pull — self-update cannot work. Refuse here so a
    # stray click or a direct API call can never kick the updater against a
    # source-mounted stack (defense in depth, independent of the UI hiding).
    if settings.app_version == "dev":
        raise HTTPException(
            status_code=409,
            detail="Self-update is not available on a development build.",
        )
    cached = await get_cached_release(db)
    target = cached.latest_version
    if not target:
        raise HTTPException(status_code=409, detail="No update available to apply.")
    # The endpoint is the robustness boundary. Never recreate the running
    # version (a stale client, a direct API call, or a post-check race) for no
    # actual update. Mirrors the raw-equality `update_available` check above.
    if target == settings.app_version:
        raise HTTPException(
            status_code=409, detail="Already running the latest version."
        )
    try:
        request_id = request_update(target)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ApplyResponse(request_id=request_id, state=read_update_status()["state"])


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
