"""Self-update key/value store over the user_setting table.

Mirrors app/services/setup_state.py: these keys carry a different schema than
the user-facing settings allowlist, so they are upserted DIRECTLY here, never
through services/settings.py validate_setting/upsert_setting. They are kept OFF
SETTING_KEYS_ALLOWLIST so GET /api/settings can never read them and PUT
/api/settings can never write them. Two groups of rows:

  - dismissed_version: the server-persisted "dismiss until next version" marker,
    written only via the dedicated /api/update/dismiss endpoint.
  - the cached latest GitHub release + the daily check status, written
    only by the version-check cron.

Services do NOT commit; callers (the cron handler / the dismiss router) own the
transaction boundary, consistent with services/settings.py and setup_state.py.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user_setting import UserSetting

# Update-owned user_setting keys. NEVER added to SETTING_KEYS_ALLOWLIST.
UPDATE_KEYS = (
    "dismissed_version",
    "latest_release_version",
    "latest_release_notes_url",
    "latest_release_published_at",
    "update_check_last_checked",
    "update_check_last_status",
)


@dataclass
class CachedRelease:
    """The cached latest-release + daily-check status read by /api/update-status."""

    latest_version: str | None = None
    notes_url: str | None = None
    published_at: str | None = None
    last_checked: str | None = None
    last_status: str | None = None


async def _get_value(session: AsyncSession, key: str) -> str | None:
    result = await session.execute(
        select(UserSetting.value).where(UserSetting.key == key)
    )
    return result.scalar_one_or_none()


async def _set_value(session: AsyncSession, key: str, value: str | None) -> None:
    """Upsert a single update-owned key. No allowlist check (these keys are
    intentionally off the user-facing settings surface). Caller commits."""
    result = await session.execute(
        select(UserSetting).where(UserSetting.key == key)
    )
    row = result.scalar_one_or_none()
    if value is None:
        if row is not None:
            await session.delete(row)
        return
    if row is None:
        session.add(UserSetting(key=key, value=value))
    else:
        row.value = value


async def get_dismissed_version(session: AsyncSession) -> str | None:
    """The currently dismissed version, or None if the banner is not dismissed."""
    return await _get_value(session, "dismissed_version")


async def set_dismissed_version(session: AsyncSession, version: str) -> None:
    """Persist the dismiss-until-next-version marker. Caller commits."""
    if not version or not version.strip():
        raise ValueError("dismissed version must be a non-empty string")
    await _set_value(session, "dismissed_version", version.strip())


async def get_cached_release(session: AsyncSession) -> CachedRelease:
    """Read the cached latest release + daily-check status (DB only, no HTTP)."""
    return CachedRelease(
        latest_version=await _get_value(session, "latest_release_version"),
        notes_url=await _get_value(session, "latest_release_notes_url"),
        published_at=await _get_value(session, "latest_release_published_at"),
        last_checked=await _get_value(session, "update_check_last_checked"),
        last_status=await _get_value(session, "update_check_last_status"),
    )


async def set_cached_release(
    session: AsyncSession,
    *,
    version: str | None,
    notes_url: str | None,
    published_at: str | None,
) -> None:
    """Write the latest-release cache rows. Caller commits.

    notes_url is expected to already be github-validated by update_check; this
    store does not re-validate it but stores None as a row deletion.
    """
    await _set_value(session, "latest_release_version", version)
    await _set_value(session, "latest_release_notes_url", notes_url)
    await _set_value(session, "latest_release_published_at", published_at)


async def set_check_status(
    session: AsyncSession, *, status: str, checked_at: str
) -> None:
    """Record the daily-check terminal status (ok/failed) + timestamp. Caller commits."""
    await _set_value(session, "update_check_last_status", status)
    await _set_value(session, "update_check_last_checked", checked_at)
