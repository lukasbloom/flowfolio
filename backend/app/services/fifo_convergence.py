"""Shared FIFO convergence trigger for the mutation paths (plan 015).

A single lot-affecting mutation (a new buy/spend, a reconciliation insert, a
linked-trade leg) can move lot attribution across every disposal on the pair
that competes for the same lots, but only when such a competing disposal
exists. This module holds the shared existence check so the create path
(routers/transactions.py) and the linked-trade path (services/trades.py)
trigger the canonical full-pair recompute identically.
"""
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import DISPOSAL_TXN_TYPES
from app.models.transaction import Transaction
from app.services.fifo import recompute_fifo_for_pair


async def recompute_pair_if_competing_disposal(
    session: AsyncSession,
    account_id: str,
    instrument_id: str,
    exclude_txn_id: str,
) -> None:
    """Run the canonical full-pair FIFO recompute when at least one OTHER
    non-deleted disposal (sell/spend) or downward adjustment exists on the pair.

    exclude_txn_id is the just-mutated row, excluded from the competing-disposal
    scan (a row does not compete with itself). There is intentionally no date
    bound: match_lots_for_sell places a disposal on any open lot regardless of
    date, so a disposal dated BEFORE the mutated row can still hold (or should
    hold) a lot dated on or after it. Feasibility depends only on total supply
    versus total demand, so this never surfaces a NEW insufficient-lots
    ValueError for a mutation that already succeeded. Propagates ValueError from
    recompute_fifo_for_pair for the caller to map to 422.
    """
    others = await session.execute(
        select(Transaction.txn_type, Transaction.quantity).where(
            Transaction.account_id == account_id,
            Transaction.instrument_id == instrument_id,
            Transaction.id != exclude_txn_id,
            Transaction.deleted_at.is_(None),
            Transaction.txn_type.in_(DISPOSAL_TXN_TYPES | {"adjustment"}),
        )
    )
    has_competing_disposal = any(
        t_type in DISPOSAL_TXN_TYPES or qty < Decimal("0")
        for t_type, qty in others
    )
    if has_competing_disposal:
        await recompute_fifo_for_pair(session, account_id, instrument_id)
