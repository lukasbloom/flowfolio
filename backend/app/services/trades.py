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

    return sell_txn, buy_txn
