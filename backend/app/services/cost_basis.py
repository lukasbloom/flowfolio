"""Shared cost-basis helpers.

Two flavors of "cost basis" math live here:

1. ``compute_cost_basis`` — original W8 helper (transaction-row-scoped):
   pre-computes ``cost_basis_eur`` at insert/update time from a single
   ``Transaction``'s ``unit_price`` × ``|quantity|`` ÷ ``fx_rate_to_eur``.
   Used by routers/transactions.py and services/trades.py.

2. ``_load_allocations`` + ``_cost_basis_at`` — FIFO helpers
   (portfolio-scoped): compute the EUR cost basis of all still-open buy lots
   as of a given date, lifted verbatim from ``services/contributions.py`` so
   ``services/networth.py`` can layer a daily cost-basis series onto
   ``/api/networth`` without creating a circular import.

Services do not commit or roll back transactions; routers own transaction
boundaries.
"""

from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.constants import ZERO
from app.models import LotAlloc, Transaction


def compute_cost_basis(txn: Transaction) -> Optional[Decimal]:
    """Pre-compute cost_basis_eur at insert time. Returns None for yield transactions."""
    if txn.unit_price is None or txn.fx_rate_to_eur is None:
        return None
    if txn.fx_rate_to_eur == Decimal("0"):
        return None
    qty = abs(txn.quantity)
    return (qty * txn.unit_price / txn.fx_rate_to_eur).quantize(Decimal("0.00000001"))


async def _load_allocations(
    session: AsyncSession, sell_txn_ids: set[str]
) -> list[tuple[LotAlloc, Transaction, Transaction]]:
    """Load FIFO allocations for the given sell/spend transaction ids.

    Returns a list of ``(LotAlloc, buy_txn, sell_txn)`` tuples. Empty input
    short-circuits with an empty list — no DB roundtrip.
    """
    if not sell_txn_ids:
        return []
    buy_txn = aliased(Transaction)
    sell_txn = aliased(Transaction)
    stmt = (
        select(LotAlloc, buy_txn, sell_txn)
        .join(buy_txn, LotAlloc.buy_txn_id == buy_txn.id)
        .join(sell_txn, LotAlloc.sell_txn_id == sell_txn.id)
        .where(LotAlloc.sell_txn_id.in_(sell_txn_ids))
    )
    result = await session.execute(stmt)
    return list(result.all())


def _cost_basis_at(
    buy_txns: list[Transaction],
    allocations: list[tuple[LotAlloc, Transaction, Transaction]],
    on_date: date,
) -> Decimal:
    """Pure FIFO cost-basis-on-date.

    Sums the EUR cost basis of every still-open buy lot as of ``on_date``,
    proportionally reduced by FIFO consumption from sells/spends that
    settled on or before ``on_date``.
    """
    total = ZERO
    for _buy_date, open_eur in _open_lots_at(buy_txns, allocations, on_date):
        total += open_eur
    return total


def _open_lots_at(
    buy_txns: list[Transaction],
    allocations: list[tuple[LotAlloc, Transaction, Transaction]],
    on_date: date,
) -> list[tuple[date, Decimal]]:
    """FIFO open-lot decomposition as of ``on_date``.

    Returns one ``(buy_date, open_cost_basis_eur)`` tuple per still-open buy
    lot — the proportionally-reduced EUR cost basis surviving FIFO
    consumption by sells/spends settled on or before ``on_date``.

    This is the shared primitive behind both ``_cost_basis_at`` (which sums
    the EUR amounts, ignoring the dates) and the display-currency cost-basis
    series in ``contributions``/``networth`` (which convert each lot's EUR
    amount at ITS OWN transaction-date FX rate — "transaction-time FX" — so
    the cost line only steps on transaction days in every display currency,
    rather than drifting daily as FX moves). The FIFO proration is identical
    to the historical ``_cost_basis_at`` logic so EUR output is unchanged.
    """
    consumed_by_buy: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for alloc, _, sell in allocations:
        if sell.date <= on_date:
            consumed_by_buy[alloc.buy_txn_id] += alloc.quantity

    open_lots: list[tuple[date, Decimal]] = []
    for buy in buy_txns:
        if buy.date > on_date or buy.quantity <= ZERO or buy.cost_basis_eur is None:
            continue
        open_qty = buy.quantity - consumed_by_buy[buy.id]
        if open_qty <= ZERO:
            continue
        open_lots.append((buy.date, buy.cost_basis_eur * open_qty / buy.quantity))
    return open_lots
