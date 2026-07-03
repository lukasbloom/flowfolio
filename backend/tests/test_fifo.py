"""
FIFO lot matching tests using an in-memory SQLite database.

These tests verify the core financial correctness guarantee:
sell transactions consume buy lots oldest-first (FIFO / PEPS).
"""
from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base
from app.models import Account, Instrument, Transaction
from app.services.fifo import match_lots_for_sell


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
        base_currency="USD",
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
    unit_price: Decimal = Decimal("1000"),
) -> Transaction:
    txn = Transaction(
        account_id=account_id,
        instrument_id=instrument_id,
        txn_type="buy",
        date=trade_date,
        quantity=qty,
        unit_price=unit_price,
        price_currency="USD",
        fx_rate_to_eur=Decimal("1.1"),
        cost_basis_eur=(qty * unit_price / Decimal("1.1")),
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
    unit_price: Decimal = Decimal("2000"),
) -> Transaction:
    txn = Transaction(
        account_id=account_id,
        instrument_id=instrument_id,
        txn_type="sell",
        date=trade_date,
        quantity=-qty,  # sells stored as negative
        unit_price=unit_price,
        price_currency="USD",
        fx_rate_to_eur=Decimal("1.1"),
    )
    session.add(txn)
    await session.flush()
    return txn


@pytest.mark.asyncio
async def test_partial_sell_fifo(session):
    """
    3 buys (10, 5, 8 units), 1 sell of 12 units.
    FIFO: first lot fully consumed (10), second partially consumed (2).
    Buy 3 untouched.
    """
    acct_id, inst_id = await _seed_account_instrument(session)
    buy1 = await _make_buy(session, acct_id, inst_id, Decimal("10"), date(2024, 1, 1))
    buy2 = await _make_buy(session, acct_id, inst_id, Decimal("5"), date(2024, 2, 1))
    buy3 = await _make_buy(session, acct_id, inst_id, Decimal("8"), date(2024, 3, 1))
    sell = await _make_sell(session, acct_id, inst_id, Decimal("12"), date(2024, 4, 1))

    allocs = await match_lots_for_sell(session, sell)

    assert len(allocs) == 2
    alloc_by_buy = {a.buy_txn_id: a for a in allocs}
    assert alloc_by_buy[buy1.id].quantity == Decimal("10")
    assert alloc_by_buy[buy2.id].quantity == Decimal("2")
    assert buy3.id not in alloc_by_buy


@pytest.mark.asyncio
async def test_full_sell_fifo(session):
    """Single buy fully consumed by single sell of same quantity."""
    acct_id, inst_id = await _seed_account_instrument(session)
    buy = await _make_buy(session, acct_id, inst_id, Decimal("5"), date(2024, 1, 1))
    sell = await _make_sell(session, acct_id, inst_id, Decimal("5"), date(2024, 2, 1))

    allocs = await match_lots_for_sell(session, sell)

    assert len(allocs) == 1
    assert allocs[0].buy_txn_id == buy.id
    assert allocs[0].quantity == Decimal("5")


@pytest.mark.asyncio
async def test_sell_exceeds_open_qty(session):
    """Sell quantity exceeds total available lots — must raise ValueError."""
    acct_id, inst_id = await _seed_account_instrument(session)
    await _make_buy(session, acct_id, inst_id, Decimal("3"), date(2024, 1, 1))
    sell = await _make_sell(session, acct_id, inst_id, Decimal("5"), date(2024, 2, 1))

    with pytest.raises(ValueError, match="exceeds available lots"):
        await match_lots_for_sell(session, sell)


@pytest.mark.asyncio
async def test_decimal_precision(session):
    """All lot_alloc quantities must be Decimal, not float."""
    acct_id, inst_id = await _seed_account_instrument(session)
    await _make_buy(session, acct_id, inst_id, Decimal("10"), date(2024, 1, 1))
    sell = await _make_sell(session, acct_id, inst_id, Decimal("3"), date(2024, 2, 1))

    allocs = await match_lots_for_sell(session, sell)

    for alloc in allocs:
        assert isinstance(alloc.quantity, Decimal), (
            f"quantity must be Decimal, got {type(alloc.quantity)}"
        )


@pytest.mark.asyncio
async def test_multiple_sells_fifo(session):
    """
    2 buys (10, 10 units).
    sell1=8: consumes 8 from buy1.
    sell2=12: consumes 2 remaining from buy1, 10 from buy2.
    """
    acct_id, inst_id = await _seed_account_instrument(session)
    buy1 = await _make_buy(session, acct_id, inst_id, Decimal("10"), date(2024, 1, 1))
    buy2 = await _make_buy(session, acct_id, inst_id, Decimal("10"), date(2024, 2, 1))
    sell1 = await _make_sell(session, acct_id, inst_id, Decimal("8"), date(2024, 3, 1))
    allocs1 = await match_lots_for_sell(session, sell1)
    await session.flush()

    sell2 = await _make_sell(session, acct_id, inst_id, Decimal("12"), date(2024, 4, 1))
    allocs2 = await match_lots_for_sell(session, sell2)

    assert len(allocs1) == 1
    assert allocs1[0].buy_txn_id == buy1.id
    assert allocs1[0].quantity == Decimal("8")

    assert len(allocs2) == 2
    by_buy = {a.buy_txn_id: a for a in allocs2}
    assert by_buy[buy1.id].quantity == Decimal("2")
    assert by_buy[buy2.id].quantity == Decimal("10")


@pytest.mark.asyncio
async def test_lot_isolation_across_instruments(session):
    """
    Two different instruments in the same account must never cross-contaminate FIFO lots.
    Sell on instrument A must not consume buy lots from instrument B.
    """
    acct = Account(name="Broker", account_type="broker", currency="EUR")
    inst_a = Instrument(
        symbol="BTC", name="Bitcoin", instrument_type="crypto",
        base_currency="USD", price_source="coingecko",
    )
    inst_b = Instrument(
        symbol="ETH", name="Ethereum", instrument_type="crypto",
        base_currency="USD", price_source="coingecko",
    )
    session.add_all([acct, inst_a, inst_b])
    await session.flush()

    # Buy 10 BTC and 10 ETH
    buy_btc = Transaction(
        account_id=acct.id, instrument_id=inst_a.id, txn_type="buy",
        date=date(2024, 1, 1), quantity=Decimal("10"),
        unit_price=Decimal("50000"), price_currency="USD", fx_rate_to_eur=Decimal("1.1"),
        cost_basis_eur=Decimal("454545.45"),
    )
    buy_eth = Transaction(
        account_id=acct.id, instrument_id=inst_b.id, txn_type="buy",
        date=date(2024, 1, 1), quantity=Decimal("10"),
        unit_price=Decimal("2000"), price_currency="USD", fx_rate_to_eur=Decimal("1.1"),
        cost_basis_eur=Decimal("18181.82"),
    )
    session.add_all([buy_btc, buy_eth])
    await session.flush()

    # Sell 5 BTC — should only consume BTC lots, NOT ETH lots
    sell_btc = Transaction(
        account_id=acct.id, instrument_id=inst_a.id, txn_type="sell",
        date=date(2024, 6, 1), quantity=Decimal("-5"),
        unit_price=Decimal("60000"), price_currency="USD", fx_rate_to_eur=Decimal("1.05"),
    )
    session.add(sell_btc)
    await session.flush()

    allocs = await match_lots_for_sell(session, sell_btc)

    assert len(allocs) == 1, "Only 1 alloc row expected (from BTC buy)"
    assert allocs[0].buy_txn_id == buy_btc.id, "Alloc must reference BTC buy, not ETH buy"
    assert allocs[0].quantity == Decimal("5")
