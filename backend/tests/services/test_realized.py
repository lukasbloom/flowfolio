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
from app.models import Account, HoldingTag, Instrument, LotAlloc, PriceQuote, Tag, Transaction
from app.services.perf import get_performance_rows
from app.services.realized import get_realized_per_holding, get_realized_totals
from tests.conftest import seed_admin_password


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    attach_sqlite_pragmas(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


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


async def _holding(
    session: AsyncSession, *, symbol: str = "FLOW", price: str = "100"
) -> tuple[Account, Instrument]:
    account = Account(name=f"{symbol} Account", account_type="broker", currency="EUR")
    instrument = Instrument(
        symbol=symbol,
        name=f"{symbol} Holding",
        instrument_type="stock",
        base_currency="EUR",
        price_source="manual",
    )
    session.add_all([account, instrument])
    await session.flush()
    session.add(
        PriceQuote(
            instrument_id=instrument.id,
            date=date.today(),
            price=Decimal(price),
            currency="EUR",
            source="manual",
        )
    )
    await session.flush()
    return account, instrument


async def _buy(
    session: AsyncSession,
    account: Account,
    instrument: Instrument,
    *,
    qty: str = "1",
    price: str = "100",
    trade_date: date = date(2025, 1, 1),
) -> Transaction:
    txn = Transaction(
        account_id=account.id,
        instrument_id=instrument.id,
        txn_type="buy",
        date=trade_date,
        quantity=Decimal(qty),
        unit_price=Decimal(price),
        price_currency="EUR",
        fx_rate_to_eur=Decimal("1"),
        cost_basis_eur=Decimal(qty) * Decimal(price),
    )
    session.add(txn)
    await session.flush()
    return txn


async def _disposal(
    session: AsyncSession,
    account: Account,
    instrument: Instrument,
    buy: Transaction,
    *,
    txn_type: str = "sell",
    qty: str = "1",
    price: str = "150",
    gain: str = "50",
    trade_date: date = date(2026, 2, 1),
    deleted: bool = False,
) -> Transaction:
    txn = Transaction(
        account_id=account.id,
        instrument_id=instrument.id,
        txn_type=txn_type,
        date=trade_date,
        quantity=-Decimal(qty),
        unit_price=Decimal(price),
        price_currency="EUR",
        fx_rate_to_eur=Decimal("1"),
        deleted_at=datetime.utcnow() if deleted else None,
    )
    session.add(txn)
    await session.flush()
    session.add(
        LotAlloc(
            sell_txn_id=txn.id,
            buy_txn_id=buy.id,
            quantity=Decimal(qty),
            realized_gain_eur=Decimal(gain),
        )
    )
    await session.flush()
    return txn


async def _tag(
    session: AsyncSession, account: Account, instrument: Instrument, name: str
) -> None:
    tag = Tag(name=name, color="#22c55e")
    session.add(tag)
    await session.flush()
    session.add(HoldingTag(account_id=account.id, instrument_id=instrument.id, tag_id=tag.id))
    await session.flush()


@pytest.mark.asyncio
async def test_realized_per_holding_sums_fifo_gain(session):
    account, instrument = await _holding(session, symbol="WIN")
    buy = await _buy(session, account, instrument, price="100")
    await _disposal(session, account, instrument, buy, price="150", gain="50")

    rows = await get_realized_per_holding(session)

    assert [(row.instrument_symbol, row.realized_eur) for row in rows] == [
        ("WIN", Decimal("50"))
    ]


@pytest.mark.asyncio
async def test_holding_without_disposal_is_absent(session):
    account, instrument = await _holding(session, symbol="OPEN")
    await _buy(session, account, instrument, price="100")

    rows = await get_realized_per_holding(session)

    assert rows == []


@pytest.mark.asyncio
async def test_spend_disposal_contributes_to_realized(session):
    account, instrument = await _holding(session, symbol="SPND")
    buy = await _buy(session, account, instrument, price="10")
    await _disposal(session, account, instrument, buy, txn_type="spend", price="100", gain="90")

    totals = await get_realized_totals(session)
    rows = await get_realized_per_holding(session)

    assert totals.lifetime == Decimal("90")
    assert rows[0].realized_eur == Decimal("90")


@pytest.mark.asyncio
async def test_realized_totals_include_lifetime_and_this_year(session):
    account, instrument = await _holding(session, symbol="TOT")
    old_buy = await _buy(session, account, instrument, trade_date=date(2024, 1, 1))
    await _disposal(
        session,
        account,
        instrument,
        old_buy,
        gain="20",
        trade_date=date(date.today().year - 1, 6, 1),
    )
    new_buy = await _buy(session, account, instrument, trade_date=date(date.today().year, 1, 2))
    await _disposal(
        session,
        account,
        instrument,
        new_buy,
        gain="30",
        trade_date=date(date.today().year, 2, 1),
    )

    totals = await get_realized_totals(session)

    assert totals.lifetime == Decimal("50")
    assert totals.this_year == Decimal("30")


@pytest.mark.asyncio
async def test_realized_totals_this_year_boundary_uses_local_calendar(session, monkeypatch):
    """Between local midnight and ~02:00 UTC on Jan 1, clock.today() (UTC) is
    still Dec 31 while clock.today_local() (Madrid) has already turned over.
    "This year" must bucket by the local date, not UTC, or it sweeps in a
    prior-year gain during that window.
    """
    account, instrument = await _holding(session, symbol="BOUND")
    old_buy = await _buy(session, account, instrument, trade_date=date(2024, 1, 1))
    await _disposal(
        session, account, instrument, old_buy, gain="40", trade_date=date(2025, 12, 30)
    )
    new_buy = await _buy(session, account, instrument, trade_date=date(2024, 1, 1))
    await _disposal(
        session, account, instrument, new_buy, gain="15", trade_date=date(2026, 1, 1)
    )

    monkeypatch.setattr("app.core.clock.today", lambda: date(2025, 12, 31))
    monkeypatch.setattr("app.core.clock.today_local", lambda: date(2026, 1, 1))

    totals = await get_realized_totals(session)

    assert totals.lifetime == Decimal("55")
    assert totals.this_year == Decimal("15")


@pytest.mark.asyncio
async def test_realized_totals_respect_tag_filter(session):
    tagged_account, tagged_instrument = await _holding(session, symbol="TAG")
    tagged_buy = await _buy(session, tagged_account, tagged_instrument)
    await _disposal(session, tagged_account, tagged_instrument, tagged_buy, gain="70")
    await _tag(session, tagged_account, tagged_instrument, "growth")
    other_account, other_instrument = await _holding(session, symbol="OTHER")
    other_buy = await _buy(session, other_account, other_instrument)
    await _disposal(session, other_account, other_instrument, other_buy, gain="40")

    totals = await get_realized_totals(session, tag_filter="growth")

    assert totals.lifetime == Decimal("70")


@pytest.mark.asyncio
async def test_realized_per_holding_respects_tag_filter(session):
    tagged_account, tagged_instrument = await _holding(session, symbol="TAG")
    tagged_buy = await _buy(session, tagged_account, tagged_instrument)
    await _disposal(session, tagged_account, tagged_instrument, tagged_buy, gain="70")
    await _tag(session, tagged_account, tagged_instrument, "growth")
    other_account, other_instrument = await _holding(session, symbol="OTHER")
    other_buy = await _buy(session, other_account, other_instrument)
    await _disposal(session, other_account, other_instrument, other_buy, gain="40")

    rows = await get_realized_per_holding(session, tag_filter="growth")

    assert [row.instrument_symbol for row in rows] == ["TAG"]


@pytest.mark.asyncio
async def test_soft_deleted_disposals_are_excluded(session):
    account, instrument = await _holding(session, symbol="DEAD")
    buy = await _buy(session, account, instrument)
    await _disposal(session, account, instrument, buy, gain="60", deleted=True)

    totals = await get_realized_totals(session)

    assert totals.lifetime == Decimal("0")
    assert await get_realized_per_holding(session) == []


@pytest.mark.asyncio
async def test_perf_rows_include_realized_eur(session):
    account, instrument = await _holding(session, symbol="PERF")
    buy = await _buy(session, account, instrument, qty="2", trade_date=date.today())
    await _disposal(session, account, instrument, buy, gain="25", trade_date=date.today())

    rows = await get_performance_rows(session, timeframe="all", display_currency="EUR")

    assert rows[0].realized_eur == Decimal("25")


@pytest.mark.asyncio
async def test_perf_tag_filter_returns_subset(session):
    tagged_account, tagged_instrument = await _holding(session, symbol="TAG")
    await _buy(session, tagged_account, tagged_instrument, trade_date=date.today())
    await _tag(session, tagged_account, tagged_instrument, "growth")
    other_account, other_instrument = await _holding(session, symbol="OTHER")
    await _buy(session, other_account, other_instrument, trade_date=date.today())

    rows = await get_performance_rows(
        session, timeframe="all", display_currency="EUR", tag_filter="growth"
    )

    assert [(row.account_id, row.instrument_id) for row in rows] == [
        (tagged_account.id, tagged_instrument.id)
    ]


@pytest.mark.asyncio
async def test_get_perf_json_contains_realized_eur(authed_client):
    client, maker = authed_client
    async with maker() as session:
        account, instrument = await _holding(session, symbol="API")
        buy = await _buy(session, account, instrument, qty="2", trade_date=date.today())
        await _disposal(session, account, instrument, buy, gain="15", trade_date=date.today())
        await session.commit()

    response = await client.get("/api/perf?timeframe=all&currency=EUR")

    assert response.status_code == 200, response.text
    # Money serializes in canonical plain form, no scale padding.
    assert response.json()[0]["realized_eur"] == "15"
