"""Closed positions read model.

Services do not commit or roll back transactions; routers own transaction
boundaries.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.constants import ZERO
from app.models import LotAlloc, Transaction
from app.schemas.closed import ClosedPositionRow
from app.services.market_data import MarketDataSnapshot, load_market_data
from app.services.perf import calculate_twrr
from app.services.quotes import latest_quote as _latest_quote
from app.services.quotes import load_holdings


async def get_closed_positions(
    session: AsyncSession,
    display_currency: str = "EUR",
    tag_filter: str | None = None,
) -> list[ClosedPositionRow]:
    holdings, accounts_by_id, instruments_by_id = await load_holdings(session, tag_filter)

    # Build the market-data snapshot once for FX. NOTE: closed
    # converts avg_cost/last_close/realized at each row's `last_close_date` (NOT a
    # single as_of), so it consumes the snapshot's PER-DATE convert(amount, ...,
    # as_of=last_close_date). Build the FX-by-date map up to today so every
    # last_close_date (always <= today) resolves. The quote lookups stay as
    # per-row latest_quote DB calls because they too are date-specific
    # (last_close_date) — the single-as_of snapshot quote would resolve the wrong
    # date.
    snapshot = await load_market_data(
        session,
        as_of=clock.today(),
        instrument_ids={instrument_id for _, instrument_id in holdings},
    )

    # Compute the closed set UP FRONT with one grouped
    # query, then batch the per-holding disposal/cost/realized aggregates across
    # only the closed holdings — instead of calling _open_quantity per holding and
    # discarding the non-closed ones, plus N more queries each.
    closed_set = await _closed_holding_set(session)
    last_disposal_by_holding = await _last_disposal_dates_batch(session, closed_set)
    buy_basis_by_holding, buy_qty_by_holding = await _buy_basis_and_qty_batch(
        session, closed_set
    )
    realized_by_holding = await _realized_gains_batch(session, closed_set)
    first_buy_by_holding = await _first_buy_dates_batch(session, closed_set)

    rows: list[ClosedPositionRow] = []
    for account_id, instrument_id in holdings:
        # Closed set membership replaces the per-holding _open_quantity != ZERO
        # discard — same predicate (coalesce(sum(quantity),ZERO) == ZERO).
        if (account_id, instrument_id) not in closed_set:
            continue

        last_close_date = last_disposal_by_holding.get((account_id, instrument_id))
        first_buy_date = first_buy_by_holding.get((account_id, instrument_id))
        account = accounts_by_id.get(account_id)
        instrument = instruments_by_id.get(instrument_id)
        if account is None or instrument is None or last_close_date is None:
            continue

        avg_cost = _average_buy_cost_from_parts(
            buy_basis_by_holding.get((account_id, instrument_id), ZERO),
            buy_qty_by_holding.get((account_id, instrument_id), ZERO),
            display_currency,
            last_close_date,
            snapshot,
        )
        last_close = await _last_close_price(
            session,
            instrument_id,
            display_currency,
            last_close_date,
            snapshot,
        )
        realized_eur_raw = realized_by_holding.get((account_id, instrument_id), ZERO)
        total_basis_eur = buy_basis_by_holding.get((account_id, instrument_id), ZERO)
        # percent_return is a ratio — currency-invariant — so compute it from
        # the EUR raw before converting realized for display.
        percent_return = None
        if realized_eur_raw is not None and total_basis_eur > ZERO:
            percent_return = realized_eur_raw / total_basis_eur
        # The Closed Positions table renders the Realized column under the
        # user-selected currency badge; convert to display_currency to match
        # the convention used by services/realized.py (otherwise USD viewers
        # see EUR magnitudes labelled "$"). Convert via the
        # snapshot's per-date FX at last_close_date (was _convert_currency).
        realized_eur = (
            snapshot.convert(
                realized_eur_raw, "EUR", display_currency, as_of=last_close_date
            )
            if realized_eur_raw is not None
            else None
        )

        twrr = await calculate_twrr(
            session,
            account_id,
            instrument_id,
            first_buy_date,
            last_close_date,
            first_buy=first_buy_date,
        )
        rows.append(
            ClosedPositionRow(
                account_id=account.id,
                account_name=account.name,
                instrument_id=instrument.id,
                instrument_symbol=instrument.symbol,
                instrument_name=instrument.name,
                instrument_type=instrument.instrument_type,
                display_decimals=instrument.display_decimals,
                quantity=ZERO,
                avg_cost=avg_cost,
                last_close=last_close,
                last_close_date=last_close_date,
                percent_return=percent_return,
                realized_eur=realized_eur,
                twrr=twrr.twrr,
                twrr_window_days=(
                    (last_close_date - first_buy_date).days
                    if first_buy_date is not None
                    else 0
                ),
                twrr_annualized=twrr.twrr_annualized,
            )
        )

    return sorted(
        rows,
        key=lambda row: (row.twrr is not None, row.twrr or ZERO),
        reverse=True,
    )


async def _closed_holding_set(session: AsyncSession) -> set[tuple[str, str]]:
    """The set of CLOSED (account, instrument) holdings, computed in one query.

    closed-set = `_open_quantity`'s predicate grouped by (account_id,
    instrument_id) HAVING coalesce(sum(quantity), ZERO) == ZERO. This is exactly
    _open_quantity's WHERE (deleted_at IS NULL, coalesce-sum) lifted to a GROUP
    BY over (account_id, instrument_id) with the CLOSED (== ZERO) HAVING.

    Intentionally NOT reconciliation.build_preview: that groups by instrument_id
    ONLY (single account fixed in WHERE), wants OPEN holdings (HAVING sum !=
    ZERO — the opposite predicate), and filters one account. Conflating the two
    would break correctness on every axis.
    """
    # quantity is TEXT-backed (DecimalText) — sum per (account, instrument) in
    # Python and apply the closed (== ZERO) predicate there. A SQL SUM/HAVING
    # would do float arithmetic on the text values and the float dust could
    # make a fully-closed position look open (see plan 006).
    stmt = (
        select(
            Transaction.account_id,
            Transaction.instrument_id,
            Transaction.quantity,
        )
        .where(Transaction.deleted_at.is_(None))
    )
    result = await session.execute(stmt)
    totals: dict[tuple[str, str], Decimal] = {}
    for account_id, instrument_id, qty in result:
        key = (account_id, instrument_id)
        totals[key] = totals.get(key, ZERO) + qty
    return {key for key, total in totals.items() if total == ZERO}


async def _last_disposal_dates_batch(
    session: AsyncSession, closed_set: set[tuple[str, str]]
) -> dict[tuple[str, str], date]:
    """Batched last-disposal date per (account, instrument), restricted to the
    closed set. Same predicate as _last_disposal_date: max(date) WHERE txn_type
    in (sell, spend), deleted_at IS NULL, grouped per holding.
    """
    if not closed_set:
        return {}
    stmt = (
        select(
            Transaction.account_id,
            Transaction.instrument_id,
            func.max(Transaction.date).label("last_date"),
        )
        .where(
            Transaction.txn_type.in_(("sell", "spend")),
            Transaction.deleted_at.is_(None),
        )
        .group_by(Transaction.account_id, Transaction.instrument_id)
    )
    result = await session.execute(stmt)
    return {
        (row.account_id, row.instrument_id): row.last_date
        for row in result
        if (row.account_id, row.instrument_id) in closed_set and row.last_date is not None
    }


async def _first_buy_dates_batch(
    session: AsyncSession, closed_set: set[tuple[str, str]]
) -> dict[tuple[str, str], date]:
    """Batched first-buy date per (account, instrument), restricted to the
    closed set. Same predicate as first_buy_date (app.services.quotes):
    min(date) WHERE txn_type in (buy, adjustment), quantity>0, deleted_at IS
    NULL, grouped per holding. A holding with no buy/adjustment rows is absent
    from the dict — matching first_buy_date's scalar_one_or_none() -> None.
    """
    if not closed_set:
        return {}
    # quantity is TEXT-backed (DecimalText): the qty>0 sign filter moves to
    # Python; min(date) is then reduced per holding. Matches first_buy_date.
    stmt = (
        select(
            Transaction.account_id,
            Transaction.instrument_id,
            Transaction.date,
            Transaction.quantity,
        )
        .where(
            Transaction.txn_type.in_(("buy", "adjustment")),
            Transaction.deleted_at.is_(None),
        )
    )
    result = await session.execute(stmt)
    first_by: dict[tuple[str, str], date] = {}
    for account_id, instrument_id, txn_date, qty in result:
        if qty <= ZERO:
            continue
        key = (account_id, instrument_id)
        if key not in closed_set:
            continue
        if key not in first_by or txn_date < first_by[key]:
            first_by[key] = txn_date
    return first_by


async def _buy_basis_and_qty_batch(
    session: AsyncSession, closed_set: set[tuple[str, str]]
) -> tuple[dict[tuple[str, str], Decimal], dict[tuple[str, str], Decimal]]:
    """Batched (total buy basis EUR, total buy quantity) per holding.

    basis: coalesce(sum(cost_basis_eur), ZERO) — same as _total_buy_basis_eur
    (adjustments carry cost_basis_eur=NULL → coalesce contributes 0).
    qty:   coalesce(sum(quantity), ZERO) — same as the _average_buy_cost quantity
    query. Both share the identical buy-side predicate: txn_type in
    (buy, adjustment), quantity>0, deleted_at IS NULL.
    """
    if not closed_set:
        return {}, {}
    # cost_basis_eur / quantity are TEXT-backed (DecimalText) — sum in Python.
    # The qty>0 sign filter moves to Python; cost_basis_eur=NULL (adjustments)
    # contributes 0, matching the old coalesce semantics. A SQL SUM/HAVING
    # would coerce the text values to float (see plan 006).
    stmt = (
        select(
            Transaction.account_id,
            Transaction.instrument_id,
            Transaction.cost_basis_eur,
            Transaction.quantity,
        )
        .where(
            Transaction.txn_type.in_(("buy", "adjustment")),
            Transaction.deleted_at.is_(None),
        )
    )
    result = await session.execute(stmt)
    basis_by: dict[tuple[str, str], Decimal] = {}
    qty_by: dict[tuple[str, str], Decimal] = {}
    for account_id, instrument_id, cost_basis_eur, qty in result:
        if qty <= ZERO:
            continue
        key = (account_id, instrument_id)
        if key not in closed_set:
            continue
        basis_by[key] = basis_by.get(key, ZERO) + (cost_basis_eur or ZERO)
        qty_by[key] = qty_by.get(key, ZERO) + qty
    return basis_by, qty_by


async def _realized_gains_batch(
    session: AsyncSession, closed_set: set[tuple[str, str]]
) -> dict[tuple[str, str], Decimal]:
    """Batched realized-gain EUR per holding, restricted to the closed set.

    Replicates _realized_gain_eur: sum of LotAlloc.realized_gain_eur over
    disposal (sell/spend) txns for the holding, coalesced to ZERO. Joins
    LotAlloc → its sell Transaction so we can group by (account, instrument).
    """
    if not closed_set:
        return {}
    # realized_gain_eur is TEXT-backed — fetch raw per-alloc values and sum per
    # holding in Python. SQL SUM ignores NULLs; we skip None to match. A SQL
    # SUM would coerce the text values back to float (see plan 006).
    stmt = (
        select(
            Transaction.account_id,
            Transaction.instrument_id,
            LotAlloc.realized_gain_eur,
        )
        .join(Transaction, LotAlloc.sell_txn_id == Transaction.id)
        .where(
            Transaction.txn_type.in_(("sell", "spend")),
            Transaction.deleted_at.is_(None),
        )
    )
    result = await session.execute(stmt)
    realized_by: dict[tuple[str, str], Decimal] = {}
    for account_id, instrument_id, realized_gain in result:
        key = (account_id, instrument_id)
        if key not in closed_set:
            continue
        realized_by[key] = realized_by.get(key, ZERO) + (realized_gain or ZERO)
    return realized_by


def _average_buy_cost_from_parts(
    basis: Decimal,
    quantity: Decimal,
    display_currency: str,
    as_of: date,
    snapshot: MarketDataSnapshot,
) -> Decimal | None:
    """Pure reducer matching _average_buy_cost: None when quantity<=ZERO or
    basis<=ZERO, else (basis/quantity) converted at as_of via the snapshot.
    """
    if quantity is None or quantity <= ZERO or basis <= ZERO:
        return None
    avg_cost_eur = basis / quantity
    return snapshot.convert(avg_cost_eur, "EUR", display_currency, as_of=as_of)


async def _last_close_price(
    session: AsyncSession,
    instrument_id: str,
    display_currency: str,
    last_close_date: date,
    snapshot: MarketDataSnapshot,
) -> Decimal | None:
    # Quote is resolved at last_close_date (per-row date) — kept as a DB call
    # because the single-as_of snapshot quote would resolve the wrong date.
    quote = await _latest_quote(session, instrument_id, last_close_date)
    if quote is None:
        return None
    # FX conversion via snapshot per-date map at last_close_date.
    return snapshot.convert(
        quote.price,
        quote.currency,
        display_currency,
        as_of=last_close_date,
    )
