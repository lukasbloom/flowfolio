"""Keys configuration API.

Routes (all session-gated by AuthMiddleware, never on AUTH_EXEMPT_PATHS):
  GET    /api/keys              registry + per-provider masked status + demo flag
  PUT    /api/keys/{provider}   test-then-persist (a failed live test blocks the save)
  POST   /api/keys/{provider}/test  run only the live test, persist nothing
  DELETE /api/keys/{provider}   clear a stored key

Demo write-lock: `forbid_in_demo` guards every mutation/test and returns 403 when
settings.demo_mode. GET masks every value in demo so no key is revealed.
The router owns commit/rollback; key_store stages only (caller-commits).
The request body / key value is NEVER logged.
"""
from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import forbid_in_demo
from app.schemas.keys import KeysResponse, KeyUpdate, ProviderStatus
from app.services import key_test
from app.services.key_store import clear_key, get_key_status, set_key
from app.services.provider_registry import get_provider

router = APIRouter(prefix="/api/keys", tags=["keys"])


def _require_provider(provider: str):  # type: ignore[no-untyped-def]
    entry = get_provider(provider)
    if entry is None:
        raise HTTPException(status_code=404, detail="Unknown provider")
    return entry


async def _run_test(provider: str, candidate_key: str) -> None:
    """Run the provider's live test; ValueError -> 422 (sanitized). No persistence."""
    test_fn = key_test.TEST_DISPATCH[provider]
    try:
        async with httpx.AsyncClient() as client:
            await test_fn(client, candidate_key)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("", response_model=KeysResponse)
async def list_keys() -> KeysResponse:
    providers = get_key_status()
    if settings.demo_mode:
        # Suppress every masked value so no stored key is revealed in demo.
        for entry in providers:
            entry["masked"] = None
    return KeysResponse(
        demo=settings.demo_mode,
        providers=[ProviderStatus(**entry) for entry in providers],
    )


@router.put("/{provider}", status_code=204, dependencies=[Depends(forbid_in_demo)])
async def set_provider_key(
    body: KeyUpdate,
    provider: str = Path(..., max_length=32, pattern="^[a-z_]+$"),
    db: AsyncSession = Depends(get_db),
) -> None:
    entry = _require_provider(provider)
    value = body.value.strip()

    if not value:
        # An empty value for the optional provider (github) clears it with NO
        # test call (empty-github path); a required provider rejects empty.
        if entry.optional:
            await clear_key(db, provider)
            await db.commit()
            return
        raise HTTPException(status_code=422, detail=f"{provider}: a value is required")

    # Test-then-persist: a failed live test blocks the save.
    try:
        await _run_test(provider, value)
    except HTTPException:
        await db.rollback()
        raise

    await set_key(db, provider, value)
    await db.commit()


@router.post("/{provider}/test", dependencies=[Depends(forbid_in_demo)])
async def test_provider_key(
    body: KeyUpdate,
    provider: str = Path(..., max_length=32, pattern="^[a-z_]+$"),
) -> dict[str, bool]:
    _require_provider(provider)
    value = body.value.strip()
    if not value:
        raise HTTPException(status_code=422, detail=f"{provider}: a value is required")
    await _run_test(provider, value)
    return {"ok": True}


@router.delete("/{provider}", status_code=204, dependencies=[Depends(forbid_in_demo)])
async def delete_provider_key(
    provider: str = Path(..., max_length=32, pattern="^[a-z_]+$"),
    db: AsyncSession = Depends(get_db),
) -> None:
    _require_provider(provider)
    await clear_key(db, provider)
    await db.commit()
