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
from app.models.transaction import Transaction
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


def _patch_frankfurter(monkeypatch, handler) -> None:
    """Replace httpx.AsyncClient referenced from app.routers.transactions with a
    MockTransport-backed client that runs `handler`. Module-level rebind so the
    `async with httpx.AsyncClient()` block in the router uses our mock."""
    real = httpx.AsyncClient
    transport = httpx.MockTransport(handler)

    def factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real(*args, **kwargs)

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


def _frankfurter_500():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "upstream"})

    return handler


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

    _patch_frankfurter(monkeypatch, handler)

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

    _patch_frankfurter(monkeypatch, _frankfurter_ok("1.0512", "2025-01-15"))

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

    _patch_frankfurter(monkeypatch, _frankfurter_ok("1.0500", "2025-02-01"))

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

    _patch_frankfurter(monkeypatch, _frankfurter_500())

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

    _patch_frankfurter(monkeypatch, _frankfurter_500())

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

    _patch_frankfurter(monkeypatch, _frankfurter_ok("1.0000", "2025-03-01"))

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
    _patch_frankfurter(monkeypatch, _frankfurter_ok("1.0000", "2025-04-10"))
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
