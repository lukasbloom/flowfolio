"""Atomic linked sell+buy transaction creation.

Caller-commits contract (mirrors backend/app/services/fifo.py):
    Must be called INSIDE an open DB transaction (caller is responsible for
    commit). The service stages two new rows + lot_alloc via session.add(...)
    but never calls session.commit() / session.rollback().
"""
import uuid
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.transaction import Transaction
from app.services.cost_basis import compute_cost_basis
from app.services.fifo import match_lots_for_sell
from app.services.fifo_convergence import recompute_pair_if_competing_disposal
from app.services.fx import get_or_fetch_fx_rate

if TYPE_CHECKING:
    from app.schemas.trade import TradeLeg


class FxUpstreamError(Exception):
    """Raised when an FX rate fetch fails because of an upstream provider
    issue (Frankfurter unreachable, malformed response). Distinct from
    ValueError so the router can map to 502 instead of 422 — mirrors the
    distinction already made in routers/transactions.py."""


async def create_linked_trade(
    session: AsyncSession,
    fx_client: httpx.AsyncClient,
    sold: "TradeLeg",
    received: "TradeLeg",
    trade_date: date,
    notes: str | None = None,
) -> tuple[Transaction, Transaction]:
    """Create an atomic linked sell+buy pair with a shared trade_pair_id.

    Returns (sell_txn, buy_txn). Caller must commit.
    Raises ValueError on FIFO insufficient lots (caller should rollback + return 422).
    """
    pair_id = str(uuid.uuid4())

    # Build sell row (signed negative)
    sell_txn = Transaction(
        account_id=sold.account_id,
        instrument_id=sold.instrument_id,
        txn_type="sell",
        date=trade_date,
        quantity=-abs(sold.quantity),
        unit_price=sold.unit_price,
        price_currency=sold.price_currency,
        fx_rate_to_eur=sold.fx_rate_to_eur,
        fee_eur=sold.fee_eur or Decimal("0"),
        notes=notes,
        trade_pair_id=pair_id,
    )
    # Build buy row (positive)
    buy_txn = Transaction(
        account_id=received.account_id,
        instrument_id=received.instrument_id,
        txn_type="buy",
        date=trade_date,
        quantity=abs(received.quantity),
        unit_price=received.unit_price,
        price_currency=received.price_currency,
        fx_rate_to_eur=received.fx_rate_to_eur,
        fee_eur=received.fee_eur or Decimal("0"),
        notes=notes,
        trade_pair_id=pair_id,
    )

    # FX auto-fetch per leg if USD and not provided (mirror routers/transactions.py pattern)
    for txn in (sell_txn, buy_txn):
        if txn.price_currency == "USD" and txn.fx_rate_to_eur is None:
            try:
                fx_row = await get_or_fetch_fx_rate(session, fx_client, txn.date, base="EUR", quote="USD")
            except ValueError as exc:
                # Upstream FX provider failure — surface as 502, not 422.
                raise FxUpstreamError(str(exc)) from exc
            txn.fx_rate_to_eur = fx_row.rate
        elif txn.price_currency == "EUR":
            txn.fx_rate_to_eur = Decimal("1")

    # Cost basis locked at insert time (shared module — W8 revision iter 1)
    sell_txn.cost_basis_eur = compute_cost_basis(sell_txn)
    buy_txn.cost_basis_eur = compute_cost_basis(buy_txn)

    session.add(sell_txn)
    session.add(buy_txn)
    await session.flush()  # populate IDs before FIFO

    # FIFO on the sell leg only (received leg is a brand-new lot)
    await match_lots_for_sell(session, sell_txn)
    await session.flush()

    # Converge BOTH pairs to canonical FIFO (plan 015). POST /api/trades is the
    # only entry path for sells, so it must recompute like the create path does:
    # a back-dated sell leg can land on the wrong lot when an existing disposal
    # holds an earlier one, and a back-dated received leg can precede lots that
    # existing disposals of that instrument already consumed. Each recompute
    # runs only when a competing disposal exists and raises ValueError (caller
    # maps to 422 and rolls back) exactly like the sell-leg match above.
    await recompute_pair_if_competing_disposal(
        session, sell_txn.account_id, sell_txn.instrument_id, sell_txn.id
    )
    await recompute_pair_if_competing_disposal(
        session, buy_txn.account_id, buy_txn.instrument_id, buy_txn.id
    )

    return sell_txn, buy_txn
