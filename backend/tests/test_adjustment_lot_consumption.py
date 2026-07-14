"""Downward adjustments consume FIFO open lots (plan 008).

A negative reconciliation adjustment trims a holding. These tests pin the FIXED
behavior: the trim matches open lots like a sell (creating LotAlloc rows with
realized_gain_eur=None), so the open-lot decomposition and sell availability
both reflect the reduced balance, and realized totals never move because of a
correction.
"""
from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base
from app.models import Account, Instrument, LotAlloc, Transaction
from app.services.cost_basis import _load_allocations, _open_lots_at
from app.services.fifo import match_lots_for_sell, recompute_fifo_for_pair
from app.services.realized import get_realized_per_holding


@pytest_asyncio.fixture
async def session():
    """In-memory async SQLite session for each test."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


async def _seed_account_instrument(session: AsyncSession) -> tuple[str, str]:
    acct = Account(name="Revolut", account_type="broker", currency="EUR")
    inst = Instrument(
        symbol="BTC",
        name="Bitcoin",
        instrument_type="crypto",
        base_currency="EUR",
        price_source="coingecko",
    )
    session.add_all([acct, inst])
    await session.flush()
    return acct.id, inst.id


async def _make_buy(
    session: AsyncSession,
    account_id: str,
    instrument_id: str,
    qty: Decimal,
    trade_date: date,
    unit_price: Decimal = Decimal("10"),
) -> Transaction:
    txn = Transaction(
        account_id=account_id,
        instrument_id=instrument_id,
        txn_type="buy",
        date=trade_date,
        quantity=qty,
        unit_price=unit_price,
        price_currency="EUR",
        fx_rate_to_eur=Decimal("1"),
        cost_basis_eur=(qty * unit_price / Decimal("1")),
    )
    session.add(txn)
    await session.flush()
    return txn


async def _make_sell(
    session: AsyncSession,
    account_id: str,
    instrument_id: str,
    qty: Decimal,
    trade_date: date,
    unit_price: Decimal = Decimal("20"),
) -> Transaction:
    txn = Transaction(
        account_id=account_id,
        instrument_id=instrument_id,
        txn_type="sell",
        date=trade_date,
        quantity=-qty,  # sells stored as negative
        unit_price=unit_price,
        price_currency="EUR",
        fx_rate_to_eur=Decimal("1"),
    )
    session.add(txn)
    await session.flush()
    return txn


async def _make_adjustment(
    session: AsyncSession,
    account_id: str,
    instrument_id: str,
    delta_qty: Decimal,
    trade_date: date,
) -> Transaction:
    """Mirror reconciliation._write_adjustment_txn: signed delta, no price/FX."""
    txn = Transaction(
        account_id=account_id,
        instrument_id=instrument_id,
        txn_type="adjustment",
        date=trade_date,
        quantity=delta_qty,
        unit_price=None,
        price_currency=None,
        fx_rate_to_eur=None,
        cost_basis_eur=None,
        fee_eur=Decimal("0"),
        source="adjustment",
    )
    session.add(txn)
    await session.flush()
    return txn


@pytest.mark.asyncio
async def test_negative_adjustment_reduces_open_lot(session):
    """buy(100 @ 10) then adjustment(-30): the open lot decomposition shows a
    70-share lot with proportional cost basis 700, not the untrimmed 1000."""
    acct_id, inst_id = await _seed_account_instrument(session)
    buy = await _make_buy(session, acct_id, inst_id, Decimal("100"), date(2024, 1, 1))
    adj = await _make_adjustment(session, acct_id, inst_id, Decimal("-30"), date(2024, 2, 1))

    # recompute matches the negative adjustment against the buy lot.
    await recompute_fifo_for_pair(session, acct_id, inst_id)

    allocations = await _load_allocations(session, {adj.id})
    open_lots = _open_lots_at([buy], allocations, date(2024, 3, 1))

    assert open_lots == [(date(2024, 1, 1), Decimal("700"))]


@pytest.mark.asyncio
async def test_sell_after_adjustment_cannot_over_consume(session):
    """buy(100), adjustment(-30), then sell(80): only 70 remain, so the sell
    must raise instead of over-consuming quantity the adjustment removed."""
    acct_id, inst_id = await _seed_account_instrument(session)
    await _make_buy(session, acct_id, inst_id, Decimal("100"), date(2024, 1, 1))
    await _make_adjustment(session, acct_id, inst_id, Decimal("-30"), date(2024, 2, 1))
    await recompute_fifo_for_pair(session, acct_id, inst_id)

    sell = await _make_sell(session, acct_id, inst_id, Decimal("80"), date(2024, 3, 1))
    with pytest.raises(ValueError, match="exceeds available lots"):
        await match_lots_for_sell(session, sell)


@pytest.mark.asyncio
async def test_sell_within_post_adjustment_balance_succeeds(session):
    """buy(100), adjustment(-30), then sell(70): matches exactly the surviving
    70 shares."""
    acct_id, inst_id = await _seed_account_instrument(session)
    buy = await _make_buy(session, acct_id, inst_id, Decimal("100"), date(2024, 1, 1))
    await _make_adjustment(session, acct_id, inst_id, Decimal("-30"), date(2024, 2, 1))
    await recompute_fifo_for_pair(session, acct_id, inst_id)

    sell = await _make_sell(session, acct_id, inst_id, Decimal("70"), date(2024, 3, 1))
    allocs = await match_lots_for_sell(session, sell)

    assert sum(a.quantity for a in allocs) == Decimal("70")
    assert {a.buy_txn_id for a in allocs} == {buy.id}


@pytest.mark.asyncio
async def test_adjustment_alloc_is_not_realized_and_totals_unchanged(session):
    """The adjustment's alloc carries realized_gain_eur=None, and the holding's
    realized total is the sell's gain alone, unmoved by the correction."""
    acct_id, inst_id = await _seed_account_instrument(session)
    await _make_buy(session, acct_id, inst_id, Decimal("100"), date(2024, 1, 1))
    sell = await _make_sell(
        session, acct_id, inst_id, Decimal("50"), date(2024, 3, 1), unit_price=Decimal("20")
    )
    await match_lots_for_sell(session, sell)
    await session.flush()

    before = await _realized_for_instrument(session, inst_id)
    assert before == Decimal("500")  # (20 - 10) * 50

    adj = await _make_adjustment(session, acct_id, inst_id, Decimal("-20"), date(2024, 4, 1))
    await recompute_fifo_for_pair(session, acct_id, inst_id)

    adj_allocs = (
        await session.execute(select(LotAlloc).where(LotAlloc.sell_txn_id == adj.id))
    ).scalars().all()
    assert adj_allocs, "the negative adjustment must create an alloc"
    assert all(a.realized_gain_eur is None for a in adj_allocs)
    assert sum(a.quantity for a in adj_allocs) == Decimal("20")

    after = await _realized_for_instrument(session, inst_id)
    assert after == before == Decimal("500")


@pytest.mark.asyncio
async def test_backdated_adjustment_reorders_consumption_in_date_order(session):
    """A back-dated negative adjustment recomputes FIFO for the whole pair: the
    earlier adjustment consumes the oldest lot ahead of the later sell, which
    then spills into the newer lot (makes reconciliation's docstring claim true)."""
    acct_id, inst_id = await _seed_account_instrument(session)
    buy1 = await _make_buy(
        session, acct_id, inst_id, Decimal("50"), date(2024, 1, 1), unit_price=Decimal("10")
    )
    buy2 = await _make_buy(
        session, acct_id, inst_id, Decimal("50"), date(2024, 6, 1), unit_price=Decimal("20")
    )
    sell = await _make_sell(session, acct_id, inst_id, Decimal("50"), date(2024, 7, 1))
    await match_lots_for_sell(session, sell)
    await session.flush()

    # Before the adjustment the sell sits entirely on buy1 (oldest).
    pre = (
        await session.execute(select(LotAlloc).where(LotAlloc.sell_txn_id == sell.id))
    ).scalars().all()
    assert {a.buy_txn_id for a in pre} == {buy1.id}

    # Back-dated trim lands between buy1 and buy2, ahead of the sell in date order.
    adj = await _make_adjustment(session, acct_id, inst_id, Decimal("-30"), date(2024, 3, 1))
    await recompute_fifo_for_pair(session, acct_id, inst_id)

    adj_allocs = (
        await session.execute(select(LotAlloc).where(LotAlloc.sell_txn_id == adj.id))
    ).scalars().all()
    # Adjustment (2024-03-01) consumes 30 from the oldest lot first.
    assert {a.buy_txn_id for a in adj_allocs} == {buy1.id}
    assert sum(a.quantity for a in adj_allocs) == Decimal("30")

    sell_allocs = (
        await session.execute(select(LotAlloc).where(LotAlloc.sell_txn_id == sell.id))
    ).scalars().all()
    by_buy = {a.buy_txn_id: a.quantity for a in sell_allocs}
    # The sell now spills: 20 left on buy1 after the trim, then 30 from buy2.
    assert by_buy.get(buy1.id) == Decimal("20")
    assert by_buy.get(buy2.id) == Decimal("30")
    assert sum(a.quantity for a in sell_allocs) == Decimal("50")


async def _realized_for_instrument(session: AsyncSession, instrument_id: str) -> Decimal:
    rows = await get_realized_per_holding(session, "EUR")
    for row in rows:
        if row.instrument_id == instrument_id:
            return row.realized_eur
    return Decimal("0")
