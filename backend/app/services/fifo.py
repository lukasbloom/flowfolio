from collections import defaultdict
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lot_alloc import LotAlloc
from app.models.transaction import Transaction


async def match_lots_for_sell(
    session: AsyncSession,
    sell_txn: Transaction,
) -> list[LotAlloc]:
    """
    FIFO lot matching executed at sell-write time.

    Must be called INSIDE an open DB transaction (caller is responsible for commit).
    Raises ValueError if sell quantity exceeds available open lots.

    Returns the list of LotAlloc rows that were created and added to the session.
    """
    sell_qty = abs(sell_txn.quantity)  # sell quantity is stored negative; work with positive

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
        realized_gain_eur: Decimal | None = None
        if (
            sell_txn.unit_price is not None
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
