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
from app.models import Account, FxRate, Instrument, PriceQuote, Transaction
from app.services.perf import _first_buy_from_preload
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


async def _seed_open_holding(maker) -> None:
    async with maker() as session:
        account = Account(name="Revolut", account_type="broker", currency="EUR")
        instrument = Instrument(
            symbol="FLOW",
            name="Flow Test",
            instrument_type="stock",
            base_currency="EUR",
            price_source="manual",
        )
        session.add_all([account, instrument])
        await session.flush()
        session.add(
            Transaction(
                account_id=account.id,
                instrument_id=instrument.id,
                txn_type="buy",
                date=date.today() - timedelta(days=10),
                quantity=Decimal("2"),
                unit_price=Decimal("50"),
                price_currency="EUR",
                fx_rate_to_eur=Decimal("1"),
                cost_basis_eur=Decimal("100"),
            )
        )
        for days_ago in range(6, -1, -1):
            session.add(
                PriceQuote(
                    instrument_id=instrument.id,
                    date=date.today() - timedelta(days=days_ago),
                    price=Decimal("60"),
                    currency="EUR",
                    source="manual",
                )
            )
        await session.commit()


@pytest.mark.asyncio
async def test_get_perf_authenticated_returns_decimal_strings(authed_client):
    client, maker = authed_client
    await _seed_open_holding(maker)

    resp = await client.get("/api/perf?timeframe=1y&currency=EUR")

    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert len(rows) == 1
    row = rows[0]
    assert "percent_return" in row
    assert "twrr" in row
    assert isinstance(row["quantity"], str)
    assert isinstance(row["percent_return"], str)


@pytest.mark.asyncio
async def test_get_perf_invalid_timeframe_422(authed_client):
    client, _ = authed_client

    # "custom" became a valid timeframe; "bogus" still 422s the regex.
    resp = await client.get("/api/perf?timeframe=bogus")

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_get_perf_custom_range_happy_path(authed_client):
    """?timeframe=custom&from=...&to=... returns rows."""
    client, maker = authed_client
    await _seed_open_holding(maker)

    today = date.today()
    # _seed_open_holding seeds 7 quote days (range(6, -1, -1)); use the full
    # window so we clear the INSUFFICIENT_HISTORY_DAYS=7 threshold.
    frm = (today - timedelta(days=6)).isoformat()
    to_ = today.isoformat()
    resp = await client.get(
        f"/api/perf?timeframe=custom&from={frm}&to={to_}&currency=EUR"
    )

    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert len(rows) == 1
    row = rows[0]
    # Wire contract: numeric fields are Decimal-as-string.
    assert isinstance(row["quantity"], str)
    # Flat 60 EUR quote series → TWRR is 0 over the window. Pydantic emits
    # the quantised "0E-16" representation; accept either notation.
    assert row["twrr"] in {"0E-16", "0.0000000000000000"}, row


@pytest.mark.asyncio
async def test_get_perf_custom_range_missing_dates_422(authed_client):
    """timeframe=custom without dates → 422."""
    client, _ = authed_client

    resp = await client.get("/api/perf?timeframe=custom")

    assert resp.status_code == 422
    assert "from" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_get_perf_custom_range_reversed_422(authed_client):
    """from > to → 422."""
    client, _ = authed_client

    resp = await client.get(
        "/api/perf?timeframe=custom&from=2026-02-14&to=2026-01-15"
    )

    assert resp.status_code == 422
    assert "<=" in resp.json()["detail"]


async def _seed_usd_holding(maker) -> None:
    async with maker() as session:
        account = Account(name="Revolut", account_type="broker", currency="EUR")
        instrument = Instrument(
            symbol="AAPL",
            name="Apple",
            instrument_type="stock",
            base_currency="USD",
            price_source="manual",
        )
        session.add_all([account, instrument])
        await session.flush()
        session.add(
            Transaction(
                account_id=account.id,
                instrument_id=instrument.id,
                txn_type="buy",
                date=date.today() - timedelta(days=10),
                quantity=Decimal("1"),
                unit_price=Decimal("100"),
                price_currency="USD",
                fx_rate_to_eur=Decimal("1.25"),
                cost_basis_eur=Decimal("80"),
            )
        )
        for days_ago in range(6, -1, -1):
            session.add(
                PriceQuote(
                    instrument_id=instrument.id,
                    date=date.today() - timedelta(days=days_ago),
                    price=Decimal("110"),
                    currency="USD",
                    source="manual",
                )
            )
        session.add(
            FxRate(
                date=date.today(),
                base_currency="EUR",
                quote_currency="USD",
                rate=Decimal("1.50"),
                source="manual",
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_get_perf_currency_usd_differs_from_eur(authed_client):
    """/api/perf?currency=USD must return different numeric values
    for avg_cost and current_price than ?currency=EUR for a USD-priced
    holding with a non-1.0 EUR/USD rate."""
    client, maker = authed_client
    await _seed_usd_holding(maker)

    eur_resp = await client.get("/api/perf?timeframe=1y&currency=EUR")
    usd_resp = await client.get("/api/perf?timeframe=1y&currency=USD")
    assert eur_resp.status_code == 200, eur_resp.text
    assert usd_resp.status_code == 200, usd_resp.text

    eur_row = eur_resp.json()[0]
    usd_row = usd_resp.json()[0]

    # Both must be Decimal-as-string per the wire contract.
    assert isinstance(eur_row["avg_cost"], str)
    assert isinstance(usd_row["avg_cost"], str)
    assert isinstance(eur_row["current_price"], str)
    assert isinstance(usd_row["current_price"], str)

    # The fix is the difference: same holding, two currencies, two numbers.
    assert eur_row["avg_cost"] != usd_row["avg_cost"]
    assert eur_row["current_price"] != usd_row["current_price"]


def _txn(
    txn_type: str, trade_date: date, quantity: Decimal
) -> Transaction:
    return Transaction(
        account_id="acct-1",
        instrument_id="inst-1",
        txn_type=txn_type,
        date=trade_date,
        quantity=quantity,
        unit_price=Decimal("10"),
        price_currency="EUR",
        fx_rate_to_eur=Decimal("1"),
        cost_basis_eur=Decimal("100"),
    )


def test_first_buy_from_preload_picks_earliest_positive_buy():
    """Earliest positive buy/adjustment date wins; a later sell and a
    zero-quantity adjustment are ignored, mirroring quotes.first_buy_date."""
    buy = _txn("buy", date(2024, 1, 10), Decimal("5"))
    sell = _txn("sell", date(2024, 2, 1), Decimal("-5"))
    zero_adjustment = _txn("adjustment", date(2024, 1, 1), Decimal("0"))

    result = _first_buy_from_preload([sell, zero_adjustment, buy])

    assert result == date(2024, 1, 10)


def test_first_buy_from_preload_empty_list_returns_none():
    assert _first_buy_from_preload([]) is None
