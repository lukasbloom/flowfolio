"""Tests for the reserved-instrument-id guard.

Test groups:
  1. POST /api/instruments rejects reserved tokens in `symbol` and `id`.
  2. PUT /api/instruments/{id} rejects reserved tokens in `symbol`.

(The original group 3 verified the one-time retroactive 0006 scan that aborted
`alembic upgrade head` on a pre-existing reserved row. That migration was folded
into the squashed baseline — there is no pre-existing data to scan on a fresh
schema — so the runtime API validator above is now the sole enforcement.)

Assertions on HTTP status use the 4xx class: Pydantic field_validator
returns 422; the spec text says "4xx"; tests assert the class, not the
exact code.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core import config as cfg_module
from app.core.database import Base, attach_sqlite_pragmas, get_db
from app.main import app
from app.models import Instrument
from tests.conftest import seed_admin_password

RESERVED = ["active", "closed", "catalog", "i"]


# --- Fixtures (copied from test_api_instruments.py:22-82) -----------------


@pytest_asyncio.fixture
async def client():
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
        yield c

    app.dependency_overrides.clear()
    await engine.dispose()
    cfg_module.settings.app_password = original_password


@pytest_asyncio.fixture
async def client_with_maker():
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


# --- Test 1: POST /api/instruments rejects reserved tokens ----------------


@pytest.mark.asyncio
@pytest.mark.parametrize("reserved", RESERVED)
async def test_post_reserved_symbol_rejected(client, reserved):
    resp = await client.post(
        "/api/instruments",
        json={
            "symbol": reserved,
            "name": "Reserved Test",
            "instrument_type": "stock",
            "base_currency": "USD",
            "price_source": "finnhub",
        },
    )
    # Assert on the 4xx class, not the literal 422.
    assert 400 <= resp.status_code < 500, (
        f"Expected 4xx for reserved symbol {reserved!r}, got {resp.status_code}: {resp.text}"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("reserved", RESERVED)
async def test_post_reserved_id_rejected(client, reserved):
    resp = await client.post(
        "/api/instruments",
        json={
            "id": reserved,
            "symbol": "AAPL-TEST",
            "name": "ID Reserved Test",
            "instrument_type": "stock",
            "base_currency": "USD",
            "price_source": "finnhub",
        },
    )
    assert 400 <= resp.status_code < 500, (
        f"Expected 4xx for reserved id {reserved!r}, got {resp.status_code}: {resp.text}"
    )


@pytest.mark.asyncio
async def test_post_valid_symbol_still_accepted(client):
    """Regression: a normal, non-reserved symbol must still create successfully."""
    resp = await client.post(
        "/api/instruments",
        json={
            "symbol": "AAPL",
            "name": "Apple",
            "instrument_type": "stock",
            "base_currency": "USD",
            "price_source": "finnhub",
        },
    )
    assert resp.status_code == 201, resp.text


# --- Test 2: PUT /api/instruments/{id} rejects reserved tokens ------------


@pytest.mark.asyncio
@pytest.mark.parametrize("reserved", RESERVED)
async def test_put_reserved_symbol_rejected(client_with_maker, reserved):
    c, maker = client_with_maker
    # Seed a valid instrument via SQLAlchemy directly (bypasses the validator
    # path that the PUT will exercise).
    async with maker() as s:
        inst = Instrument(
            symbol="SEED",
            name="Seed",
            instrument_type="stock",
            base_currency="USD",
            price_source="finnhub",
        )
        s.add(inst)
        await s.commit()
        await s.refresh(inst)
        seed_id = inst.id

    resp = await c.put(
        f"/api/instruments/{seed_id}",
        json={
            "symbol": reserved,
            "name": "Renamed",
            "instrument_type": "stock",
            "base_currency": "USD",
            "price_source": "finnhub",
        },
    )
    assert 400 <= resp.status_code < 500, (
        f"Expected 4xx for PUT with reserved symbol {reserved!r}, got "
        f"{resp.status_code}: {resp.text}"
    )


# Regression: a successful PUT (no `id` in body) must not overwrite the
# primary key. Before the fix, the InstrumentCreate `id: Optional[str] = None`
# field flowed through the router's `setattr` loop and clobbered `inst.id` with
# None, orphaning every Transaction / HoldingTag / PriceQuote FK on the row.
@pytest.mark.asyncio
async def test_put_valid_payload_preserves_primary_key(client_with_maker):
    c, maker = client_with_maker
    async with maker() as s:
        inst = Instrument(
            symbol="AAPL",
            name="Apple Inc.",
            instrument_type="stock",
            base_currency="USD",
            price_source="finnhub",
        )
        s.add(inst)
        await s.commit()
        await s.refresh(inst)
        seed_id = inst.id

    resp = await c.put(
        f"/api/instruments/{seed_id}",
        json={
            "symbol": "AAPL",
            "name": "Apple Inc. (renamed)",
            "instrument_type": "stock",
            "base_currency": "USD",
            "price_source": "finnhub",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == seed_id, (
        f"PUT must preserve the primary key; got id={body['id']!r}, "
        f"expected {seed_id!r}"
    )

    # Verify the row is still reachable by its original id.
    follow = await c.get(f"/api/instruments/{seed_id}")
    assert follow.status_code == 200
    assert follow.json()["id"] == seed_id
    assert follow.json()["name"] == "Apple Inc. (renamed)"
