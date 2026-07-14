"""Realized-gain analytics service.

Services do not commit or roll back transactions; routers own transaction
boundaries.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.constants import ZERO
from app.models import HoldingTag, Instrument, LotAlloc, Tag, Transaction
from app.schemas.realized import RealizedPerHolding, RealizedTotals
from app.services.quotes import convert
from app.services.quotes import convert_currency as _convert_currency
from app.services.quotes import latest_eur_usd_rate as _latest_eur_usd_rate


async def get_realized_per_holding(
    session: AsyncSession,
    display_currency: str = "EUR",
    tag_filter: str | None = None,
) -> list[RealizedPerHolding]:
    # realized_gain_eur is TEXT-backed (DecimalText) — fetch the raw per-alloc
    # values and sum per instrument in Python; a SQL SUM would coerce the text
    # back to float (see plan 006). The GROUP BY collapses to a Python reduce.
    stmt = (
        select(
            Transaction.instrument_id,
            LotAlloc.realized_gain_eur,
        )
        .join(Transaction, LotAlloc.sell_txn_id == Transaction.id)
        .where(
            Transaction.deleted_at.is_(None),
            LotAlloc.realized_gain_eur.is_not(None),
        )
    )
    if tag_filter is not None:
        stmt = stmt.where(
            _tagged_holding_exists(Transaction.account_id, Transaction.instrument_id, tag_filter)
        )

    result = await session.execute(stmt)
    realized_by_instrument: dict[str, Decimal] = {}
    for instrument_id, realized_gain in result:
        realized_by_instrument[instrument_id] = (
            realized_by_instrument.get(instrument_id, ZERO) + realized_gain
        )
    # Preserve the old SQL `GROUP BY instrument_id` ordering (SQLite emits
    # groups sorted by the grouping key) so the response row order is stable
    # and byte-identical to the pre-TEXT-storage baseline.
    rows = sorted(realized_by_instrument.items())
    instrument_ids = {instrument_id for instrument_id, _ in rows}
    symbols_by_id: dict[str, str] = {}
    if instrument_ids:
        instruments = await session.execute(
            select(Instrument).where(Instrument.id.in_(instrument_ids))
        )
        symbols_by_id = {
            instrument.id: instrument.symbol for instrument in instruments.scalars()
        }

    as_of = clock.today()
    # Hoist the single EUR/USD rate out of the per-instrument
    # loop — every row converts at the same as_of, so one rate lookup replaces N.
    # Resolve the rate ONLY for a non-EUR display currency AND only when there is
    # at least one row to convert. Two behaviors must be preserved byte-for-byte:
    #   - convert_currency short-circuits the EUR->EUR identity WITHOUT a rate
    #     lookup, so EUR display must never trigger MissingFxRateError.
    #   - the old per-row loop only called _convert_currency when `rows` was
    #     non-empty, so a zero-row result with missing FX must NOT raise (the
    #     perf USD missing-FX fall-through path relies on this). Resolving the
    #     rate eagerly when rows is empty would newly 500.
    # `convert(...)` below mirrors convert_currency's arithmetic exactly.
    rate = (
        await _latest_eur_usd_rate(session, as_of)
        if display_currency != "EUR" and rows
        else None
    )
    response: list[RealizedPerHolding] = []
    for instrument_id, realized_eur in rows:
        amount = realized_eur or ZERO
        if display_currency == "EUR":
            realized = amount
        else:
            realized = convert(amount, "EUR", display_currency, rate)
        response.append(
            RealizedPerHolding(
                instrument_id=instrument_id,
                instrument_symbol=symbols_by_id.get(instrument_id, instrument_id),
                realized_eur=realized,
            )
        )
    return response


async def get_realized_totals(
    session: AsyncSession,
    display_currency: str = "EUR",
    tag_filter: str | None = None,
) -> RealizedTotals:
    # "This year" is a calendar boundary the user reasons about in their own
    # timezone, so use today_local() per clock.today_local's docstring, not
    # the UTC today().
    year_start = date(clock.today_local().year, 1, 1)
    lifetime = await _realized_sum(session, tag_filter=tag_filter)
    this_year = await _realized_sum(
        session, tag_filter=tag_filter, start_date=year_start
    )
    as_of = clock.today_local()
    return RealizedTotals(
        currency=display_currency,
        lifetime=await _convert_currency(session, lifetime, "EUR", display_currency, as_of),
        this_year=await _convert_currency(
            session, this_year, "EUR", display_currency, as_of
        ),
    )


async def _realized_sum(
    session: AsyncSession,
    *,
    tag_filter: str | None = None,
    start_date: date | None = None,
) -> Decimal:
    # realized_gain_eur is TEXT-backed — sum in Python (no SQL SUM on TEXT).
    stmt = (
        select(LotAlloc.realized_gain_eur)
        .join(Transaction, LotAlloc.sell_txn_id == Transaction.id)
        .where(
            Transaction.deleted_at.is_(None),
            LotAlloc.realized_gain_eur.is_not(None),
        )
    )
    if start_date is not None:
        stmt = stmt.where(Transaction.date >= start_date)
    if tag_filter is not None:
        stmt = stmt.where(
            _tagged_holding_exists(Transaction.account_id, Transaction.instrument_id, tag_filter)
        )
    result = await session.execute(stmt)
    return sum((gain for (gain,) in result), ZERO)


def _tagged_holding_exists(account_id, instrument_id, tag_filter: str):
    return (
        select(HoldingTag.account_id)
        .join(Tag, Tag.id == HoldingTag.tag_id)
        .where(
            HoldingTag.account_id == account_id,
            HoldingTag.instrument_id == instrument_id,
            Tag.name == tag_filter,
        )
        .exists()
    )
