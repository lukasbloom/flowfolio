"""Tests for services/trades.py and POST /api/trades.

Covers:
- Test 1: POST /api/trades creates two linked transactions with same trade_pair_id
- Test 2: POST /api/trades populates lot_alloc for the sell leg
- Test 3: POST /api/trades with same instrument on both sides returns 422
- Test 4: POST /api/trades where sell exceeds available lots returns 422; both legs rolled back
- Test 5: POST /api/trades persists both legs with the SAME trade_pair_id
- Test 6: POST /api/transactions with txn_type='spend' succeeds with FIFO
- Test 7: POST /api/transactions with txn_type='sell' returns 422
- Test 8: POST /api/transactions with txn_type='spend' AND trade_pair_id set returns 422
- Test 9: POST /api/transactions with txn_type='yield'/'adjustment' returns 422
"""
from __future__ import annotations

from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core import config as cfg_module
from app.core.database import Base, attach_sqlite_pragmas, get_db
from app.main import app
from app.models.lot_alloc import LotAlloc
from app.models.transaction import Transaction
from tests.conftest import seed_admin_password


@pytest_asyncio.fixture
async def client_and_session():
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


async def _create_account_and_instrument(
    client: AsyncClient, symbol: str = "BTC"
) -> tuple[str, str]:
    acct = (
        await client.post(
            "/api/accounts", json={"name": f"Acct-{symbol}", "account_type": "broker"}
        )
    ).json()
    inst = (
        await client.post(
            "/api/instruments",
            json={
                "symbol": symbol,
                "name": f"{symbol} Coin",
                "instrument_type": "crypto",
                "base_currency": "EUR",
                "price_source": "coingecko",
            },
        )
    ).json()
    return acct["id"], inst["id"]


async def _buy(client: AsyncClient, acct_id: str, inst_id: str, qty: str = "1.0") -> dict:
    resp = await client.post(
        "/api/transactions",
        json={
            "account_id": acct_id,
            "instrument_id": inst_id,
            "txn_type": "buy",
            "date": "2024-01-01",
            "quantity": qty,
            "unit_price": "100.0",
            "price_currency": "EUR",
            "fee_eur": "0",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Test 1 + 5: POST /api/trades creates two rows with same trade_pair_id
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_trades_creates_two_rows_with_same_pair_id(client_and_session):
    client, maker = client_and_session
    acct_id, btc_id = await _create_account_and_instrument(client, "BTC2")
    acct_id2, usdc_id = await _create_account_and_instrument(client, "USDC2")

    # Buy some BTC first so the sell has lots
    await _buy(client, acct_id, btc_id, qty="0.5")

    resp = await client.post(
        "/api/trades",
        json={
            "sold": {
                "account_id": acct_id,
                "instrument_id": btc_id,
                "quantity": "0.1",
                "unit_price": "50000",
                "price_currency": "EUR",
            },
            "received": {
                "account_id": acct_id2,
                "instrument_id": usdc_id,
                "quantity": "5000",
                "unit_price": "1.0",
                "price_currency": "EUR",
            },
            "date": "2024-06-01",
        },
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()

    assert "trade_pair_id" in data
    assert "sold_txn_id" in data
    assert "received_txn_id" in data
    assert data["sold_txn_id"] != data["received_txn_id"]

    # Verify both DB rows share the same trade_pair_id (Test 5)
    async with maker() as session:
        sell_result = await session.execute(
            select(Transaction).where(Transaction.id == data["sold_txn_id"])
        )
        sell = sell_result.scalar_one()
        buy_result = await session.execute(
            select(Transaction).where(Transaction.id == data["received_txn_id"])
        )
        buy = buy_result.scalar_one()

        assert sell.trade_pair_id == buy.trade_pair_id == data["trade_pair_id"]
        # Sell side is stored negative
        assert sell.quantity < 0
        # Buy side is positive
        assert buy.quantity > 0
        # Both have cost_basis_eur set
        assert sell.cost_basis_eur is not None
        assert buy.cost_basis_eur is not None


# ---------------------------------------------------------------------------
# Test 2: lot_alloc rows populated for sell leg
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_trades_populates_lot_alloc_for_sell_leg(client_and_session):
    client, maker = client_and_session
    acct_id, btc_id = await _create_account_and_instrument(client, "BTC3")
    acct_id2, usdc_id = await _create_account_and_instrument(client, "USDC3")

    await _buy(client, acct_id, btc_id, qty="1.0")

    resp = await client.post(
        "/api/trades",
        json={
            "sold": {
                "account_id": acct_id,
                "instrument_id": btc_id,
                "quantity": "0.5",
                "unit_price": "50000",
                "price_currency": "EUR",
            },
            "received": {
                "account_id": acct_id2,
                "instrument_id": usdc_id,
                "quantity": "25000",
                "unit_price": "1.0",
                "price_currency": "EUR",
            },
            "date": "2024-06-01",
        },
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()

    # Verify lot_alloc rows exist for the sell leg
    async with maker() as session:
        alloc_result = await session.execute(
            select(LotAlloc).where(LotAlloc.sell_txn_id == data["sold_txn_id"])
        )
        allocs = alloc_result.scalars().all()
        assert len(allocs) > 0
        total_consumed = sum(a.quantity for a in allocs)
        assert total_consumed == Decimal("0.5")


# ---------------------------------------------------------------------------
# Test 3: Same instrument on both sides returns 422
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_trades_same_instrument_returns_422(client_and_session):
    client, _ = client_and_session
    acct_id, btc_id = await _create_account_and_instrument(client, "BTC4")

    resp = await client.post(
        "/api/trades",
        json={
            "sold": {
                "account_id": acct_id,
                "instrument_id": btc_id,
                "quantity": "0.1",
                "unit_price": "50000",
                "price_currency": "EUR",
            },
            "received": {
                "account_id": acct_id,
                "instrument_id": btc_id,  # same!
                "quantity": "0.1",
                "unit_price": "50000",
                "price_currency": "EUR",
            },
            "date": "2024-06-01",
        },
    )
    assert resp.status_code == 422
    assert "differ" in resp.text.lower() or "must differ" in resp.text.lower()


# ---------------------------------------------------------------------------
# Test 4: FIFO insufficient lots rolls back both legs
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_trades_insufficient_lots_rolls_back_both_legs(client_and_session):
    client, maker = client_and_session
    acct_id, btc_id = await _create_account_and_instrument(client, "BTC5")
    acct_id2, usdc_id = await _create_account_and_instrument(client, "USDC5")

    # Buy only 0.1 BTC but try to sell 1.0 BTC
    await _buy(client, acct_id, btc_id, qty="0.1")

    resp = await client.post(
        "/api/trades",
        json={
            "sold": {
                "account_id": acct_id,
                "instrument_id": btc_id,
                "quantity": "1.0",  # exceeds available 0.1
                "unit_price": "50000",
                "price_currency": "EUR",
            },
            "received": {
                "account_id": acct_id2,
                "instrument_id": usdc_id,
                "quantity": "50000",
                "unit_price": "1.0",
                "price_currency": "EUR",
            },
            "date": "2024-06-01",
        },
    )
    assert resp.status_code == 422

    # Both legs must be rolled back — no sell/buy rows for this trade
    async with maker() as session:
        sell_result = await session.execute(
            select(Transaction).where(
                Transaction.txn_type == "sell",
                Transaction.instrument_id == btc_id,
            )
        )
        sells = sell_result.scalars().all()
        assert sells == [], "No sell row should persist after rollback"

        buy_result = await session.execute(
            select(Transaction).where(
                Transaction.txn_type == "buy",
                Transaction.instrument_id == usdc_id,
            )
        )
        buys = buy_result.scalars().all()
        assert buys == [], "No buy (received) row should persist after rollback"

        # No lot_alloc rows either
        alloc_result = await session.execute(
            select(LotAlloc).where(
                LotAlloc.sell_txn_id.in_(
                    select(Transaction.id).where(
                        Transaction.instrument_id == btc_id,
                        Transaction.txn_type == "sell",
                    )
                )
            )
        )
        assert alloc_result.scalars().all() == []


@pytest.mark.asyncio
async def test_trades_fx_upstream_error_returns_502_and_rolls_back(
    client_and_session, monkeypatch
):
    client, maker = client_and_session
    acct_id, btc_id = await _create_account_and_instrument(client, "BTCFX")
    acct_id2, usd_id = await _create_account_and_instrument(client, "USDFX")
    await _buy(client, acct_id, btc_id, qty="1.0")

    async def fail_fx(*args, **kwargs):
        raise ValueError("frankfurter network error: ConnectError")

    monkeypatch.setattr("app.services.trades.get_or_fetch_fx_rate", fail_fx)

    resp = await client.post(
        "/api/trades",
        json={
            "sold": {
                "account_id": acct_id,
                "instrument_id": btc_id,
                "quantity": "0.5",
                "unit_price": "50000",
                "price_currency": "EUR",
            },
            "received": {
                "account_id": acct_id2,
                "instrument_id": usd_id,
                "quantity": "25000",
                "unit_price": "1.0",
                "price_currency": "USD",
            },
            "date": "2024-06-01",
        },
    )

    assert resp.status_code == 502
    assert "fx upstream error" in resp.text
    async with maker() as session:
        persisted_received = await session.execute(
            select(Transaction).where(Transaction.instrument_id == usd_id)
        )
        assert persisted_received.scalars().all() == []


# ---------------------------------------------------------------------------
# Test 6: POST /api/transactions with txn_type='spend' succeeds
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_spend_transaction_succeeds(client_and_session):
    client, maker = client_and_session
    acct_id, btc_id = await _create_account_and_instrument(client, "BTC6")

    # Buy some BTC first
    await _buy(client, acct_id, btc_id, qty="1.0")

    # Spend some BTC (paying for something)
    resp = await client.post(
        "/api/transactions",
        json={
            "account_id": acct_id,
            "instrument_id": btc_id,
            "txn_type": "spend",
            "date": "2024-06-01",
            "quantity": "0.01",
            "unit_price": "50000",
            "price_currency": "EUR",
        },
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["txn_type"] == "spend"
    assert data["trade_pair_id"] is None

    # FIFO lot_alloc rows should be written for the consumed lots
    async with maker() as session:
        alloc_result = await session.execute(
            select(LotAlloc).where(LotAlloc.sell_txn_id == data["id"])
        )
        allocs = alloc_result.scalars().all()
        assert len(allocs) > 0, "spend should create lot_alloc rows"


# ---------------------------------------------------------------------------
# Test 7: POST /api/transactions with txn_type='sell' returns 422
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_sell_via_transactions_rejected(client_and_session):
    client, _ = client_and_session
    acct_id, btc_id = await _create_account_and_instrument(client, "BTC7")

    resp = await client.post(
        "/api/transactions",
        json={
            "account_id": acct_id,
            "instrument_id": btc_id,
            "txn_type": "sell",
            "date": "2024-06-01",
            "quantity": "0.1",
            "unit_price": "50000",
            "price_currency": "EUR",
        },
    )
    assert resp.status_code == 422
    assert "trades" in resp.text.lower()


# ---------------------------------------------------------------------------
# Test 8: POST /api/transactions with txn_type='spend' AND trade_pair_id set → 422
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_spend_with_trade_pair_id_rejected(client_and_session):
    client, _ = client_and_session
    acct_id, btc_id = await _create_account_and_instrument(client, "BTC8")
    import uuid

    resp = await client.post(
        "/api/transactions",
        json={
            "account_id": acct_id,
            "instrument_id": btc_id,
            "txn_type": "spend",
            "date": "2024-06-01",
            "quantity": "0.1",
            "unit_price": "50000",
            "price_currency": "EUR",
            "trade_pair_id": str(uuid.uuid4()),  # invalid for spend
        },
    )
    assert resp.status_code == 422
    assert "trade_pair_id" in resp.text.lower()


# ---------------------------------------------------------------------------
# Test 9: adjustment still rejected, yield now accepted
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_yield_now_accepted(client_and_session):
    """Yield is now user-creatable (returns 201, source='manual').

    Yield rows from /api/transactions are manual; the accrual cron sets source='accrual'.
    """
    client, _ = client_and_session
    acct_id, btc_id = await _create_account_and_instrument(client, "BTC9")

    resp = await client.post(
        "/api/transactions",
        json={
            "account_id": acct_id,
            "instrument_id": btc_id,
            "txn_type": "yield",
            "date": "2024-06-01",
            "quantity": "0.01",
        },
    )
    assert resp.status_code == 201, f"yield must be accepted (201), got: {resp.text}"
    assert resp.json()["source"] == "manual"


@pytest.mark.asyncio
async def test_adjustment_still_rejected(client_and_session):
    client, _ = client_and_session
    acct_id, btc_id = await _create_account_and_instrument(client, "BTC10")

    resp = await client.post(
        "/api/transactions",
        json={
            "account_id": acct_id,
            "instrument_id": btc_id,
            "txn_type": "adjustment",
            "date": "2024-06-01",
            "quantity": "0.01",
        },
    )
    assert resp.status_code == 422
