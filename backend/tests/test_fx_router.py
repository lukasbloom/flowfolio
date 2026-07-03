"""/api/fx router tests.

Pattern: ASGITransport + AsyncClient (matches test_api_transactions.py). The
service module's httpx call is intercepted by monkey-patching httpx.AsyncClient
to a MockTransport-backed client when needed.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core import config as cfg_module
from app.core.database import Base, attach_sqlite_pragmas, get_db
from app.main import app
from app.models.fx_rate import FxRate
from tests.conftest import seed_admin_password


@pytest_asyncio.fixture
async def authed_client():
    """Authenticated AsyncClient + sessionmaker for the /api/fx router."""
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


@pytest_asyncio.fixture
async def unauthed_client():
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
        yield c
    app.dependency_overrides.clear()
    await engine.dispose()


def _frankfurter_handler(rate: str, date_str: str):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "amount": 1,
                "base": "EUR",
                "date": date_str,
                "rates": {"USD": float(rate)},
            },
        )

    return handler


# ---------------------------------------------------------------------------
# Auth gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_fx_unauthenticated_401(unauthed_client):
    resp = await unauthed_client.get("/api/fx?timeframe=3m")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/fx/{date} — on-demand rate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_fx_for_date_cache_hit(authed_client):
    """Pre-inserted FxRate row → returned without HTTP call."""
    client, maker = authed_client
    async with maker() as s:
        s.add(
            FxRate(
                date=date(2025, 1, 15),
                base_currency="EUR",
                quote_currency="USD",
                rate=Decimal("1.0234"),
                source="frankfurter",
            )
        )
        await s.commit()

    resp = await client.get("/api/fx/2025-01-15?from=USD&to=EUR")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["date"] == "2025-01-15"
    assert isinstance(data["rate"], str)
    assert Decimal(data["rate"]) == Decimal("1.0234")
    assert data["source"] == "frankfurter"


@pytest.mark.asyncio
async def test_get_fx_for_date_cache_miss_fetches(authed_client, monkeypatch):
    """Cache empty → router calls Frankfurter (mocked) → row appears in DB."""
    client, maker = authed_client

    # Patch httpx.AsyncClient to use MockTransport
    real_async_client = httpx.AsyncClient
    transport = httpx.MockTransport(_frankfurter_handler("1.0501", "2025-03-03"))

    def _patched(*args, **kwargs):
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr("app.routers.fx.httpx.AsyncClient", _patched)

    resp = await client.get("/api/fx/2025-03-03?from=USD&to=EUR")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert Decimal(data["rate"]) == Decimal("1.0501")

    # DB write-through
    async with maker() as s:
        result = await s.execute(
            select(FxRate).where(FxRate.date == date(2025, 3, 3))
        )
        row = result.scalar_one()
    assert row.source == "frankfurter"


@pytest.mark.asyncio
async def test_get_fx_for_date_same_currency_400(authed_client):
    client, _ = authed_client
    resp = await client.get("/api/fx/2025-01-15?from=EUR&to=EUR")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_get_fx_for_date_invalid_currency_400(authed_client):
    client, _ = authed_client
    resp = await client.get("/api/fx/2025-01-15?from=GBP&to=EUR")
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# GET /api/fx — history list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_fx_default_3m(authed_client):
    """Pre-insert rows spanning >90 days; default timeframe returns ≤50 rows within last 90 days."""
    client, maker = authed_client
    async with maker() as s:
        # Insert 100 rows spaced 1 day apart, ending today
        for i in range(100):
            s.add(
                FxRate(
                    date=date.today() - timedelta(days=i),
                    base_currency="EUR",
                    quote_currency="USD",
                    rate=Decimal("1.05") + Decimal(i) / Decimal("10000"),
                    source="frankfurter",
                )
            )
        await s.commit()

    resp = await client.get("/api/fx")  # defaults: timeframe=3m, limit=50
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) <= 50
    cutoff = date.today() - timedelta(days=90)
    for row in rows:
        assert date.fromisoformat(row["date"]) >= cutoff


@pytest.mark.asyncio
async def test_list_fx_pagination(authed_client):
    """70 rows, ?limit=50&offset=50 returns the 20 oldest within timeframe."""
    client, maker = authed_client
    async with maker() as s:
        for i in range(70):
            s.add(
                FxRate(
                    date=date.today() - timedelta(days=i),
                    base_currency="EUR",
                    quote_currency="USD",
                    rate=Decimal("1.05"),
                    source="frankfurter",
                )
            )
        await s.commit()

    resp = await client.get("/api/fx?timeframe=3m&limit=50&offset=50")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 20


# ---------------------------------------------------------------------------
# POST /api/fx/manual
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_manual_override_creates_row(authed_client):
    client, maker = authed_client
    resp = await client.post(
        "/api/fx/manual",
        json={
            "date": "2025-04-01",
            "base_currency": "EUR",
            "quote_currency": "USD",
            "rate": "1.0850",
            "source": "manual",
        },
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["source"] == "manual"
    assert Decimal(data["rate"]) == Decimal("1.0850")

    async with maker() as s:
        result = await s.execute(
            select(FxRate).where(FxRate.date == date(2025, 4, 1))
        )
        row = result.scalar_one()
    assert row.source == "manual"


@pytest.mark.asyncio
async def test_post_manual_override_upserts_same_date(authed_client):
    """Pre-insert frankfurter row, POST manual override for same date → row updated in place."""
    client, maker = authed_client
    async with maker() as s:
        s.add(
            FxRate(
                date=date(2025, 4, 2),
                base_currency="EUR",
                quote_currency="USD",
                rate=Decimal("1.0700"),
                source="frankfurter",
            )
        )
        await s.commit()

    resp = await client.post(
        "/api/fx/manual",
        json={
            "date": "2025-04-02",
            "base_currency": "EUR",
            "quote_currency": "USD",
            "rate": "1.0900",
            "source": "manual",
        },
    )
    assert resp.status_code == 201, resp.text

    async with maker() as s:
        result = await s.execute(
            select(FxRate).where(FxRate.date == date(2025, 4, 2))
        )
        rows = result.scalars().all()
    assert len(rows) == 1
    assert rows[0].source == "manual"
    assert rows[0].rate == Decimal("1.0900")


@pytest.mark.asyncio
async def test_post_manual_override_rejects_non_manual_source(authed_client):
    client, _ = authed_client
    resp = await client.post(
        "/api/fx/manual",
        json={
            "date": "2025-04-03",
            "base_currency": "EUR",
            "quote_currency": "USD",
            "rate": "1.05",
            "source": "frankfurter",
        },
    )
    assert resp.status_code == 400
