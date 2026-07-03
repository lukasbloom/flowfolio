"""Tests for GET /api/instruments/{instrument_id}/holdings.

Behavior contract:
    1. Unknown instrument_id returns 200 + [] (NOT 404 — empty-state UI is the
       FE's responsibility; a 404 would force defensive handling).
    2. One pair returned per account that has any non-deleted transaction for
       this instrument.
    3. Currently attached tags ride along on each pair.
    4. Two accounts holding the same instrument yield two pairs, sorted by
       account_name ASC.
    5. Soft-deleted-only transactions do NOT surface as holdings.
    6. AuthMiddleware gates the route — no cookie → 401.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core import config as cfg_module
from app.core.database import Base, attach_sqlite_pragmas, get_db
from app.main import app
from app.models import Account, Instrument, Transaction
from tests.conftest import seed_admin_password


# ---------------------------------------------------------------------------
# Module-local fixtures (mirror tests/services/test_tags.py — pytest_asyncio
# fixtures are scoped per-module by default in this repo's test suite).
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture
async def authed_client():
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
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        login = await client.post(
            "/api/auth/login", json={"password": "test-password-123"}
        )
        assert login.status_code == 200
        yield client, maker

    app.dependency_overrides.clear()
    await engine.dispose()
    cfg_module.settings.app_password = original_password


@pytest_asyncio.fixture
async def unauthed_client():
    """A client that has NEVER called /api/auth/login — used to verify the
    new route is gated by AuthMiddleware (test_instrument_holdings_requires_auth).
    """
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
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, maker

    app.dependency_overrides.clear()
    await engine.dispose()
    cfg_module.settings.app_password = original_password


async def _create_account_and_instrument(maker, account_name: str = "Test Account"):
    """Create an Account, an Instrument, and a non-deleted buy Transaction
    binding the two. Returns (account_id, instrument_id)."""
    async with maker() as s:
        account = Account(
            name=account_name, account_type="broker", currency="EUR"
        )
        instrument = Instrument(
            symbol="BTC",
            name="Bitcoin",
            instrument_type="crypto",
            risk_level="High",
            base_currency="EUR",
            price_source="coingecko",
        )
        s.add_all([account, instrument])
        await s.flush()
        txn = Transaction(
            account_id=account.id,
            instrument_id=instrument.id,
            txn_type="buy",
            date=date.today(),
            quantity=Decimal("1"),
            unit_price=Decimal("30000"),
            price_currency="EUR",
            fx_rate_to_eur=Decimal("1"),
            cost_basis_eur=Decimal("30000"),
        )
        s.add(txn)
        await s.commit()
        return account.id, instrument.id


async def _create_second_account_with_holding(
    maker, instrument_id: str, account_name: str = "Second Account"
):
    """Create a second Account and a buy Transaction for the SAME instrument,
    so we can test multi-account holdings."""
    async with maker() as s:
        account = Account(
            name=account_name, account_type="broker", currency="EUR"
        )
        s.add(account)
        await s.flush()
        txn = Transaction(
            account_id=account.id,
            instrument_id=instrument_id,
            txn_type="buy",
            date=date.today(),
            quantity=Decimal("0.5"),
            unit_price=Decimal("31000"),
            price_currency="EUR",
            fx_rate_to_eur=Decimal("1"),
            cost_basis_eur=Decimal("15500"),
        )
        s.add(txn)
        await s.commit()
        return account.id


async def _create_account_and_instrument_softdeleted_only(maker):
    """Create an Account + Instrument whose ONLY transaction is soft-deleted.
    The route should NOT surface this pair as a holding."""
    async with maker() as s:
        account = Account(
            name="Soft Delete Account", account_type="broker", currency="EUR"
        )
        instrument = Instrument(
            symbol="ETH",
            name="Ethereum",
            instrument_type="crypto",
            risk_level="High",
            base_currency="EUR",
            price_source="coingecko",
        )
        s.add_all([account, instrument])
        await s.flush()
        txn = Transaction(
            account_id=account.id,
            instrument_id=instrument.id,
            txn_type="buy",
            date=date.today(),
            quantity=Decimal("2"),
            unit_price=Decimal("2000"),
            price_currency="EUR",
            fx_rate_to_eur=Decimal("1"),
            cost_basis_eur=Decimal("4000"),
            deleted_at=datetime.utcnow(),
        )
        s.add(txn)
        await s.commit()
        return account.id, instrument.id


# ---------------------------------------------------------------------------
# Test 1: Unknown instrument_id → 200 + []
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_instrument_holdings_returns_empty_for_unknown_instrument(
    authed_client,
):
    client, _ = authed_client

    response = await client.get(
        "/api/instruments/non-existent-instrument-id/holdings"
    )

    assert response.status_code == 200
    assert response.json() == []


# ---------------------------------------------------------------------------
# Test 2: One holding in one account → one element with empty tags array
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_instrument_holdings_returns_one_pair_when_held_in_one_account(
    authed_client,
):
    client, maker = authed_client
    account_id, instrument_id = await _create_account_and_instrument(maker)

    response = await client.get(f"/api/instruments/{instrument_id}/holdings")

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    assert len(body) == 1
    pair = body[0]
    assert pair["account_id"] == account_id
    assert pair["account_name"] == "Test Account"
    assert pair["tags"] == []


# ---------------------------------------------------------------------------
# Test 3: After attaching a tag, the route returns it
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_instrument_holdings_includes_attached_tag(authed_client):
    client, maker = authed_client
    account_id, instrument_id = await _create_account_and_instrument(maker)

    tag_resp = await client.post("/api/tags", json={"name": "growth"})
    assert tag_resp.status_code == 201
    tag_id = tag_resp.json()["id"]

    apply_resp = await client.post(
        f"/api/holdings/{account_id}/{instrument_id}/tags",
        json={"tag_id": tag_id},
    )
    assert apply_resp.status_code == 204

    response = await client.get(f"/api/instruments/{instrument_id}/holdings")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    pair = body[0]
    assert pair["account_id"] == account_id
    assert len(pair["tags"]) == 1
    tag = pair["tags"][0]
    assert tag["id"] == tag_id
    assert tag["name"] == "growth"
    # holdings_count is part of the TagResponse contract; per the service
    # docstring it is set to 0 inside the per-instrument payload (the FE does
    # not need cascade preview here — that lives on GET /api/tags).
    assert tag["holdings_count"] == 0


# ---------------------------------------------------------------------------
# Test 4: Two accounts holding the same instrument → two pairs, sorted by name
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_instrument_holdings_returns_two_pairs_for_multi_account_holding(
    authed_client,
):
    client, maker = authed_client
    # First account is "Test Account" (alphabetically AFTER "Bit2Me-style")
    account_a_id, instrument_id = await _create_account_and_instrument(maker)
    # Second account name is chosen so it sorts BEFORE "Test Account"
    account_b_id = await _create_second_account_with_holding(
        maker, instrument_id, account_name="Bit2Me-style"
    )

    # Attach a tag to ONE pair only — verify each pair carries its own tag list
    tag_resp = await client.post("/api/tags", json={"name": "speculative"})
    tag_id = tag_resp.json()["id"]
    apply_resp = await client.post(
        f"/api/holdings/{account_a_id}/{instrument_id}/tags",
        json={"tag_id": tag_id},
    )
    assert apply_resp.status_code == 204

    response = await client.get(f"/api/instruments/{instrument_id}/holdings")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2
    # Sorted by account_name ASC: "Bit2Me-style" < "Test Account"
    assert body[0]["account_name"] == "Bit2Me-style"
    assert body[0]["account_id"] == account_b_id
    assert body[0]["tags"] == []
    assert body[1]["account_name"] == "Test Account"
    assert body[1]["account_id"] == account_a_id
    assert len(body[1]["tags"]) == 1
    assert body[1]["tags"][0]["name"] == "speculative"


# ---------------------------------------------------------------------------
# Test 5: Soft-deleted-only transactions do NOT surface a holding
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_instrument_holdings_excludes_soft_deleted_only_holdings(
    authed_client,
):
    client, maker = authed_client
    _, instrument_id = await _create_account_and_instrument_softdeleted_only(maker)

    response = await client.get(f"/api/instruments/{instrument_id}/holdings")

    assert response.status_code == 200
    assert response.json() == []


# ---------------------------------------------------------------------------
# Test 6: No auth cookie → 401 (proves AuthMiddleware coverage)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_instrument_holdings_requires_auth(unauthed_client):
    client, _ = unauthed_client

    response = await client.get("/api/instruments/anything/holdings")

    assert response.status_code == 401
