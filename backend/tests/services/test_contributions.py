from __future__ import annotations

from datetime import date, datetime, timedelta
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
from app.services.contributions import get_contribution_segments, get_cost_basis_series


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


async def _holding(
    session: AsyncSession, *, symbol: str = "FLOW"
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
    return account, instrument


async def _txn(
    session: AsyncSession,
    account: Account,
    instrument: Instrument,
    *,
    txn_type: str,
    trade_date: date,
    quantity: str,
    unit_price: str | None = "100",
    cost_basis_eur: str | None = None,
    trade_pair_id: str | None = None,
    deleted: bool = False,
) -> Transaction:
    txn = Transaction(
        account_id=account.id,
        instrument_id=instrument.id,
        txn_type=txn_type,
        date=trade_date,
        quantity=Decimal(quantity),
        unit_price=Decimal(unit_price) if unit_price is not None else None,
        price_currency="EUR" if unit_price is not None else None,
        fx_rate_to_eur=Decimal("1") if unit_price is not None else None,
        cost_basis_eur=Decimal(cost_basis_eur) if cost_basis_eur is not None else None,
        trade_pair_id=trade_pair_id,
        deleted_at=datetime.utcnow() if deleted else None,
    )
    session.add(txn)
    await session.flush()
    return txn


async def _quote(
    session: AsyncSession, instrument: Instrument, quote_date: date, price: str
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


async def _fx(
    session: AsyncSession, fx_date: date, rate: str
) -> None:
    session.add(
        FxRate(
            base_currency="EUR",
            quote_currency="USD",
            date=fx_date,
            rate=Decimal(rate),
            source="manual",
        )
    )
    await session.flush()


async def _alloc(
    session: AsyncSession,
    sell: Transaction,
    buy: Transaction,
    *,
    quantity: str,
    gain: str = "0",
) -> None:
    session.add(
        LotAlloc(
            sell_txn_id=sell.id,
            buy_txn_id=buy.id,
            quantity=Decimal(quantity),
            realized_gain_eur=Decimal(gain),
        )
    )
    await session.flush()


async def _tag(
    session: AsyncSession, account: Account, instrument: Instrument, name: str
) -> None:
    tag = Tag(name=name, color="#22c55e")
    session.add(tag)
    await session.flush()
    session.add(HoldingTag(account_id=account.id, instrument_id=instrument.id, tag_id=tag.id))
    await session.flush()


def _point_map(points):
    return {point.date: point.value for point in points}


def _bucket_map(buckets):
    return {bucket.period_start: bucket for bucket in buckets}


@pytest.mark.asyncio
async def test_cost_basis_series_steps_up_on_buy(session):
    account, instrument = await _holding(session)
    buy_date = date.today() - timedelta(days=2)
    await _txn(
        session,
        account,
        instrument,
        txn_type="buy",
        trade_date=buy_date,
        quantity="1",
        unit_price="100",
        cost_basis_eur="100",
    )
    await _quote(session, instrument, buy_date, "100")

    cost_basis, _ = await get_cost_basis_series(session)

    points = _point_map(cost_basis)
    assert points[buy_date] == Decimal("100")
    assert points[buy_date + timedelta(days=1)] == Decimal("100")


@pytest.mark.asyncio
async def test_usd_cost_basis_series_is_constant_between_transaction_dates(session):
    """Regression: cost-basis-line-drifts-daily.

    Before the transaction-time-FX fix, the USD-display cost-basis line was
    re-converted at EACH chart day's EUR/USD rate, so it drifted daily even on
    no-transaction days. With transaction-time FX (each open lot converted at
    ITS OWN transaction-date rate), the USD line must hold FLAT between
    transaction dates and only step when a buy/sell settles — regardless of
    daily FX movement.
    """
    account, instrument = await _holding(session)
    buy_date = date.today() - timedelta(days=4)
    # USD-priced buy: cost_basis_eur is stamped (price / fx_rate_to_eur), and
    # fx_rate_to_eur is the EUR/USD rate locked at the trade date.
    buy = Transaction(
        account_id=account.id,
        instrument_id=instrument.id,
        txn_type="buy",
        date=buy_date,
        quantity=Decimal("1"),
        unit_price=Decimal("110"),
        price_currency="USD",
        fx_rate_to_eur=Decimal("1.10"),  # 110 USD / 1.10 = 100 EUR cost basis
        cost_basis_eur=Decimal("100"),
    )
    session.add(buy)
    await session.flush()

    # FX moves every single day AFTER the buy date — the drift trigger.
    await _fx(session, buy_date, "1.10")
    await _fx(session, buy_date + timedelta(days=1), "1.20")
    await _fx(session, buy_date + timedelta(days=2), "1.30")
    await _fx(session, buy_date + timedelta(days=3), "1.40")
    await _fx(session, date.today(), "1.50")

    cost_basis, _ = await get_cost_basis_series(session, display_currency="USD")
    points = _point_map(cost_basis)

    # Every day's USD cost basis equals the lot's transaction-time USD value
    # (100 EUR * 1.10 = 110 USD), held flat across all no-transaction days.
    usd_values = [points[buy_date + timedelta(days=offset)] for offset in range(5)]
    assert usd_values[0] == Decimal("110.00")
    # The whole post-buy series is byte-constant — no daily FX drift.
    assert len(set(usd_values)) == 1


@pytest.mark.asyncio
async def test_cost_basis_series_steps_down_on_sell(session):
    account, instrument = await _holding(session)
    buy = await _txn(
        session,
        account,
        instrument,
        txn_type="buy",
        trade_date=date.today() - timedelta(days=2),
        quantity="1",
        unit_price="100",
        cost_basis_eur="100",
    )
    sell_date = date.today() - timedelta(days=1)
    sell = await _txn(
        session,
        account,
        instrument,
        txn_type="sell",
        trade_date=sell_date,
        quantity="-1",
        unit_price="120",
        trade_pair_id="pair-1",
    )
    await _alloc(session, sell, buy, quantity="1", gain="20")

    cost_basis, _ = await get_cost_basis_series(session)

    assert _point_map(cost_basis)[sell_date] == Decimal("0")


@pytest.mark.asyncio
async def test_cost_basis_series_steps_down_by_consumed_basis_on_spend(session):
    account, instrument = await _holding(session)
    buy = await _txn(
        session,
        account,
        instrument,
        txn_type="buy",
        trade_date=date.today() - timedelta(days=2),
        quantity="1",
        unit_price="10",
        cost_basis_eur="10",
    )
    spend_date = date.today() - timedelta(days=1)
    spend = await _txn(
        session,
        account,
        instrument,
        txn_type="spend",
        trade_date=spend_date,
        quantity="-1",
        unit_price="100",
    )
    await _alloc(session, spend, buy, quantity="1", gain="90")

    cost_basis, _ = await get_cost_basis_series(session)

    assert _point_map(cost_basis)[spend_date] == Decimal("0")


@pytest.mark.asyncio
async def test_portfolio_value_series_uses_daily_replay_prices(session):
    account, instrument = await _holding(session)
    buy_date = date.today() - timedelta(days=1)
    await _txn(
        session,
        account,
        instrument,
        txn_type="buy",
        trade_date=buy_date,
        quantity="1",
        unit_price="100",
        cost_basis_eur="100",
    )
    await _quote(session, instrument, buy_date, "100")
    await _quote(session, instrument, date.today(), "200")

    cost_basis, value = await get_cost_basis_series(session)

    assert _point_map(cost_basis)[date.today()] == Decimal("100")
    assert _point_map(value)[date.today()] == Decimal("200.00000000")


@pytest.mark.asyncio
async def test_month_segments_classify_deposits_spendings_realized_and_yield(session):
    account, instrument = await _holding(session)
    month_start = date.today().replace(day=1)
    old_buy = await _txn(
        session,
        account,
        instrument,
        txn_type="buy",
        trade_date=month_start - timedelta(days=1),
        quantity="2",
        unit_price="50",
        cost_basis_eur="100",
    )
    await _txn(
        session,
        account,
        instrument,
        txn_type="buy",
        trade_date=month_start,
        quantity="1",
        unit_price="5000",
        cost_basis_eur="5000",
    )
    spend = await _txn(
        session,
        account,
        instrument,
        txn_type="spend",
        trade_date=month_start + timedelta(days=1),
        quantity="-1",
        unit_price="100",
    )
    await _alloc(session, spend, old_buy, quantity="1", gain="50")
    sell = await _txn(
        session,
        account,
        instrument,
        txn_type="sell",
        trade_date=month_start + timedelta(days=2),
        quantity="-1",
        unit_price="140",
        trade_pair_id="pair-realized",
    )
    await _alloc(session, sell, old_buy, quantity="1", gain="90")
    await _txn(
        session,
        account,
        instrument,
        txn_type="yield",
        trade_date=month_start + timedelta(days=3),
        quantity="0.1",
        unit_price=None,
        cost_basis_eur="7",
    )

    buckets = _bucket_map(await get_contribution_segments(session, period="month"))
    bucket = buckets[month_start]

    assert bucket.deposits == Decimal("5000")
    assert bucket.spendings == Decimal("50")
    assert bucket.realized_gains == Decimal("90")
    assert bucket.yield_amount == Decimal("7")


@pytest.mark.asyncio
async def test_year_segments_aggregate_month_buckets(session):
    account, instrument = await _holding(session)
    await _txn(
        session,
        account,
        instrument,
        txn_type="buy",
        trade_date=date(date.today().year, 1, 15),
        quantity="1",
        unit_price="10",
        cost_basis_eur="10",
    )
    await _txn(
        session,
        account,
        instrument,
        txn_type="buy",
        trade_date=date(date.today().year, 2, 15),
        quantity="1",
        unit_price="20",
        cost_basis_eur="20",
    )

    buckets = await get_contribution_segments(session, period="year")

    assert len(buckets) == 1
    assert buckets[0].period_start == date(date.today().year, 1, 1)
    assert buckets[0].deposits == Decimal("30")


@pytest.mark.asyncio
async def test_contribution_functions_respect_tag_filter(session):
    tagged_account, tagged_instrument = await _holding(session, symbol="TAG")
    await _txn(
        session,
        tagged_account,
        tagged_instrument,
        txn_type="buy",
        trade_date=date.today(),
        quantity="1",
        unit_price="100",
        cost_basis_eur="100",
    )
    await _quote(session, tagged_instrument, date.today(), "100")
    await _tag(session, tagged_account, tagged_instrument, "growth")
    other_account, other_instrument = await _holding(session, symbol="OTHER")
    await _txn(
        session,
        other_account,
        other_instrument,
        txn_type="buy",
        trade_date=date.today(),
        quantity="1",
        unit_price="200",
        cost_basis_eur="200",
    )
    await _quote(session, other_instrument, date.today(), "200")

    cost_basis, value = await get_cost_basis_series(session, tag_filter="growth")
    buckets = await get_contribution_segments(session, period="month", tag_filter="growth")

    assert _point_map(cost_basis)[date.today()] == Decimal("100")
    assert _point_map(value)[date.today()] == Decimal("100.00000000")
    assert buckets[0].deposits == Decimal("100")


@pytest.mark.asyncio
async def test_soft_deleted_transactions_are_excluded(session):
    account, instrument = await _holding(session)
    await _txn(
        session,
        account,
        instrument,
        txn_type="buy",
        trade_date=date.today(),
        quantity="1",
        unit_price="100",
        cost_basis_eur="100",
        deleted=True,
    )

    cost_basis, value = await get_cost_basis_series(session)
    buckets = await get_contribution_segments(session, period="month")

    assert cost_basis == []
    assert value == []
    assert buckets == []


@pytest.mark.asyncio
async def test_spend_invariant_spendings_only_no_realized_segment(session):
    account, instrument = await _holding(session, symbol="BTC")
    buy = await _txn(
        session,
        account,
        instrument,
        txn_type="buy",
        trade_date=date.today().replace(day=1) - timedelta(days=1),
        quantity="0.1",
        unit_price="100",
        cost_basis_eur="10",
    )
    spend = await _txn(
        session,
        account,
        instrument,
        txn_type="spend",
        trade_date=date.today().replace(day=1),
        quantity="-0.1",
        unit_price="1000",
    )
    await _alloc(session, spend, buy, quantity="0.1", gain="90")

    buckets = _bucket_map(await get_contribution_segments(session, period="month"))
    bucket = buckets[date.today().replace(day=1)]

    assert bucket.deposits == Decimal("0")
    assert bucket.spendings == Decimal("10")
    assert bucket.realized_gains == Decimal("0")
    assert bucket.yield_amount == Decimal("0")


@pytest.mark.asyncio
async def test_linked_sell_invariant_realized_gain_not_linked_buy_deposit(session):
    account, btc = await _holding(session, symbol="BTC")
    _, msft = await _holding(session, symbol="MSFT")
    buy = await _txn(
        session,
        account,
        btc,
        txn_type="buy",
        trade_date=date.today().replace(day=1) - timedelta(days=1),
        quantity="0.1",
        unit_price="100",
        cost_basis_eur="10",
    )
    pair_id = "pair-linked"
    sell = await _txn(
        session,
        account,
        btc,
        txn_type="sell",
        trade_date=date.today().replace(day=1),
        quantity="-0.1",
        unit_price="1000",
        trade_pair_id=pair_id,
    )
    await _alloc(session, sell, buy, quantity="0.1", gain="90")
    await _txn(
        session,
        account,
        msft,
        txn_type="buy",
        trade_date=date.today().replace(day=1),
        quantity="1",
        unit_price="100",
        cost_basis_eur="100",
        trade_pair_id=pair_id,
    )

    buckets = _bucket_map(await get_contribution_segments(session, period="month"))
    bucket = buckets[date.today().replace(day=1)]

    assert bucket.deposits == Decimal("0")
    assert bucket.spendings == Decimal("0")
    assert bucket.realized_gains == Decimal("90")
    assert bucket.yield_amount == Decimal("0")
