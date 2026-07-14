from collections import defaultdict
from decimal import Decimal

from sqlalchemy import delete as sql_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import DISPOSAL_TXN_TYPES
from app.models.lot_alloc import LotAlloc
from app.models.transaction import Transaction


async def match_lots_for_sell(
    session: AsyncSession,
    sell_txn: Transaction,
) -> list[LotAlloc]:
    """
    FIFO lot matching for a disposal-like txn (sell, spend, or a DOWNWARD
    adjustment), executed at write time.

    Must be called INSIDE an open DB transaction (caller is responsible for commit).
    A negative adjustment consumes open lots like a sell, but it is a correction
    and not a taxable disposal, so its allocs carry realized_gain_eur=None.
    Raises ValueError if the disposal quantity exceeds available open lots.

    Returns the list of LotAlloc rows that were created and added to the session.
    """
    # sell/spend and downward adjustments are all stored negative. abs() gives
    # the positive amount to consume.
    sell_qty = abs(sell_txn.quantity)
    is_adjustment = sell_txn.txn_type == "adjustment"

    # Fetch all buy transactions for this (account, instrument) pair, ordered FIFO (date ASC).
    # quantity is TEXT-backed (DecimalText) — the qty>0 sign filter moves to Python
    # below; a SQL comparison would type-juggle the text against an integer.
    stmt = (
        select(Transaction)
        .where(
            Transaction.account_id == sell_txn.account_id,
            Transaction.instrument_id == sell_txn.instrument_id,
            # Positive adjustments are buy-lot equivalents (system-only top-ups).
            Transaction.txn_type.in_(("buy", "adjustment")),
            Transaction.deleted_at.is_(None),
        )
        .order_by(Transaction.date.asc(), Transaction.created_at.asc())
    )
    result = await session.execute(stmt)
    buy_txns = [b for b in result.scalars().all() if b.quantity > Decimal("0")]

    # For each buy, compute how much is already consumed by prior sells.
    # IMPORTANT: scope to buy_txn_ids belonging to THIS (account, instrument) pair only.
    # A global query would return alloc rows from OTHER instruments and contaminate quantities.
    buy_txn_ids = [b.id for b in buy_txns]
    consumed_by_lot: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    if buy_txn_ids:
        # LotAlloc.quantity is TEXT-backed — sum in Python, not SQL (a SQL SUM
        # would coerce the text back to float and reintroduce the dust bug).
        alloc_stmt = select(LotAlloc.buy_txn_id, LotAlloc.quantity).where(
            LotAlloc.buy_txn_id.in_(buy_txn_ids)
        )
        alloc_result = await session.execute(alloc_stmt)
        for buy_txn_id, qty in alloc_result:
            consumed_by_lot[buy_txn_id] += qty

    remaining_to_match = sell_qty
    allocs: list[LotAlloc] = []

    for buy in buy_txns:
        if remaining_to_match <= Decimal("0"):
            break

        already_consumed = consumed_by_lot.get(buy.id, Decimal("0"))
        available = buy.quantity - already_consumed  # buy.quantity is positive

        if available <= Decimal("0"):
            continue

        matched_qty = min(remaining_to_match, available)

        # Compute realized gain in EUR
        # (sell_unit_price / sell_fx) - (buy_unit_price / buy_fx)) * matched_qty
        # A downward adjustment is a correction, not a taxable disposal, so it
        # never contributes a realized gain (realized_gain_eur=None).
        realized_gain_eur: Decimal | None = None
        if (
            not is_adjustment
            and sell_txn.unit_price is not None
            and sell_txn.fx_rate_to_eur is not None
            and buy.unit_price is not None
            and buy.fx_rate_to_eur is not None
            and buy.fx_rate_to_eur != Decimal("0")
            and sell_txn.fx_rate_to_eur != Decimal("0")
        ):
            sell_price_eur = sell_txn.unit_price / sell_txn.fx_rate_to_eur
            buy_price_eur = buy.unit_price / buy.fx_rate_to_eur
            # Quantize to the realized_gain_eur column's intended 8-dp scale.
            # With TEXT storage the full 28-digit decimal-context result would
            # otherwise persist verbatim (Numeric(20,8) used to truncate it).
            realized_gain_eur = (
                (sell_price_eur - buy_price_eur) * matched_qty
            ).quantize(Decimal("0.00000001"))

        alloc = LotAlloc(
            sell_txn_id=sell_txn.id,
            buy_txn_id=buy.id,
            quantity=matched_qty,
            realized_gain_eur=realized_gain_eur,
        )
        session.add(alloc)
        allocs.append(alloc)
        remaining_to_match -= matched_qty

    if remaining_to_match > Decimal("0"):
        raise ValueError(
            f"Sell quantity {sell_qty} exceeds available lots by {remaining_to_match}"
        )

    return allocs


async def delete_lot_allocs_for_sell(session: AsyncSession, sell_txn_id: str) -> None:
    """Delete a sell/spend's LotAlloc rows (re-opens the buy lots it consumed)."""
    await session.execute(
        sql_delete(LotAlloc).where(LotAlloc.sell_txn_id == sell_txn_id)
    )


async def recompute_fifo_for_pair(
    session: AsyncSession,
    account_id: str,
    instrument_id: str,
) -> None:
    """Re-run FIFO for every disposal on this (account, instrument) pair, in
    FIFO order. A disposal here is a sell, a spend, or a DOWNWARD (negative)
    adjustment. A negative reconciliation trim consumes open lots exactly like
    a sell. The rematch is always pair-wide: a disposal can consume a buy dated
    after it, so per-disposal rematching is order-sensitive.

    Deletes the lot allocs of ALL selected disposals first (one flush), then
    rematches each in FIFO order. Rematching one at a time would let a
    not-yet-rematched later disposal's stale allocations still count as
    consumption, giving non-FIFO lot attribution and wrong per-disposal realized
    gains. Raises ValueError if a disposal can no longer be covered by open lots.
    """
    stmt = (
        select(Transaction)
        .where(
            Transaction.account_id == account_id,
            Transaction.instrument_id == instrument_id,
            # sell/spend plus adjustments. The downward-only sign filter for
            # adjustments moves to Python (quantity is TEXT-backed, so a SQL
            # comparison would coerce the text against a number). Positive
            # adjustments are lot sources, not disposals, and are dropped below.
            Transaction.txn_type.in_(DISPOSAL_TXN_TYPES | {"adjustment"}),
            Transaction.deleted_at.is_(None),
        )
        .order_by(Transaction.date.asc(), Transaction.created_at.asc())
    )
    result = await session.execute(stmt)
    disposals = [
        txn
        for txn in result.scalars().all()
        if txn.txn_type in DISPOSAL_TXN_TYPES or txn.quantity < Decimal("0")
    ]
    # Clear every disposal's allocs up front so availability at each rematch
    # step reflects only what earlier sells (already rematched in this pass)
    # have consumed, never a later sell's stale rows.
    for sell_txn in disposals:
        await delete_lot_allocs_for_sell(session, sell_txn.id)
    await session.flush()
    for sell_txn in disposals:
        await match_lots_for_sell(session, sell_txn)
        await session.flush()
