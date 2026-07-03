"""Tests for /api/transactions CRUD with FIFO cascade.

Critical assertions:
- POST /api/transactions with txn_type=sell creates lot_alloc rows atomically
- DELETE on a sell cascades the deletion of its lot_alloc rows (via ON DELETE CASCADE)
- Monetary fields are returned as strings (not floats) preserving NUMERIC precision
- yield txn type is accepted (manual yield POST returns 201)
- adjustment txn type is rejected (system-created only)
- Non-EUR transactions require fx_rate_to_eur
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
from tests.conftest import seed_admin_password


@pytest_asyncio.fixture
async def client_and_session():
    original_password = cfg_module.settings.app_password
    cfg_module.settings.app_password = "test-password-123"

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    # MUST attach pragmas (foreign_keys=ON) so ON DELETE CASCADE on lot_alloc fires.
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


async def _create_account_and_instrument(client):
    acct = (
        await client.post(
            "/api/accounts", json={"name": "TestAccount", "account_type": "broker"}
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
async def test_buy_transaction_creates_record(client_and_session):
    client, _ = client_and_session
    acct_id, inst_id = await _create_account_and_instrument(client)
    resp = await client.post(
        "/api/transactions",
        json={
            "account_id": acct_id,
            "instrument_id": inst_id,
            "txn_type": "buy",
            "date": "2024-01-15",
            "quantity": "2.5",
            "unit_price": "2000.00",
            "price_currency": "USD",
            "fx_rate_to_eur": "1.1",
            "fee_eur": "5.00",
        },
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["txn_type"] == "buy"
    # Returned as a Decimal-as-string; compare numerically
    assert Decimal(data["quantity"]) == Decimal("2.5")
    # cost_basis_eur = 2.5 * 2000 / 1.1 = 4545.45...
    assert data["cost_basis_eur"] is not None


@pytest.mark.asyncio
async def test_sell_creates_lot_alloc(client_and_session):
    """POST /api/transactions rejects bare sell. Sell via /api/trades.
    This test verifies the sell rejection + that lot_alloc is created via session insert
    (mimicking what POST /api/trades will do).
    """
    client, maker = client_and_session
    acct_id, inst_id = await _create_account_and_instrument(client)

    # Buy 10 units via API
    buy_resp = await client.post(
        "/api/transactions",
        json={
            "account_id": acct_id,
            "instrument_id": inst_id,
            "txn_type": "buy",
            "date": "2024-01-01",
            "quantity": "10",
            "unit_price": "1000",
            "price_currency": "USD",
            "fx_rate_to_eur": "1.1",
        },
    )
    assert buy_resp.status_code == 201

    # POST /api/transactions with txn_type='sell' must be rejected
    reject_resp = await client.post(
        "/api/transactions",
        json={
            "account_id": acct_id,
            "instrument_id": inst_id,
            "txn_type": "sell",
            "date": "2024-06-01",
            "quantity": "6",
            "unit_price": "1500",
            "price_currency": "USD",
            "fx_rate_to_eur": "1.08",
        },
    )
    assert reject_resp.status_code == 422, "sell via /api/transactions must be rejected"
    assert "trades" in reject_resp.text.lower()

    # Create sell via session (simulating what POST /api/trades will do)
    from app.models.transaction import Transaction as Txn
    from app.services.fifo import match_lots_for_sell
    async with maker() as session:
        sell_txn = Txn(
            account_id=acct_id,
            instrument_id=inst_id,
            txn_type="sell",
            date=__import__("datetime").date(2024, 6, 1),
            quantity=Decimal("-6"),
            unit_price=Decimal("1500"),
            price_currency="USD",
            fx_rate_to_eur=Decimal("1.08"),
        )
        session.add(sell_txn)
        await session.flush()
        await match_lots_for_sell(session, sell_txn)
        await session.commit()

    # Verify lot_alloc row was created
    async with maker() as session:
        result = await session.execute(select(LotAlloc))
        allocs = result.scalars().all()
    assert len(allocs) == 1
    assert allocs[0].quantity == Decimal("6")


@pytest.mark.asyncio
async def test_delete_sell_cascades_lot_alloc(client_and_session):
    """DELETE on a sell soft-deletes it and releases lot_alloc rows."""
    client, maker = client_and_session
    acct_id, inst_id = await _create_account_and_instrument(client)

    await client.post(
        "/api/transactions",
        json={
            "account_id": acct_id,
            "instrument_id": inst_id,
            "txn_type": "buy",
            "date": "2024-01-01",
            "quantity": "10",
            "unit_price": "1000",
            "price_currency": "USD",
            "fx_rate_to_eur": "1.1",
        },
    )

    # Create sell via session (bare sells rejected at API level)
    from app.models.transaction import Transaction as Txn
    from app.services.fifo import match_lots_for_sell
    async with maker() as session:
        sell_txn = Txn(
            account_id=acct_id,
            instrument_id=inst_id,
            txn_type="sell",
            date=__import__("datetime").date(2024, 6, 1),
            quantity=Decimal("-5"),
            unit_price=Decimal("1500"),
            price_currency="USD",
            fx_rate_to_eur=Decimal("1.08"),
        )
        session.add(sell_txn)
        await session.flush()
        await match_lots_for_sell(session, sell_txn)
        await session.commit()
        sell_id = sell_txn.id

    # Delete the sell (soft-delete + lot_alloc release)
    del_resp = await client.delete(f"/api/transactions/{sell_id}")
    assert del_resp.status_code == 204

    # lot_alloc rows must be released (not via CASCADE since soft-delete, but via
    # _delete_lot_allocs_for_sell called in the DELETE handler)
    async with maker() as session:
        result = await session.execute(select(LotAlloc))
        allocs = result.scalars().all()
    assert len(allocs) == 0


@pytest.mark.asyncio
async def test_update_buy_rejects_explicit_unit_price_clear(client_and_session):
    """PUT cannot null out unit_price/price_currency on a buy/spend.
    Editing other fields on a row that already has nulls is still fine —
    the guard only fires when the request explicitly clears the field."""
    client, _ = client_and_session
    acct_id, inst_id = await _create_account_and_instrument(client)
    create_resp = await client.post(
        "/api/transactions",
        json={
            "account_id": acct_id,
            "instrument_id": inst_id,
            "txn_type": "buy",
            "date": "2024-01-15",
            "quantity": "1",
            "unit_price": "100",
            "price_currency": "USD",
            "fx_rate_to_eur": "1.1",
        },
    )
    assert create_resp.status_code == 201
    txn_id = create_resp.json()["id"]

    # Explicit null is rejected.
    bad = await client.put(f"/api/transactions/{txn_id}", json={"unit_price": None})
    assert bad.status_code == 422
    assert "unit_price" in bad.text

    # Updating other fields without touching the price columns still works.
    ok = await client.put(f"/api/transactions/{txn_id}", json={"notes": "edited"})
    assert ok.status_code == 200, ok.text


@pytest.mark.asyncio
async def test_update_sell_recomputes_fifo(client_and_session):
    """PUT on a sell deletes old lot_allocs and re-runs FIFO with the new quantity.
    Sell created via session (bare sells rejected at API level).
    """
    client, maker = client_and_session
    acct_id, inst_id = await _create_account_and_instrument(client)

    await client.post(
        "/api/transactions",
        json={
            "account_id": acct_id,
            "instrument_id": inst_id,
            "txn_type": "buy",
            "date": "2024-01-01",
            "quantity": "10",
            "unit_price": "1000",
            "price_currency": "USD",
            "fx_rate_to_eur": "1.1",
        },
    )

    # Create sell via session (POST /api/transactions rejects bare sell)
    from app.models.transaction import Transaction as Txn
    from app.services.fifo import match_lots_for_sell
    async with maker() as session:
        sell_txn = Txn(
            account_id=acct_id,
            instrument_id=inst_id,
            txn_type="sell",
            date=__import__("datetime").date(2024, 6, 1),
            quantity=Decimal("-4"),
            unit_price=Decimal("1500"),
            price_currency="USD",
            fx_rate_to_eur=Decimal("1.08"),
        )
        session.add(sell_txn)
        await session.flush()
        await match_lots_for_sell(session, sell_txn)
        await session.commit()
        sell_id = sell_txn.id

    # Increase sell quantity to 7 — FIFO must recompute and the alloc row becomes 7
    upd = await client.put(
        f"/api/transactions/{sell_id}",
        json={"quantity": "7"},
    )
    assert upd.status_code == 200, upd.text

    async with maker() as session:
        result = await session.execute(select(LotAlloc))
        allocs = result.scalars().all()
    assert len(allocs) == 1
    assert allocs[0].quantity == Decimal("7")


@pytest.mark.asyncio
async def test_monetary_fields_are_strings_not_floats(client_and_session):
    """Verify decimal precision: monetary values come back as strings."""
    client, _ = client_and_session
    acct_id, inst_id = await _create_account_and_instrument(client)
    resp = await client.post(
        "/api/transactions",
        json={
            "account_id": acct_id,
            "instrument_id": inst_id,
            "txn_type": "buy",
            "date": "2024-01-01",
            "quantity": "0.123456789012345678",
            "unit_price": "1234.56789012",
            "price_currency": "EUR",
            "fx_rate_to_eur": "1.0",
        },
    )
    data = resp.json()
    # quantity must come back as a string, not a float
    assert isinstance(data["quantity"], str), "quantity must be string in JSON"
    assert isinstance(data["unit_price"], str), "unit_price must be string in JSON"


@pytest.mark.asyncio
async def test_yield_transaction_accepted(client_and_session):
    """Yield transactions are now user-creatable.

    The old restriction was relaxed so the new YieldForm can
    POST txn_type='yield' directly. Manual yield rows carry source='manual' (default).
    The adjustment restriction remains in place. See test_adjustment_transaction_rejected.
    """
    client, _ = client_and_session
    acct_id, inst_id = await _create_account_and_instrument(client)
    resp = await client.post(
        "/api/transactions",
        json={
            "account_id": acct_id,
            "instrument_id": inst_id,
            "txn_type": "yield",
            "date": "2024-01-15",
            "quantity": "5",
        },
    )
    assert resp.status_code == 201, f"yield transactions must be accepted (201), got: {resp.text}"
    body = resp.json()
    assert body["txn_type"] == "yield"
    assert body["source"] == "manual"


@pytest.mark.asyncio
async def test_adjustment_transaction_rejected(client_and_session):
    """Adjustment txns are system-created; API rejects txn_type='adjustment'."""
    client, _ = client_and_session
    acct_id, inst_id = await _create_account_and_instrument(client)
    resp = await client.post(
        "/api/transactions",
        json={
            "account_id": acct_id,
            "instrument_id": inst_id,
            "txn_type": "adjustment",
            "date": "2024-01-15",
            "quantity": "1",
        },
    )
    assert resp.status_code == 422, "adjustment transactions must be rejected with 422"


@pytest.mark.asyncio
async def test_non_eur_currency_blocked(client_and_session):
    """Only EUR/USD txn currencies. GBP (etc.) must be rejected at schema layer.

    Note: USD without fx_rate_to_eur is now ACCEPTED — the POST handler
    auto-fetches from Frankfurter. See
    tests/test_txn_fx_locking.py for that path's coverage.
    """
    client, _ = client_and_session
    acct_id, inst_id = await _create_account_and_instrument(client)
    resp = await client.post(
        "/api/transactions",
        json={
            "account_id": acct_id,
            "instrument_id": inst_id,
            "txn_type": "buy",
            "date": "2024-01-15",
            "quantity": "10",
            "unit_price": "150.00",
            "price_currency": "GBP",
            "fx_rate_to_eur": "1.18",
        },
    )
    assert resp.status_code == 422, "Non-EUR/USD currency must be rejected"


def test_transaction_update_date_field_accepts_iso_string():
    """Regression: TransactionUpdate.date must accept ISO date strings.

    The field name `date` shadows the imported `date` class; with `= None`
    default that shadow makes Pydantic v2 re-evaluation resolve the
    annotation to `Optional[None]`, rejecting every PUT body that includes
    `date`. The fix is the `_date_t` aliased import in
    `app/schemas/transaction.py`.
    """
    from datetime import date as D

    from app.schemas.transaction import TransactionUpdate

    upd = TransactionUpdate(date="2024-06-15")
    assert upd.date == D(2024, 6, 15)
    assert TransactionUpdate(date=None).date is None
    assert TransactionUpdate(notes="foo").date is None
