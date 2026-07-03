"""DB-backed API-key store + in-process resolver cache.

The five provider keys live in user_setting rows, mirroring setup_state.py and
update_store.py. KEY_STORE_KEYS is derived from provider_registry so it can
never drift from the registry, and these keys are NEVER added to
SETTING_KEYS_ALLOWLIST — GET/PUT /api/settings can neither read nor write them.

Resolver cache: `_CACHE` is keyed by provider id (the route slug). Pricing
clients call get_api_key(id) with no DB session. The cache loads once at
boot (load_key_cache) and is updated in lock-step on every write (set_key /
clear_key), so a key change is live with no process restart. Status
reads return a last-4 mask + a boolean only — never a raw value.

Services do NOT commit; callers own the transaction boundary.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user_setting import UserSetting
from app.services.provider_registry import PROVIDERS

# Registry-derived user_setting keys. NEVER added to SETTING_KEYS_ALLOWLIST.
KEY_STORE_KEYS: tuple[str, ...] = tuple(p.setting_key for p in PROVIDERS)

# provider id -> raw key value (or None). Resolver cache + monkeypatchable seam.
_CACHE: dict[str, str | None] = {}


async def _get_value(session: AsyncSession, key: str) -> str | None:
    result = await session.execute(
        select(UserSetting.value).where(UserSetting.key == key)
    )
    return result.scalar_one_or_none()


async def _set_value(session: AsyncSession, key: str, value: str | None) -> None:
    """Upsert a single key-store row; delete on None. Caller commits."""
    result = await session.execute(select(UserSetting).where(UserSetting.key == key))
    row = result.scalar_one_or_none()
    if value is None:
        if row is not None:
            await session.delete(row)
        return
    if row is None:
        session.add(UserSetting(key=key, value=value))
    else:
        row.value = value


def _mask(value: str) -> str:
    """Last-4 mask. Values of <=4 chars are fully dotted (never reveal >4)."""
    if len(value) <= 4:
        return "•" * len(value)
    return "••••" + value[-4:]


async def load_key_cache(session: AsyncSession) -> None:
    """Boot-load the resolver cache from persisted rows (read-only)."""
    for provider in PROVIDERS:
        _CACHE[provider.id] = await _get_value(session, provider.setting_key)


def get_api_key(provider_id: str) -> str | None:
    """Resolve a key from the in-process cache only (no session)."""
    return _CACHE.get(provider_id)


async def set_key(session: AsyncSession, provider_id: str, value: str) -> None:
    """Upsert a provider key and refresh the cache so it is live (no restart)."""
    from app.services.provider_registry import get_provider

    provider = get_provider(provider_id)
    if provider is None:
        raise ValueError(f"Unknown provider: {provider_id!r}")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("key value must be a non-empty string")
    await _set_value(session, provider.setting_key, cleaned)
    _CACHE[provider_id] = cleaned


async def clear_key(session: AsyncSession, provider_id: str) -> None:
    """Delete a provider key row and clear it from the cache (no restart)."""
    from app.services.provider_registry import get_provider

    provider = get_provider(provider_id)
    if provider is None:
        raise ValueError(f"Unknown provider: {provider_id!r}")
    await _set_value(session, provider.setting_key, None)
    _CACHE[provider_id] = None


def get_key_status() -> list[dict]:
    """One entry per provider (registry order) with masked status, never a raw key."""
    status: list[dict] = []
    for provider in PROVIDERS:
        value = _CACHE.get(provider.id)
        configured = bool(value)
        entry = {
            "id": provider.id,
            "label": provider.label,
            "blurb": provider.blurb,
            "free_tier": provider.free_tier,
            "register_url": provider.register_url,
            "optional": provider.optional,
            "configured": configured,
            "masked": _mask(value) if configured and value is not None else None,
        }
        status.append(entry)
    return status
