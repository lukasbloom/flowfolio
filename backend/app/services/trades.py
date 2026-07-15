"""Atomic linked sell+buy transaction creation.

Caller-commits contract (mirrors backend/app/services/fifo.py):
    Must be called INSIDE an open DB transaction (caller is responsible for
    commit). The service stages two new rows + lot_alloc via session.add(...)
    but never calls session.commit() / session.rollback().
"""
import uuid
from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import ZERO
from app.core.enums import signed_quantity
from app.models.transaction import Transaction
from app.services.cost_basis import compute_cost_basis
from app.services.fifo import recompute_fifo_for_pair
from app.services.fx import resolve_locked_fx_rate

if TYPE_CHECKING:
    from app.schemas.trade import TradeLeg


async def create_linked_trade(
    session: AsyncSession,
    sold: "TradeLeg",
    received: "TradeLeg",
    trade_date: date,
    notes: str | None = None,
) -> tuple[Transaction, Transaction]:
    """Create an atomic linked sell+buy pair with a shared trade_pair_id.

    Returns (sell_txn, buy_txn). Caller must commit.
    Raises ValueError on FIFO insufficient lots (caller should rollback + return
    422) and FxUpstreamError on an FX provider failure (caller maps to 502).
    """
    pair_id = str(uuid.uuid4())

    sell_txn = Transaction(
        account_id=sold.account_id,
        instrument_id=sold.instrument_id,
        txn_type="sell",
        date=trade_date,
        quantity=signed_quantity("sell", sold.quantity),
        unit_price=sold.unit_price,
        price_currency=sold.price_currency,
        fx_rate_to_eur=sold.fx_rate_to_eur,
        fee_eur=sold.fee_eur or ZERO,
        notes=notes,
        trade_pair_id=pair_id,
    )
    buy_txn = Transaction(
        account_id=received.account_id,
        instrument_id=received.instrument_id,
        txn_type="buy",
        date=trade_date,
        quantity=signed_quantity("buy", received.quantity),
        unit_price=received.unit_price,
        price_currency=received.price_currency,
        fx_rate_to_eur=received.fx_rate_to_eur,
        fee_eur=received.fee_eur or ZERO,
        notes=notes,
        trade_pair_id=pair_id,
    )

    for txn in (sell_txn, buy_txn):
        txn.fx_rate_to_eur = await resolve_locked_fx_rate(
            session, txn.price_currency, txn.date, txn.fx_rate_to_eur
        )
        txn.cost_basis_eur = compute_cost_basis(txn)

    session.add(sell_txn)
    session.add(buy_txn)
    await session.flush()  # populate IDs before FIFO

    # Converge both pairs to canonical FIFO. The sell leg's recompute matches
    # the leg itself and re-attributes any disposal it competes with; the
    # received leg's recompute re-attributes existing disposals onto the new
    # lot when it is back-dated. Insufficient lots raise ValueError (caller
    # maps to 422 and rolls back).
    await recompute_fifo_for_pair(
        session, sell_txn.account_id, sell_txn.instrument_id
    )
    await recompute_fifo_for_pair(
        session, buy_txn.account_id, buy_txn.instrument_id
    )

    return sell_txn, buy_txn
