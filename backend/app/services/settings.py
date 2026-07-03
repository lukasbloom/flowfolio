from __future__ import annotations

"""User settings service.

Services do not commit or roll back transactions; routers own transaction
boundaries.
"""

from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user_setting import UserSetting

SETTING_KEYS_ALLOWLIST = ("concentration_threshold",)


async def get_settings(session: AsyncSession) -> dict[str, str]:
    """Return user-facing settings only.

    Restricted to SETTING_KEYS_ALLOWLIST so setup-owned secrets (the bcrypt
    admin_password_hash and the setup_complete flag, written by
    services/setup_state.py) are NEVER exposed through GET /api/settings.
    """
    result = await session.execute(
        select(UserSetting).where(UserSetting.key.in_(SETTING_KEYS_ALLOWLIST))
    )
    return {row.key: row.value for row in result.scalars()}


def validate_setting(key: str, value: str) -> None:
    """Raise ValueError if (key, value) does not satisfy the schema for this key."""
    if key not in SETTING_KEYS_ALLOWLIST:
        raise ValueError(f"Unknown setting key: {key!r}")
    if key == "concentration_threshold":
        try:
            threshold = Decimal(value)
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(
                f"concentration_threshold must be a decimal, got {value!r}"
            ) from exc
        if not (Decimal("0.01") <= threshold <= Decimal("0.99")):
            raise ValueError("concentration_threshold must be between 0.01 and 0.99")


async def upsert_setting(session: AsyncSession, key: str, value: str) -> UserSetting:
    validate_setting(key, value)
    result = await session.execute(select(UserSetting).where(UserSetting.key == key))
    row = result.scalar_one_or_none()
    if row is None:
        row = UserSetting(key=key, value=value)
        session.add(row)
    else:
        row.value = value
    return row
