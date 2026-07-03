"""CI parity test, a wire-level assertion that FxRateStr quantizes correctly.

Seeds two fixture FxRate rows:
- 1.17615: 5dp rate exercising round-half-to-even (.1 odd → rounds to even neighbor .2)
- 1.17625: banker's-rounding edge (.2 even → .5 rounds to even (stays at .2))

Both must round to "1.1762" — proves ROUND_HALF_EVEN active end-to-end (NOT half-up).
If a future change to ROUND_HALF_UP were silently introduced, the second test would
emit "1.1763" and fail loudly.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core import config as cfg_module
from app.core.database import Base, attach_sqlite_pragmas, get_db
from app.main import app
from app.models.fx_rate import FxRate
from tests.conftest import seed_admin_password


@pytest_asyncio.fixture
async def authed_client():
    # Same fixture shape as tests/test_fx_router.py — in-memory SQLite + login cookie.
    original_password = cfg_module.settings.app_password
    cfg_module.settings.app_password = "test-password-123"

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    attach_sqlite_pragmas(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_db():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_db] = override_db
    await seed_admin_password(maker, "test-password-123")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        login = await c.post("/api/auth/login", json={"password": "test-password-123"})
        assert login.status_code == 200
        yield c, maker
    app.dependency_overrides.clear()
    await engine.dispose()
    cfg_module.settings.app_password = original_password


@pytest.mark.asyncio
async def test_fx_rate_response_quantized_to_4dp(authed_client):
    """5dp source rate (1.17615) → 4dp banker's-rounded wire string ("1.1762")."""
    client, maker = authed_client
    async with maker() as s:
        s.add(
            FxRate(
                date=date(2025, 5, 1),
                base_currency="EUR",
                quote_currency="USD",
                rate=Decimal("1.17615"),
                source="frankfurter",
            )
        )
        await s.commit()

    resp = await client.get("/api/fx/2025-05-01?from=USD&to=EUR")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["rate"] == "1.1762", (
        f"Expected '1.1762' (ROUND_HALF_EVEN: .17615 → .1762, "
        f"odd digit before .5 rounds away to even), got {body['rate']!r}"
    )


@pytest.mark.asyncio
async def test_fx_rate_bankers_rounding_edge(authed_client):
    """Decimal('1.17625') under ROUND_HALF_EVEN → '1.1762' (NOT '1.1763').

    Digit before .5 is 2 (even); half-rounds-to-even keeps it at the even neighbor.
    If this assertion fires '1.1763' instead, the serializer has silently switched
    to ROUND_HALF_UP — which would re-introduce the H7 client/server FX drift.
    """
    client, maker = authed_client
    async with maker() as s:
        s.add(
            FxRate(
                date=date(2025, 5, 2),
                base_currency="EUR",
                quote_currency="USD",
                rate=Decimal("1.17625"),
                source="frankfurter",
            )
        )
        await s.commit()

    resp = await client.get("/api/fx/2025-05-02?from=USD&to=EUR")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["rate"] == "1.1762", (
        f"Banker's-rounding edge failed: Decimal('1.17625') should round to '1.1762' "
        f"under ROUND_HALF_EVEN (digit before .5 is 2 — even). Got {body['rate']!r}. "
        f"If this is '1.1763', the serializer is using ROUND_HALF_UP, a contract violation."
    )
