"""Downward adjustments consume FIFO open lots (plan 008).

A negative reconciliation adjustment trims a holding. These tests pin the FIXED
behavior: the trim matches open lots like a sell (creating LotAlloc rows with
realized_gain_eur=None), so the open-lot decomposition and sell availability
both reflect the reduced balance, and realized totals never move because of a
correction.
"""
from datetime import date, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core import clock
from app.core.database import Base
from app.models import Account, Instrument, LotAlloc, Transaction
from app.schemas.reconciliation import (
    DriftDecision,
    HoldingSnapshotEntry,
    ReconciliationCreate,
    RejectedTxnPayload,
)
from app.services.contributions import get_cost_basis_series
from app.services.cost_basis import _load_allocations, _open_lots_at
from app.services.fifo import match_lots_for_sell, recompute_fifo_for_pair
from app.services.networth import get_networth_series
from app.services.realized import get_realized_per_holding
from app.services.reconciliation import save_event


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


@pytest.mark.asyncio
async def test_cost_basis_series_reflects_trim(session):
    """The contributions cost-basis SERIES must feed the negative adjustment's
    allocs into the open-lot decomposition, exactly as the per-holding path does.
    Basis is 1000 before the trim date and 700 on/after it, not the phantom 1000
    that survives when adjustment allocs never reach the series loader."""
    acct_id, inst_id = await _seed_account_instrument(session)
    await _make_buy(session, acct_id, inst_id, Decimal("100"), date(2024, 1, 1))
    await _make_adjustment(session, acct_id, inst_id, Decimal("-30"), date(2024, 2, 1))
    await recompute_fifo_for_pair(session, acct_id, inst_id)

    cost_basis_points, _ = await get_cost_basis_series(session)
    by_date = {point.date: point.value for point in cost_basis_points}

    assert by_date[date(2024, 1, 15)] == Decimal("1000")  # pre-trim
    assert by_date[date(2024, 2, 1)] == Decimal("700")  # on the trim date
    assert by_date[date(2024, 2, 15)] == Decimal("700")  # post-trim


@pytest.mark.asyncio
async def test_networth_cost_basis_series_reflects_trim(session):
    """The networth cost-basis series loader must also feed adjustment allocs
    into the open-lot decomposition. Recent dates plus a 3-month window give a
    daily series so the pre/post-trim basis is checkable by exact date."""
    acct_id, inst_id = await _seed_account_instrument(session)
    today = clock.today()
    buy_date = today - timedelta(days=40)
    trim_date = today - timedelta(days=20)
    await _make_buy(session, acct_id, inst_id, Decimal("100"), buy_date)
    await _make_adjustment(session, acct_id, inst_id, Decimal("-30"), trim_date)
    await recompute_fifo_for_pair(session, acct_id, inst_id)

    series = await get_networth_series(session, "3m", "EUR", include_cost_basis=True)
    by_date = {point.date: point.value for point in series.cost_basis_series}

    assert by_date[today - timedelta(days=30)] == Decimal("1000")  # pre-trim
    assert by_date[trim_date] == Decimal("700")  # on the trim date
    assert by_date[today - timedelta(days=10)] == Decimal("700")  # post-trim


async def _realized_for_instrument(session: AsyncSession, instrument_id: str) -> Decimal:
    rows = await get_realized_per_holding(session, "EUR")
    for row in rows:
        if row.instrument_id == instrument_id:
            return row.realized_eur
    return Decimal("0")


# ---------------------------------------------------------------------------
# Plan 015: reconciliation lot insertions must recompute the WHOLE pair, not a
# date-scoped subset. The old after_date=snapshot_date scope skipped earlier
# disposals holding a lot dated on or after the new row, and the reject-buy path
# never recomputed at all.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_accept_topup_reattributes_earlier_sell_full_pair(session):
    """Repro 4a: buy B(50)@20 on 2024-06-01, sell S(50)@30 on 2024-05-01 (S draws
    from B, the only lot). An accept top-up writes a +50 adjustment on 2024-05-15,
    a lot dated BEFORE B but AFTER the sell. Canonical FIFO moves the sell onto the
    earlier adjustment lot (no price -> realized gain None). The old
    snapshot_date-scoped recompute skipped the sell (dated before the snapshot)
    and left it on B."""
    acct_id, inst_id = await _seed_account_instrument(session)
    buy_b = await _make_buy(
        session, acct_id, inst_id, Decimal("50"), date(2024, 6, 1), unit_price=Decimal("20")
    )
    sell = await _make_sell(
        session, acct_id, inst_id, Decimal("50"), date(2024, 5, 1), unit_price=Decimal("30")
    )
    await match_lots_for_sell(session, sell)
    await session.flush()

    # Precondition: the sell draws entirely from B.
    pre = (
        await session.execute(select(LotAlloc).where(LotAlloc.sell_txn_id == sell.id))
    ).scalars().all()
    assert {a.buy_txn_id for a in pre} == {buy_b.id}

    payload = ReconciliationCreate(
        account_id=acct_id,
        snapshot_date=date(2024, 5, 15),
        notes=None,
        holdings=[HoldingSnapshotEntry(instrument_id=inst_id, snapshot_qty=Decimal("50"))],
        decisions=[DriftDecision(instrument_id=inst_id, action="accept")],
    )
    await save_event(session, payload)

    adj = (
        await session.execute(
            select(Transaction).where(
                Transaction.instrument_id == inst_id,
                Transaction.txn_type == "adjustment",
            )
        )
    ).scalar_one()
    assert adj.quantity == Decimal("50")  # snapshot 50 - app 0 = +50 top-up

    post = (
        await session.execute(select(LotAlloc).where(LotAlloc.sell_txn_id == sell.id))
    ).scalars().all()
    assert len(post) == 1
    assert post[0].buy_txn_id == adj.id
    assert post[0].quantity == Decimal("50")
    # The adjustment lot carries no price, so the sell's alloc has no realized gain.
    assert post[0].realized_gain_eur is None


@pytest.mark.asyncio
async def test_reject_buy_reattributes_existing_sell_full_pair(session):
    """Repro 4b: buy A(100)@10 on 2024-05-01, sell S(100)@30 on 2024-05-10 (->A,
    gain 2000). A reject-buy records a forgotten back-dated lot new(50)@20 on
    2024-01-01 (drift = snapshot 50 - app 0). Canonical FIFO splits the sell:
    new(50) gain 500 + A(50) gain 1000 = 1500. The reject-buy path used to insert
    the lot with no recompute, leaving the sell fully on A at gain 2000."""
    acct_id, inst_id = await _seed_account_instrument(session)
    buy_a = await _make_buy(
        session, acct_id, inst_id, Decimal("100"), date(2024, 5, 1), unit_price=Decimal("10")
    )
    sell = await _make_sell(
        session, acct_id, inst_id, Decimal("100"), date(2024, 5, 10), unit_price=Decimal("30")
    )
    await match_lots_for_sell(session, sell)
    await session.flush()

    # Precondition: the sell draws entirely from A (gain 2000).
    pre = (
        await session.execute(select(LotAlloc).where(LotAlloc.sell_txn_id == sell.id))
    ).scalars().all()
    assert {a.buy_txn_id for a in pre} == {buy_a.id}
    assert sum(a.quantity for a in pre) == Decimal("100")

    payload = ReconciliationCreate(
        account_id=acct_id,
        snapshot_date=date(2024, 6, 1),
        notes=None,
        holdings=[HoldingSnapshotEntry(instrument_id=inst_id, snapshot_qty=Decimal("50"))],
        decisions=[DriftDecision(instrument_id=inst_id, action="reject")],
        rejected_txns=[
            RejectedTxnPayload(
                instrument_id=inst_id,
                txn_type="buy",
                txn_date=date(2024, 1, 1),
                unit_price=Decimal("20"),
                price_currency="EUR",
                fee_eur=Decimal("0"),
            )
        ],
    )
    await save_event(session, payload)

    new_buy = (
        await session.execute(
            select(Transaction).where(
                Transaction.instrument_id == inst_id,
                Transaction.txn_type == "buy",
                Transaction.date == date(2024, 1, 1),
            )
        )
    ).scalar_one()
    assert new_buy.quantity == Decimal("50")  # abs(snapshot 50 - app 0)

    post = {
        a.buy_txn_id: a
        for a in (
            await session.execute(
                select(LotAlloc).where(LotAlloc.sell_txn_id == sell.id)
            )
        ).scalars().all()
    }
    assert set(post) == {new_buy.id, buy_a.id}
    assert post[new_buy.id].quantity == Decimal("50")
    assert post[new_buy.id].realized_gain_eur == Decimal("500")  # (30 - 20) * 50
    assert post[buy_a.id].quantity == Decimal("50")
    assert post[buy_a.id].realized_gain_eur == Decimal("1000")  # (30 - 10) * 50
