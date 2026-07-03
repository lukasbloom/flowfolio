from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base, attach_sqlite_pragmas
from app.models import (
    Account,
    FxRate,
    HoldingTag,
    Instrument,
    LotAlloc,
    PriceQuote,
    Tag,
    Transaction,
)
from app.services.closed import get_closed_positions


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


async def _account_instrument(
    session: AsyncSession,
    *,
    account_name: str = "Revolut",
    symbol: str = "BTC",
) -> tuple[Account, Instrument]:
    account = Account(name=account_name, account_type="broker", currency="EUR")
    instrument = Instrument(
        symbol=symbol,
        name=f"{symbol} Holding",
        instrument_type="crypto",
        risk_level="High",
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
    trade_date: date,
    qty: str = "1",
    price: str = "100",
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


async def _sell(
    session: AsyncSession,
    account: Account,
    instrument: Instrument,
    buy: Transaction,
    *,
    trade_date: date,
    qty: str = "1",
    price: str = "150",
    realized_gain_eur: str = "50",
    deleted: bool = False,
) -> Transaction:
    txn = Transaction(
        account_id=account.id,
        instrument_id=instrument.id,
        txn_type="sell",
        date=trade_date,
        quantity=-Decimal(qty),
        unit_price=Decimal(price),
        price_currency="EUR",
        fx_rate_to_eur=Decimal("1"),
        deleted_at=datetime.utcnow() if deleted else None,
    )
    session.add(txn)
    await session.flush()
    if not deleted:
        session.add(
            LotAlloc(
                sell_txn_id=txn.id,
                buy_txn_id=buy.id,
                quantity=Decimal(qty),
                realized_gain_eur=Decimal(realized_gain_eur),
            )
        )
    await session.flush()
    return txn


async def _quote(
    session: AsyncSession,
    instrument: Instrument,
    *,
    quote_date: date,
    price: str,
) -> None:
    session.add(
        PriceQuote(
            instrument_id=instrument.id,
            date=quote_date,
            price=Decimal(price),
            currency="EUR",
            source="manual",
        )
    )
    await session.flush()


async def _closed_holding(
    session: AsyncSession,
    *,
    account_name: str = "Revolut",
    symbol: str = "BTC",
    buy_date: date = date(2025, 1, 1),
    sell_date: date = date(2025, 6, 1),
) -> tuple[Account, Instrument]:
    account, instrument = await _account_instrument(
        session, account_name=account_name, symbol=symbol
    )
    buy = await _buy(session, account, instrument, trade_date=buy_date)
    await _sell(session, account, instrument, buy, trade_date=sell_date)
    await _quote(session, instrument, quote_date=buy_date, price="100")
    await _quote(session, instrument, quote_date=sell_date, price="150")
    return account, instrument


async def _tag_holding(
    session: AsyncSession, account: Account, instrument: Instrument, name: str
) -> None:
    tag = Tag(name=name, color="#00aa00")
    session.add(tag)
    await session.flush()
    session.add(
        HoldingTag(account_id=account.id, instrument_id=instrument.id, tag_id=tag.id)
    )
    await session.flush()


@pytest.mark.asyncio
async def test_closed_position_returns_qty_zero_last_close_and_percent_return(session):
    await _closed_holding(session)

    rows = await get_closed_positions(session, display_currency="EUR")

    assert len(rows) == 1
    row = rows[0]
    assert row.quantity == Decimal("0")
    assert row.last_close == Decimal("150.00000000")
    assert row.last_close_date == date(2025, 6, 1)
    assert row.percent_return == Decimal("0.5")
    assert row.realized_eur == Decimal("50.00000000")


@pytest.mark.asyncio
async def test_closed_position_realized_gain_uses_display_currency(session):
    await _closed_holding(session, sell_date=date(2025, 6, 1))
    session.add(
        FxRate(
            date=date(2025, 6, 1),
            base_currency="EUR",
            quote_currency="USD",
            rate=Decimal("1.20"),
            source="manual",
        )
    )
    await session.flush()

    rows = await get_closed_positions(session, display_currency="USD")

    assert rows[0].realized_eur == Decimal("60")


@pytest.mark.asyncio
async def test_open_holding_does_not_appear_in_closed_positions(session):
    account, instrument = await _account_instrument(session)
    buy = await _buy(session, account, instrument, trade_date=date(2025, 1, 1), qty="1")
    await _sell(
        session,
        account,
        instrument,
        buy,
        trade_date=date(2025, 6, 1),
        qty="0.5",
        realized_gain_eur="25",
    )
    await _quote(session, instrument, quote_date=date(2025, 1, 1), price="100")
    await _quote(session, instrument, quote_date=date(2025, 6, 1), price="150")

    assert await get_closed_positions(session, display_currency="EUR") == []


@pytest.mark.asyncio
async def test_closed_positions_tag_filter_returns_only_tagged_closed_holdings(session):
    tagged_account, tagged_instrument = await _closed_holding(
        session, account_name="Revolut", symbol="BTC"
    )
    await _closed_holding(session, account_name="XTB", symbol="ETH")
    await _tag_holding(session, tagged_account, tagged_instrument, "growth")

    rows = await get_closed_positions(
        session, display_currency="EUR", tag_filter="growth"
    )

    assert len(rows) == 1
    assert rows[0].instrument_symbol == "BTC"


@pytest.mark.asyncio
async def test_closed_positions_exclude_soft_deleted_disposals(session):
    account, instrument = await _account_instrument(session)
    buy = await _buy(session, account, instrument, trade_date=date(2025, 1, 1))
    await _sell(
        session,
        account,
        instrument,
        buy,
        trade_date=date(2025, 6, 1),
        deleted=True,
    )
    await _quote(session, instrument, quote_date=date(2025, 1, 1), price="100")
    await _quote(session, instrument, quote_date=date(2025, 6, 1), price="150")

    assert await get_closed_positions(session, display_currency="EUR") == []


@pytest.mark.asyncio
async def test_closed_position_last_close_date_is_final_disposing_transaction(session):
    await _closed_holding(session, sell_date=date(2025, 7, 15))

    rows = await get_closed_positions(session, display_currency="EUR")

    assert rows[0].last_close_date == date(2025, 7, 15)


@pytest.mark.asyncio
async def test_closed_position_twrr_uses_hold_window_and_marks_annualized(session):
    await _closed_holding(
        session,
        buy_date=date(2024, 1, 1),
        sell_date=date(2025, 1, 2),
    )

    rows = await get_closed_positions(session, display_currency="EUR")

    assert rows[0].twrr is not None
    assert rows[0].twrr_window_days == 367
    assert rows[0].twrr_annualized is True
