"""First-run setup state over the user_setting table.

The admin password is stored in the DB as `admin_password_hash` (bcrypt), and
`setup_complete` records whether the instance has been claimed. APP_PASSWORD is
a boot-time pre-seed that MATERIALIZES into these rows — it is never a runtime
fallback inside auth.check_password.

Mirrors app/services/settings.py: services do NOT commit; callers own the
transaction boundary. These keys carry a different schema than the user-facing
settings allowlist, so they are upserted directly (not via settings.validate_setting).
"""
from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models.user_setting import UserSetting

# Setup-owned user_setting keys (distinct schema from SETTING_KEYS_ALLOWLIST).
SETUP_KEYS = (
    "admin_password_hash", "setup_complete",
    "totp_secret", "totp_enabled", "token_epoch",
)


async def _get_value(session: AsyncSession, key: str) -> str | None:
    result = await session.execute(
        select(UserSetting.value).where(UserSetting.key == key)
    )
    return result.scalar_one_or_none()


async def _set_value(session: AsyncSession, key: str, value: str | None) -> None:
    """Upsert (or delete when value is None) a setup-owned key."""
    if value is None:
        await session.execute(delete(UserSetting).where(UserSetting.key == key))
        return
    stmt = (
        sqlite_insert(UserSetting)
        .values(key=key, value=value)
        .on_conflict_do_update(index_elements=["key"], set_={"value": value})
    )
    await session.execute(stmt)


async def is_setup_complete(session: AsyncSession) -> bool:
    """True only when the `setup_complete` row is exactly "true"."""
    return await _get_value(session, "setup_complete") == "true"


async def get_admin_password_hash(session: AsyncSession) -> str | None:
    """Return the stored bcrypt admin password hash, or None if unclaimed."""
    return await _get_value(session, "admin_password_hash")


async def claim_admin_password(session: AsyncSession, password: str) -> bool:
    """Atomically claim the instance; return True iff THIS call won the claim.

    The `setup_complete` row is the atomic gate: an `INSERT ... ON CONFLICT DO
    NOTHING` on its primary key (`key`) means only the first writer's insert
    takes effect. A loser observes `rowcount == 0` and returns False without
    touching `admin_password_hash`, so the winner's hash is never overwritten.
    The password hash is written only by the winner.

    Caller owns the transaction (must commit). The commit is what makes the
    claim durable; the in-statement conflict guard makes concurrent claims
    deterministic rather than colliding into an IntegrityError 500.
    """
    gate = (
        sqlite_insert(UserSetting)
        .values(key="setup_complete", value="true")
        .on_conflict_do_nothing(index_elements=["key"])
    )
    result = await session.execute(gate)
    if result.rowcount == 0:
        return False  # someone already claimed — loser, do not touch the hash
    session.add(
        UserSetting(key="admin_password_hash", value=hash_password(password))
    )
    return True


async def pre_seed_admin_password_from_env(
    session: AsyncSession, app_password: str | None
) -> None:
    """Pre-seed the admin password from APP_PASSWORD at boot.

    No-op when app_password is falsy or the instance is already claimed — never
    overwrites a password the user has already set. Caller owns the transaction.
    """
    if not app_password:
        return
    if await is_setup_complete(session):
        return
    # Fresh DB: the atomic gate insert wins, materializing the APP_PASSWORD
    # rows. The bool return is irrelevant here (boot is single-threaded and we
    # already confirmed the instance is unclaimed); never raises on a clean DB.
    await claim_admin_password(session, app_password)


async def get_totp_secret(session: AsyncSession) -> str | None:
    return await _get_value(session, "totp_secret")


async def set_totp_secret(session: AsyncSession, secret: str | None) -> None:
    await _set_value(session, "totp_secret", secret)


async def is_totp_enabled(session: AsyncSession) -> bool:
    return await _get_value(session, "totp_enabled") == "true"


async def set_totp_enabled(session: AsyncSession, enabled: bool) -> None:
    await _set_value(session, "totp_enabled", "true" if enabled else "false")


async def get_token_epoch(session: AsyncSession) -> int:
    raw = await _get_value(session, "token_epoch")
    return int(raw) if raw is not None else 0


async def bump_token_epoch(session: AsyncSession) -> int:
    new = await get_token_epoch(session) + 1
    await _set_value(session, "token_epoch", str(new))
    return new
