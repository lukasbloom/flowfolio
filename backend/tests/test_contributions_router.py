"""Router-level tests for /api/contributions, including the repeatable
``instrument_id`` query parameter.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core import config as cfg_module
from app.core.database import Base, attach_sqlite_pragmas, get_db
from app.main import app
from app.models import Account, Instrument, PriceQuote, Transaction
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
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        login = await client.post("/api/auth/login", json={"password": "test-password-123"})
        assert login.status_code == 200
        yield client, maker

    app.dependency_overrides.clear()
    await engine.dispose()
    cfg_module.settings.app_password = original_password


async def _seed_two_instruments(maker) -> tuple[str, str]:
    """Two distinct holdings: A=100 EUR cost basis, B=250 EUR cost basis.

    Returns (instrument_a_id, instrument_b_id). Both bought yesterday so
    today's series has at least two replay points (yesterday + today).
    """
    yesterday = date.today() - timedelta(days=1)
    async with maker() as session:
        account = Account(name="Revolut", account_type="broker", currency="EUR")
        instrument_a = Instrument(
            symbol="AAA",
            name="Alpha Test",
            instrument_type="stock",
            base_currency="EUR",
            price_source="manual",
        )
        instrument_b = Instrument(
            symbol="BBB",
            name="Beta Test",
            instrument_type="stock",
            base_currency="EUR",
            price_source="manual",
        )
        session.add_all([account, instrument_a, instrument_b])
        await session.flush()

        session.add_all(
            [
                Transaction(
                    account_id=account.id,
                    instrument_id=instrument_a.id,
                    txn_type="buy",
                    date=yesterday,
                    quantity=Decimal("2"),
                    unit_price=Decimal("50"),
                    price_currency="EUR",
                    fx_rate_to_eur=Decimal("1"),
                    cost_basis_eur=Decimal("100"),
                ),
                Transaction(
                    account_id=account.id,
                    instrument_id=instrument_b.id,
                    txn_type="buy",
                    date=yesterday,
                    quantity=Decimal("5"),
                    unit_price=Decimal("50"),
                    price_currency="EUR",
                    fx_rate_to_eur=Decimal("1"),
                    cost_basis_eur=Decimal("250"),
                ),
                PriceQuote(
                    instrument_id=instrument_a.id,
                    date=yesterday,
                    price=Decimal("50"),
                    currency="EUR",
                    source="manual",
                ),
                PriceQuote(
                    instrument_id=instrument_b.id,
                    date=yesterday,
                    price=Decimal("50"),
                    currency="EUR",
                    source="manual",
                ),
                # Today's quotes so portfolio_value_series has a final point.
                PriceQuote(
                    instrument_id=instrument_a.id,
                    date=date.today(),
                    price=Decimal("50"),
                    currency="EUR",
                    source="manual",
                ),
                PriceQuote(
                    instrument_id=instrument_b.id,
                    date=date.today(),
                    price=Decimal("50"),
                    currency="EUR",
                    source="manual",
                ),
            ]
        )
        await session.commit()
        return instrument_a.id, instrument_b.id


def _last_cost_basis(payload) -> Decimal:
    return Decimal(payload["cost_basis_series"][-1]["value"])


def _last_value(payload) -> Decimal:
    return Decimal(payload["portfolio_value_series"][-1]["value"])


@pytest.mark.asyncio
async def test_contributions_single_instrument_id_narrows_both_series(authed_client):
    """Single ?instrument_id=<a> reflects ONLY that instrument's transactions."""
    client, maker = authed_client
    inst_a, _ = await _seed_two_instruments(maker)

    resp = await client.get(f"/api/contributions?period=month&currency=EUR&instrument_id={inst_a}")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    # A alone = 100 EUR cost basis & portfolio value at constant 50 EUR/share.
    assert _last_cost_basis(body) == Decimal("100")
    assert _last_value(body) == Decimal("100.00000000")


@pytest.mark.asyncio
async def test_contributions_multi_instrument_id_sums(authed_client):
    """Repeated instrument_id params sum to single-instrument totals (within rounding)."""
    client, maker = authed_client
    inst_a, inst_b = await _seed_two_instruments(maker)

    resp_a = await client.get(
        f"/api/contributions?period=month&currency=EUR&instrument_id={inst_a}"
    )
    resp_b = await client.get(
        f"/api/contributions?period=month&currency=EUR&instrument_id={inst_b}"
    )
    resp_both = await client.get(
        "/api/contributions?period=month&currency=EUR"
        f"&instrument_id={inst_a}&instrument_id={inst_b}"
    )

    assert resp_a.status_code == 200
    assert resp_b.status_code == 200
    assert resp_both.status_code == 200

    # Cost basis last value sums correctly.
    last_cb = _last_cost_basis(resp_both.json())
    expected_cb = _last_cost_basis(resp_a.json()) + _last_cost_basis(resp_b.json())
    assert abs(last_cb - expected_cb) < Decimal("0.01")

    # Portfolio value last value sums correctly.
    last_val = _last_value(resp_both.json())
    expected_val = _last_value(resp_a.json()) + _last_value(resp_b.json())
    assert abs(last_val - expected_val) < Decimal("0.01")


@pytest.mark.asyncio
async def test_contributions_empty_instrument_id_list_equals_full_portfolio(authed_client):
    """No instrument_id param returns the same response as today's portfolio-wide call."""
    client, maker = authed_client
    await _seed_two_instruments(maker)

    resp = await client.get("/api/contributions?period=month&currency=EUR")

    assert resp.status_code == 200
    body = resp.json()
    # Full portfolio = A (100) + B (250) = 350 EUR at constant prices.
    assert _last_cost_basis(body) == Decimal("350")
    assert _last_value(body) == Decimal("350.00000000")


@pytest.mark.asyncio
async def test_contributions_unknown_instrument_id_returns_empty_series_not_500(authed_client):
    """An unknown UUID returns 200 with empty series, not 500."""
    client, maker = authed_client
    await _seed_two_instruments(maker)

    unknown_id = "00000000-0000-0000-0000-000000000000"
    resp = await client.get(
        f"/api/contributions?period=month&currency=EUR&instrument_id={unknown_id}"
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    # No transactions for the unknown instrument → both series empty.
    assert body["cost_basis_series"] == []
    assert body["portfolio_value_series"] == []
    assert body["buckets"] == []
