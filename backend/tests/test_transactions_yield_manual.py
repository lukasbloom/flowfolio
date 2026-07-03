"""Regression tests: manual yield POST is accepted; adjustment still rejected.

These tests gate the manual YieldForm and e2e fixture path.

Conventions mirror test_api_transactions.py:
- Uses httpx.AsyncClient with ASGITransport (no live server required)
- In-memory SQLite DB with schema created fresh per test
- Authenticates via /api/auth/login before exercising the endpoint
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core import config as cfg_module
from app.core.database import Base, attach_sqlite_pragmas, get_db
from app.main import app
from tests.conftest import seed_admin_password


@pytest_asyncio.fixture
async def client():
    """Authenticated ASGI test client with an isolated in-memory DB."""
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


async def _seed_account_and_instrument(client: AsyncClient) -> tuple[str, str]:
    """Create a broker account + stablecoin instrument; return (account_id, instrument_id)."""
    acct = (
        await client.post(
            "/api/accounts",
            json={"name": "Revolut Earn", "account_type": "broker"},
        )
    ).json()
    inst = (
        await client.post(
            "/api/instruments",
            json={
                "symbol": "ETH",
                "name": "Ethereum",
                "instrument_type": "crypto",
                "base_currency": "USD",
                "price_source": "coingecko",
            },
        )
    ).json()
    return acct["id"], inst["id"]


@pytest.mark.asyncio
async def test_manual_yield_post_succeeds_with_explicit_source(client):
    """POST yield with source='manual' returns 201 (manual-yield create path)."""
    acct_id, inst_id = await _seed_account_and_instrument(client)
    resp = await client.post(
        "/api/transactions",
        json={
            "account_id": acct_id,
            "instrument_id": inst_id,
            "txn_type": "yield",
            "date": "2026-05-12",
            "quantity": "0.001",
            "notes": "Manual yield from broker statement",
            "source": "manual",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["txn_type"] == "yield"
    assert body["source"] == "manual"
    assert body["unit_price"] is None  # yield rows don't require unit_price


@pytest.mark.asyncio
async def test_manual_yield_post_defaults_source_to_manual(client):
    """Omitting `source` defaults to 'manual' (TransactionCreate.validate_source normalises None → 'manual')."""
    acct_id, inst_id = await _seed_account_and_instrument(client)
    resp = await client.post(
        "/api/transactions",
        json={
            "account_id": acct_id,
            "instrument_id": inst_id,
            "txn_type": "yield",
            "date": "2026-05-12",
            "quantity": "0.001",
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["source"] == "manual"


@pytest.mark.asyncio
async def test_yield_post_with_source_accrual_succeeds(client):
    """source='accrual' + 'auto-accrual ' notes prefix mimics the daily APScheduler row.

    The Playwright fixture uses this path to seed an auto-accrual yield row
    without having to wait for the cron tick. EditTxnDialog's read-only ActionBanner
    is triggered by detecting the 'auto-accrual ' notes prefix.
    """
    acct_id, inst_id = await _seed_account_and_instrument(client)
    resp = await client.post(
        "/api/transactions",
        json={
            "account_id": acct_id,
            "instrument_id": inst_id,
            "txn_type": "yield",
            "date": "2026-05-12",
            "quantity": "0.001",
            "notes": "auto-accrual 2.37% APY",
            "source": "accrual",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["source"] == "accrual"
    assert body["notes"].startswith("auto-accrual ")


@pytest.mark.asyncio
async def test_adjustment_post_still_rejected(client):
    """Regression guard: the rejection still applies, adjustment rows are reconciliation-only.

    The rejection was narrowed from {yield, adjustment} → {adjustment}; this test
    ensures adjustment alone is still 422 with a message identifying the reconciliation engine.
    """
    acct_id, inst_id = await _seed_account_and_instrument(client)
    resp = await client.post(
        "/api/transactions",
        json={
            "account_id": acct_id,
            "instrument_id": inst_id,
            "txn_type": "adjustment",
            "date": "2026-05-12",
            "quantity": "0.001",
        },
    )
    assert resp.status_code == 422
    body_text = resp.text.lower()
    # The new error message explicitly identifies the adjustment restriction.
    assert "adjustment" in body_text
    assert "reconciliation" in body_text


@pytest.mark.asyncio
async def test_yield_post_invalid_source_rejected(client):
    """source must be one of VALID_SOURCES = {'manual', 'accrual', 'adjustment'}."""
    acct_id, inst_id = await _seed_account_and_instrument(client)
    resp = await client.post(
        "/api/transactions",
        json={
            "account_id": acct_id,
            "instrument_id": inst_id,
            "txn_type": "yield",
            "date": "2026-05-12",
            "quantity": "0.001",
            "source": "BOGUS",
        },
    )
    assert resp.status_code == 422
