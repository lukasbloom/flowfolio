from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base, attach_sqlite_pragmas
from app.models import Account, ApyConfig, Instrument, PriceQuote, Transaction
from app.services.accrual import _get_balance_through
from app.services.fifo import match_lots_for_sell
from app.services.networth import get_networth_series
from app.services.perf import get_performance_rows


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


async def _holding(session: AsyncSession) -> tuple[Account, Instrument]:
    account = Account(name="Audit Account", account_type="broker", currency="EUR")
    instrument = Instrument(
        symbol="AUD",
        name="Audit Holding",
        instrument_type="stock",
        base_currency="EUR",
        price_source="manual",
    )
    session.add_all([account, instrument])
    await session.flush()
    return account, instrument


async def _buy(
    session: AsyncSession,
    account: Account,
    instrument: Instrument,
    *,
    quantity: str = "1",
    trade_date: date = date(2026, 1, 1),
    deleted: bool = False,
) -> Transaction:
    txn = Transaction(
        account_id=account.id,
        instrument_id=instrument.id,
        txn_type="buy",
        date=trade_date,
        quantity=Decimal(quantity),
        unit_price=Decimal("100"),
        price_currency="EUR",
        fx_rate_to_eur=Decimal("1"),
        cost_basis_eur=Decimal(quantity) * Decimal("100"),
        deleted_at=datetime.utcnow() if deleted else None,
    )
    session.add(txn)
    await session.flush()
    return txn


async def _sell(
    session: AsyncSession,
    account: Account,
    instrument: Instrument,
    *,
    quantity: str = "1",
    trade_date: date = date(2026, 1, 2),
) -> Transaction:
    txn = Transaction(
        account_id=account.id,
        instrument_id=instrument.id,
        txn_type="sell",
        date=trade_date,
        quantity=-Decimal(quantity),
        unit_price=Decimal("100"),
        price_currency="EUR",
        fx_rate_to_eur=Decimal("1"),
    )
    session.add(txn)
    await session.flush()
    return txn


async def _quote(
    session: AsyncSession, instrument: Instrument, quote_date: date = date(2026, 1, 1)
) -> None:
    session.add(
        PriceQuote(
            instrument_id=instrument.id,
            date=quote_date,
            price=Decimal("100"),
            currency="EUR",
            source="manual",
        )
    )
    await session.flush()


@pytest.mark.asyncio
async def test_networth_ignores_soft_deleted_buy(session):
    account, instrument = await _holding(session)
    await _buy(session, account, instrument, deleted=True)
    await _quote(session, instrument)

    series = await get_networth_series(
        session,
        timeframe="custom",
        display_currency="EUR",
        start=date(2026, 1, 1),
        end=date(2026, 1, 1),
    )

    assert series.points[0].value == Decimal("0E-8")


@pytest.mark.asyncio
async def test_fifo_ignores_soft_deleted_buy(session):
    account, instrument = await _holding(session)
    await _buy(session, account, instrument, deleted=True)
    sell = await _sell(session, account, instrument)

    with pytest.raises(ValueError, match="exceeds available lots"):
        await match_lots_for_sell(session, sell)


@pytest.mark.asyncio
async def test_accrual_balance_ignores_soft_deleted_buy(session):
    account, instrument = await _holding(session)
    await _buy(session, account, instrument, quantity="10", deleted=True)
    session.add(
        ApyConfig(
            account_id=account.id,
            instrument_id=instrument.id,
            apy_rate=Decimal("0.05"),
            effective_from=date(2026, 1, 1) - timedelta(days=1),
            compounding="daily_simple",
        )
    )
    await session.flush()

    balance = await _get_balance_through(
        session, account.id, instrument.id, date(2026, 1, 1)
    )

    assert balance == Decimal("0")


@pytest.mark.asyncio
async def test_perf_ignores_soft_deleted_buy(session):
    account, instrument = await _holding(session)
    await _buy(session, account, instrument, deleted=True)
    await _quote(session, instrument)

    rows = await get_performance_rows(
        session, timeframe="all", display_currency="EUR", today=date(2026, 1, 1)
    )

    assert rows == []
