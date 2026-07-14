"""Tests for /api/transactions CRUD with FIFO cascade.

Critical assertions:
- POST /api/transactions with txn_type=sell creates lot_alloc rows atomically
- DELETE on a sell cascades the deletion of its lot_alloc rows (via ON DELETE CASCADE)
- Monetary fields are returned as strings (not floats) preserving NUMERIC precision
- yield txn type is accepted (manual yield POST returns 201)
- adjustment txn type is rejected (system-created only)
- Non-EUR transactions require fx_rate_to_eur
- PUT re-locks fx_rate_to_eur when the edit changes price_currency (see
  tests/test_txn_fx_locking.py for the analogous POST-path coverage)
"""
from __future__ import annotations

from datetime import date
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


# Captured once at import time, before any test monkeypatches
# app.routers.transactions.httpx.AsyncClient (which is the same module-level
# httpx.AsyncClient this file imports). Patching twice in one test must
# REPLACE the mock transport, not wrap the previous factory around it, so
# the factory below always builds on this original class rather than on
# whatever the last patch left behind.
_REAL_ASYNC_CLIENT = httpx.AsyncClient


def _patch_frankfurter(monkeypatch, handler) -> None:
    """Replace httpx.AsyncClient referenced from app.routers.transactions with a
    MockTransport-backed client that runs `handler`. Mirrors
    tests/test_txn_fx_locking.py's helper of the same name."""
    transport = httpx.MockTransport(handler)

    def factory(*args, **kwargs):
        kwargs["transport"] = transport
        return _REAL_ASYNC_CLIENT(*args, **kwargs)

    monkeypatch.setattr("app.routers.transactions.httpx.AsyncClient", factory)


def _frankfurter_ok(rate: str, date_str: str):
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


def _frankfurter_must_not_be_called():
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("must NOT call Frankfurter")

    return handler


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


# ---------------------------------------------------------------------------
# PUT re-locks fx_rate_to_eur on a price_currency edit (bug: was silently
# left at the old rate, e.g. a $100 price landing as a €100 cost basis).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_eur_to_usd_flip_without_rate_fetches(client_and_session, monkeypatch):
    client, _ = client_and_session
    acct_id, inst_id = await _create_account_and_instrument(client)

    create_resp = await client.post(
        "/api/transactions",
        json={
            "account_id": acct_id,
            "instrument_id": inst_id,
            "txn_type": "buy",
            "date": "2025-05-01",
            "quantity": "10",
            "unit_price": "100.00",
            "price_currency": "EUR",
        },
    )
    assert create_resp.status_code == 201
    assert Decimal(create_resp.json()["fx_rate_to_eur"]) == Decimal("1")
    txn_id = create_resp.json()["id"]

    _patch_frankfurter(monkeypatch, _frankfurter_ok("1.2000", "2025-05-01"))

    upd = await client.put(
        f"/api/transactions/{txn_id}", json={"price_currency": "USD"}
    )
    assert upd.status_code == 200, upd.text
    data = upd.json()
    assert Decimal(data["fx_rate_to_eur"]) == Decimal("1.2000")
    expected = (Decimal("10") * Decimal("100") / Decimal("1.2000")).quantize(
        Decimal("0.00000001")
    )
    assert Decimal(data["cost_basis_eur"]) == expected


@pytest.mark.asyncio
async def test_put_usd_to_eur_flip_locks_identity_rate(client_and_session, monkeypatch):
    client, _ = client_and_session
    acct_id, inst_id = await _create_account_and_instrument(client)

    create_resp = await client.post(
        "/api/transactions",
        json={
            "account_id": acct_id,
            "instrument_id": inst_id,
            "txn_type": "buy",
            "date": "2025-05-02",
            "quantity": "10",
            "unit_price": "100.00",
            "price_currency": "USD",
            "fx_rate_to_eur": "1.10",
        },
    )
    assert create_resp.status_code == 201
    txn_id = create_resp.json()["id"]

    # Flipping to EUR must never call Frankfurter, identity rate is local.
    _patch_frankfurter(monkeypatch, _frankfurter_must_not_be_called())

    upd = await client.put(
        f"/api/transactions/{txn_id}", json={"price_currency": "EUR"}
    )
    assert upd.status_code == 200, upd.text
    data = upd.json()
    assert Decimal(data["fx_rate_to_eur"]) == Decimal("1")
    expected = (Decimal("10") * Decimal("100") / Decimal("1")).quantize(
        Decimal("0.00000001")
    )
    assert Decimal(data["cost_basis_eur"]) == expected


@pytest.mark.asyncio
async def test_put_currency_flip_with_explicit_rate_is_honored(
    client_and_session, monkeypatch
):
    client, _ = client_and_session
    acct_id, inst_id = await _create_account_and_instrument(client)

    create_resp = await client.post(
        "/api/transactions",
        json={
            "account_id": acct_id,
            "instrument_id": inst_id,
            "txn_type": "buy",
            "date": "2025-05-03",
            "quantity": "10",
            "unit_price": "100.00",
            "price_currency": "EUR",
        },
    )
    assert create_resp.status_code == 201
    txn_id = create_resp.json()["id"]

    # An explicit rate in the same PUT must be honored verbatim, no fetch.
    _patch_frankfurter(monkeypatch, _frankfurter_must_not_be_called())

    upd = await client.put(
        f"/api/transactions/{txn_id}",
        json={"price_currency": "USD", "fx_rate_to_eur": "1.2500"},
    )
    assert upd.status_code == 200, upd.text
    assert Decimal(upd.json()["fx_rate_to_eur"]) == Decimal("1.2500")


@pytest.mark.asyncio
async def test_put_invalid_currency_rejected(client_and_session):
    client, _ = client_and_session
    acct_id, inst_id = await _create_account_and_instrument(client)

    create_resp = await client.post(
        "/api/transactions",
        json={
            "account_id": acct_id,
            "instrument_id": inst_id,
            "txn_type": "buy",
            "date": "2025-05-04",
            "quantity": "10",
            "unit_price": "100.00",
            "price_currency": "EUR",
        },
    )
    assert create_resp.status_code == 201
    txn_id = create_resp.json()["id"]

    upd = await client.put(
        f"/api/transactions/{txn_id}", json={"price_currency": "GBP"}
    )
    assert upd.status_code == 422, upd.text


@pytest.mark.asyncio
async def test_put_date_only_edit_keeps_locked_rate(client_and_session, monkeypatch):
    client, _ = client_and_session
    acct_id, inst_id = await _create_account_and_instrument(client)

    _patch_frankfurter(monkeypatch, _frankfurter_ok("1.1500", "2025-05-05"))
    create_resp = await client.post(
        "/api/transactions",
        json={
            "account_id": acct_id,
            "instrument_id": inst_id,
            "txn_type": "buy",
            "date": "2025-05-05",
            "quantity": "10",
            "unit_price": "100.00",
            "price_currency": "USD",
        },
    )
    assert create_resp.status_code == 201
    txn_id = create_resp.json()["id"]
    assert Decimal(create_resp.json()["fx_rate_to_eur"]) == Decimal("1.1500")

    # A date-only edit must not touch price_currency and must not re-fetch.
    _patch_frankfurter(monkeypatch, _frankfurter_must_not_be_called())
    upd = await client.put(
        f"/api/transactions/{txn_id}", json={"date": "2025-06-01"}
    )
    assert upd.status_code == 200, upd.text
    assert Decimal(upd.json()["fx_rate_to_eur"]) == Decimal("1.1500")


# ---------------------------------------------------------------------------
# A currency flip must recompute FIFO, not just re-lock the rate in isolation.
# price_currency is not itself a FIFO-relevant field pre-fix, so a currency
# edit could silently mutate fx_rate_to_eur (hence cost basis and realized
# gains) on a txn with existing lot_alloc rows without ever re-running FIFO,
# leaving LotAlloc.realized_gain_eur computed from the stale rate.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_currency_flip_on_sell_refreshes_stale_realized_gain(
    client_and_session, monkeypatch
):
    client, maker = client_and_session
    acct_id, inst_id = await _create_account_and_instrument(client)

    # Buy 10 @ 100 EUR (fx=1) -> buy_price_eur = 100
    buy_resp = await client.post(
        "/api/transactions",
        json={
            "account_id": acct_id,
            "instrument_id": inst_id,
            "txn_type": "buy",
            "date": "2025-06-01",
            "quantity": "10",
            "unit_price": "100",
            "price_currency": "EUR",
        },
    )
    assert buy_resp.status_code == 201

    # Sell 10 @ 150 EUR (fx=1) via session (bare sells rejected at API level)
    # -> realized_gain_eur = (150 - 100) * 10 = 500
    from app.models.transaction import Transaction as Txn
    from app.services.fifo import match_lots_for_sell

    async with maker() as session:
        sell_txn = Txn(
            account_id=acct_id,
            instrument_id=inst_id,
            txn_type="sell",
            date=date(2025, 6, 2),
            quantity=Decimal("-10"),
            unit_price=Decimal("150"),
            price_currency="EUR",
            fx_rate_to_eur=Decimal("1"),
        )
        session.add(sell_txn)
        await session.flush()
        await match_lots_for_sell(session, sell_txn)
        await session.commit()
        sell_id = sell_txn.id

    async with maker() as session:
        result = await session.execute(select(LotAlloc))
        alloc = result.scalars().one()
    assert alloc.realized_gain_eur == Decimal("500.00000000")

    # Flip the sell to USD without an explicit rate -> fetch rate 1.5.
    # sell_price_eur becomes 150 / 1.5 = 100, so the new gain is 0.
    _patch_frankfurter(monkeypatch, _frankfurter_ok("1.5000", "2025-06-02"))
    upd = await client.put(
        f"/api/transactions/{sell_id}", json={"price_currency": "USD"}
    )
    assert upd.status_code == 200, upd.text
    assert Decimal(upd.json()["fx_rate_to_eur"]) == Decimal("1.5000")

    async with maker() as session:
        result = await session.execute(select(LotAlloc))
        alloc = result.scalars().one()
    assert alloc.realized_gain_eur == Decimal(
        "0.00000000"
    ), "stale realized_gain_eur must be recomputed after the currency flip"


@pytest.mark.asyncio
async def test_put_currency_flip_on_buy_refreshes_stale_realized_gain(
    client_and_session, monkeypatch
):
    client, maker = client_and_session
    acct_id, inst_id = await _create_account_and_instrument(client)

    # Buy 10 @ 100 EUR (fx=1) -> buy_price_eur = 100
    buy_resp = await client.post(
        "/api/transactions",
        json={
            "account_id": acct_id,
            "instrument_id": inst_id,
            "txn_type": "buy",
            "date": "2025-06-10",
            "quantity": "10",
            "unit_price": "100",
            "price_currency": "EUR",
        },
    )
    assert buy_resp.status_code == 201
    buy_id = buy_resp.json()["id"]

    # Sell 10 @ 150 EUR (fx=1) via session -> realized_gain_eur = 500
    from app.models.transaction import Transaction as Txn
    from app.services.fifo import match_lots_for_sell

    async with maker() as session:
        sell_txn = Txn(
            account_id=acct_id,
            instrument_id=inst_id,
            txn_type="sell",
            date=date(2025, 6, 11),
            quantity=Decimal("-10"),
            unit_price=Decimal("150"),
            price_currency="EUR",
            fx_rate_to_eur=Decimal("1"),
        )
        session.add(sell_txn)
        await session.flush()
        await match_lots_for_sell(session, sell_txn)
        await session.commit()

    async with maker() as session:
        result = await session.execute(select(LotAlloc))
        alloc = result.scalars().one()
    assert alloc.realized_gain_eur == Decimal("500.00000000")

    # Flip the BUY (the consumed lot) to USD without an explicit rate ->
    # fetch rate 2.0. buy_price_eur becomes 100 / 2.0 = 50, sell is untouched
    # (still EUR @ 150), so the new gain is (150 - 50) * 10 = 1000.
    _patch_frankfurter(monkeypatch, _frankfurter_ok("2.0000", "2025-06-10"))
    upd = await client.put(
        f"/api/transactions/{buy_id}", json={"price_currency": "USD"}
    )
    assert upd.status_code == 200, upd.text
    assert Decimal(upd.json()["fx_rate_to_eur"]) == Decimal("2.0000")

    async with maker() as session:
        result = await session.execute(select(LotAlloc))
        alloc = result.scalars().one()
    assert alloc.realized_gain_eur == Decimal(
        "1000.00000000"
    ), "stale realized_gain_eur must be recomputed after the buy-side currency flip"


# ---------------------------------------------------------------------------
# A PUT that re-sends the CURRENT price_currency unchanged must be a true
# no-op: presence of the key alone used to be enough to trigger both the
# re-lock (overwriting a deliberately locked broker rate) and the FIFO
# recompute gate (churning lot allocs for nothing).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_same_currency_resend_is_noop(client_and_session, monkeypatch):
    client, maker = client_and_session
    acct_id, inst_id = await _create_account_and_instrument(client)

    _patch_frankfurter(monkeypatch, _frankfurter_ok("1.2000", "2025-06-20"))
    buy_resp = await client.post(
        "/api/transactions",
        json={
            "account_id": acct_id,
            "instrument_id": inst_id,
            "txn_type": "buy",
            "date": "2025-06-20",
            "quantity": "10",
            "unit_price": "100",
            "price_currency": "USD",
        },
    )
    assert buy_resp.status_code == 201
    buy_id = buy_resp.json()["id"]
    assert Decimal(buy_resp.json()["fx_rate_to_eur"]) == Decimal("1.2000")

    # Sell 10 via session (bare sells rejected at API level) to create a
    # LotAlloc row we can check for churn.
    from app.models.transaction import Transaction as Txn
    from app.services.fifo import match_lots_for_sell

    async with maker() as session:
        sell_txn = Txn(
            account_id=acct_id,
            instrument_id=inst_id,
            txn_type="sell",
            date=date(2025, 6, 21),
            quantity=Decimal("-10"),
            unit_price=Decimal("150"),
            price_currency="USD",
            fx_rate_to_eur=Decimal("1.2000"),
        )
        session.add(sell_txn)
        await session.flush()
        await match_lots_for_sell(session, sell_txn)
        await session.commit()

    async with maker() as session:
        result = await session.execute(select(LotAlloc))
        allocs_before = result.scalars().all()
    assert len(allocs_before) == 1
    alloc_id_before = allocs_before[0].id
    gain_before = allocs_before[0].realized_gain_eur

    # Re-send the SAME currency, unchanged, without fx_rate_to_eur. Must not
    # call Frankfurter and must not touch the locked rate or churn the alloc.
    _patch_frankfurter(monkeypatch, _frankfurter_must_not_be_called())
    upd = await client.put(f"/api/transactions/{buy_id}", json={"price_currency": "USD"})
    assert upd.status_code == 200, upd.text
    assert Decimal(upd.json()["fx_rate_to_eur"]) == Decimal("1.2000")

    async with maker() as session:
        result = await session.execute(select(LotAlloc))
        allocs_after = result.scalars().all()
    assert len(allocs_after) == 1
    assert allocs_after[0].id == alloc_id_before, "no-op currency re-send churned lot allocs"
    assert allocs_after[0].realized_gain_eur == gain_before


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
