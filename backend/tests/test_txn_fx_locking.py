"""POST /api/transactions FX auto-lock tests.

Verifies:
- EUR txns lock fx_rate_to_eur=1 without calling Frankfurter
- USD txns with no explicit rate auto-fetch from Frankfurter and lock the rate
- USD txns with explicit rate use that rate; cache warming is best-effort
- Frankfurter outage on no-explicit-rate path → 502; explicit-rate path is unaffected
- PUT recomputes cost_basis_eur via existing _compute_cost_basis
- Locked rate is immutable: subsequent fx_rate edits don't touch existing txn rows
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
from app.models.fx_rate import FxRate
from app.models.lot_alloc import LotAlloc
from app.models.transaction import Transaction
from tests._fx_mock import (
    frankfurter_500,
    frankfurter_must_not_be_called,
    frankfurter_ok,
    patch_frankfurter,
)
from tests.conftest import seed_admin_password


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
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        login = await c.post("/api/auth/login", json={"password": "test-password-123"})
        assert login.status_code == 200
        yield c, maker

    app.dependency_overrides.clear()
    await engine.dispose()
    cfg_module.settings.app_password = original_password






async def _create_account_instrument(client, base_currency="USD"):
    acct = (
        await client.post(
            "/api/accounts", json={"name": "TestBroker", "account_type": "broker"}
        )
    ).json()
    inst = (
        await client.post(
            "/api/instruments",
            json={
                "symbol": "AAPL",
                "name": "Apple",
                "instrument_type": "stock",
                "base_currency": base_currency,
                "price_source": "finnhub",
            },
        )
    ).json()
    return acct["id"], inst["id"]


# ---------------------------------------------------------------------------
# 1. EUR identity rate; no Frankfurter call
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_txn_eur_locks_rate_to_one(authed_client, monkeypatch):
    client, maker = authed_client
    acct_id, inst_id = await _create_account_instrument(client, base_currency="EUR")

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("must NOT call Frankfurter for EUR")

    patch_frankfurter(monkeypatch, handler)

    resp = await client.post(
        "/api/transactions",
        json={
            "account_id": acct_id,
            "instrument_id": inst_id,
            "txn_type": "buy",
            "date": "2025-01-15",
            "quantity": "10",
            "unit_price": "100.00",
            "price_currency": "EUR",
            # fx_rate_to_eur omitted
        },
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert Decimal(data["fx_rate_to_eur"]) == Decimal("1")


# ---------------------------------------------------------------------------
# 2. USD without explicit rate → Frankfurter auto-fetch + lock
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_txn_usd_no_explicit_rate_fetches(authed_client, monkeypatch):
    client, maker = authed_client
    acct_id, inst_id = await _create_account_instrument(client)

    patch_frankfurter(monkeypatch, frankfurter_ok("1.0512", "2025-01-15"))

    resp = await client.post(
        "/api/transactions",
        json={
            "account_id": acct_id,
            "instrument_id": inst_id,
            "txn_type": "buy",
            "date": "2025-01-15",
            "quantity": "10",
            "unit_price": "150.00",
            "price_currency": "USD",
        },
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert Decimal(data["fx_rate_to_eur"]) == Decimal("1.0512")
    # cost_basis_eur = qty * price / fx = 10 * 150 / 1.0512
    expected = (Decimal("10") * Decimal("150") / Decimal("1.0512")).quantize(
        Decimal("0.00000001")
    )
    assert Decimal(data["cost_basis_eur"]) == expected

    # fx_rate cache row written
    async with maker() as s:
        result = await s.execute(
            select(FxRate).where(FxRate.date == date(2025, 1, 15))
        )
        row = result.scalar_one()
    assert row.rate == Decimal("1.0512")
    assert row.source == "frankfurter"


# ---------------------------------------------------------------------------
# 3. USD with explicit rate → use explicit; warm cache from Frankfurter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_txn_usd_explicit_rate_overrides(authed_client, monkeypatch):
    client, maker = authed_client
    acct_id, inst_id = await _create_account_instrument(client)

    patch_frankfurter(monkeypatch, frankfurter_ok("1.0500", "2025-02-01"))

    # User-supplied broker-markup rate differs from ECB
    resp = await client.post(
        "/api/transactions",
        json={
            "account_id": acct_id,
            "instrument_id": inst_id,
            "txn_type": "buy",
            "date": "2025-02-01",
            "quantity": "5",
            "unit_price": "200.00",
            "price_currency": "USD",
            "fx_rate_to_eur": "1.07",  # broker markup vs ECB 1.05
        },
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    # Txn-locked rate is the broker-markup rate, NOT 1.05 from Frankfurter
    assert Decimal(data["fx_rate_to_eur"]) == Decimal("1.07")

    # fx_rate cache nonetheless contains the frankfurter row for history
    async with maker() as s:
        result = await s.execute(
            select(FxRate).where(FxRate.date == date(2025, 2, 1))
        )
        row = result.scalar_one()
    assert row.source == "frankfurter"
    assert row.rate == Decimal("1.0500")


# ---------------------------------------------------------------------------
# 4. Explicit-rate path: Frankfurter outage doesn't fail the txn
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_txn_usd_explicit_rate_caches_best_effort(authed_client, monkeypatch):
    client, maker = authed_client
    acct_id, inst_id = await _create_account_instrument(client)

    patch_frankfurter(monkeypatch, frankfurter_500())

    resp = await client.post(
        "/api/transactions",
        json={
            "account_id": acct_id,
            "instrument_id": inst_id,
            "txn_type": "buy",
            "date": "2025-02-15",
            "quantity": "5",
            "unit_price": "200.00",
            "price_currency": "USD",
            "fx_rate_to_eur": "1.08",
        },
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert Decimal(data["fx_rate_to_eur"]) == Decimal("1.08")

    # No fx_rate cache row created (best-effort warming was suppressed)
    async with maker() as s:
        result = await s.execute(
            select(FxRate).where(FxRate.date == date(2025, 2, 15))
        )
        assert result.scalar_one_or_none() is None


# ---------------------------------------------------------------------------
# 5. No-explicit-rate path: Frankfurter outage → 502
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_txn_usd_no_explicit_rate_frankfurter_down_returns_502(
    authed_client, monkeypatch
):
    client, _ = authed_client
    acct_id, inst_id = await _create_account_instrument(client)

    patch_frankfurter(monkeypatch, frankfurter_500())

    resp = await client.post(
        "/api/transactions",
        json={
            "account_id": acct_id,
            "instrument_id": inst_id,
            "txn_type": "buy",
            "date": "2025-02-20",
            "quantity": "5",
            "unit_price": "200.00",
            "price_currency": "USD",
        },
    )
    assert resp.status_code == 502, resp.text
    detail = resp.json()["detail"]
    assert "fx upstream error" in detail
    assert "fx_rate_to_eur" in detail  # hint about supplying explicitly


# ---------------------------------------------------------------------------
# 6. PUT FX edit → cost_basis_eur recomputed via _compute_cost_basis
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_txn_fx_edit_recomputes_cost_basis(authed_client, monkeypatch):
    client, _ = authed_client
    acct_id, inst_id = await _create_account_instrument(client)

    patch_frankfurter(monkeypatch, frankfurter_ok("1.0000", "2025-03-01"))

    create_resp = await client.post(
        "/api/transactions",
        json={
            "account_id": acct_id,
            "instrument_id": inst_id,
            "txn_type": "buy",
            "date": "2025-03-01",
            "quantity": "10",
            "unit_price": "100.00",
            "price_currency": "USD",
            "fx_rate_to_eur": "1.0",
        },
    )
    assert create_resp.status_code == 201
    txn_id = create_resp.json()["id"]
    # cost_basis at rate 1.0: 10 * 100 / 1.0 = 1000
    assert Decimal(create_resp.json()["cost_basis_eur"]) == Decimal("1000.00000000")

    # Edit FX rate to 1.1; new cost basis = 10 * 100 / 1.1
    upd = await client.put(
        f"/api/transactions/{txn_id}", json={"fx_rate_to_eur": "1.1"}
    )
    assert upd.status_code == 200, upd.text
    expected = (Decimal("10") * Decimal("100") / Decimal("1.1")).quantize(
        Decimal("0.00000001")
    )
    assert Decimal(upd.json()["cost_basis_eur"]) == expected
    assert Decimal(upd.json()["fx_rate_to_eur"]) == Decimal("1.1")


# ---------------------------------------------------------------------------
# 7. Per-txn fx_rate_to_eur is immutable on insert
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_txn_locks_immutability(authed_client, monkeypatch):
    """Manual override of fx_rate row for the same date does NOT touch existing txns."""
    client, maker = authed_client
    acct_id, inst_id = await _create_account_instrument(client)

    # First txn locks at rate 1.00
    patch_frankfurter(monkeypatch, frankfurter_ok("1.0000", "2025-04-10"))
    create_resp = await client.post(
        "/api/transactions",
        json={
            "account_id": acct_id,
            "instrument_id": inst_id,
            "txn_type": "buy",
            "date": "2025-04-10",
            "quantity": "1",
            "unit_price": "100.00",
            "price_currency": "USD",
        },
    )
    assert create_resp.status_code == 201
    txn_id = create_resp.json()["id"]

    # Now overwrite the fx_rate row for the same date via manual override
    override = await client.post(
        "/api/fx/manual",
        json={
            "date": "2025-04-10",
            "base_currency": "EUR",
            "quote_currency": "USD",
            "rate": "1.2000",
            "source": "manual",
        },
    )
    assert override.status_code == 201

    # Original txn must still hold the locked-at-insert 1.0000 rate
    async with maker() as s:
        result = await s.execute(
            select(Transaction).where(Transaction.id == txn_id)
        )
        txn = result.scalar_one()
    assert txn.fx_rate_to_eur == Decimal("1.0000000000")


# ---------------------------------------------------------------------------
# PUT /api/transactions FX re-lock on currency change (moved from
# test_api_transactions.py, this module is the FX-locking home)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_eur_to_usd_flip_without_rate_fetches(authed_client, monkeypatch):
    client, _ = authed_client
    acct_id, inst_id = await _create_account_instrument(client)

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

    patch_frankfurter(monkeypatch, frankfurter_ok("1.2000", "2025-05-01"))

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
async def test_put_usd_to_eur_flip_locks_identity_rate(authed_client, monkeypatch):
    client, _ = authed_client
    acct_id, inst_id = await _create_account_instrument(client)

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
    patch_frankfurter(monkeypatch, frankfurter_must_not_be_called())

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
    authed_client, monkeypatch
):
    client, _ = authed_client
    acct_id, inst_id = await _create_account_instrument(client)

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
    patch_frankfurter(monkeypatch, frankfurter_must_not_be_called())

    upd = await client.put(
        f"/api/transactions/{txn_id}",
        json={"price_currency": "USD", "fx_rate_to_eur": "1.2500"},
    )
    assert upd.status_code == 200, upd.text
    assert Decimal(upd.json()["fx_rate_to_eur"]) == Decimal("1.2500")


@pytest.mark.asyncio
async def test_put_invalid_currency_rejected(authed_client):
    client, _ = authed_client
    acct_id, inst_id = await _create_account_instrument(client)

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
async def test_put_date_only_edit_keeps_locked_rate(authed_client, monkeypatch):
    client, _ = authed_client
    acct_id, inst_id = await _create_account_instrument(client)

    patch_frankfurter(monkeypatch, frankfurter_ok("1.1500", "2025-05-05"))
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
    patch_frankfurter(monkeypatch, frankfurter_must_not_be_called())
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
    authed_client, monkeypatch
):
    client, maker = authed_client
    acct_id, inst_id = await _create_account_instrument(client)

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
    patch_frankfurter(monkeypatch, frankfurter_ok("1.5000", "2025-06-02"))
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
    authed_client, monkeypatch
):
    client, maker = authed_client
    acct_id, inst_id = await _create_account_instrument(client)

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
    patch_frankfurter(monkeypatch, frankfurter_ok("2.0000", "2025-06-10"))
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
async def test_put_same_currency_resend_is_noop(authed_client, monkeypatch):
    client, maker = authed_client
    acct_id, inst_id = await _create_account_instrument(client)

    patch_frankfurter(monkeypatch, frankfurter_ok("1.2000", "2025-06-20"))
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
    patch_frankfurter(monkeypatch, frankfurter_must_not_be_called())
    upd = await client.put(f"/api/transactions/{buy_id}", json={"price_currency": "USD"})
    assert upd.status_code == 200, upd.text
    assert Decimal(upd.json()["fx_rate_to_eur"]) == Decimal("1.2000")

    async with maker() as session:
        result = await session.execute(select(LotAlloc))
        allocs_after = result.scalars().all()
    assert len(allocs_after) == 1
    assert allocs_after[0].id == alloc_id_before, "no-op currency re-send churned lot allocs"
    assert allocs_after[0].realized_gain_eur == gain_before
