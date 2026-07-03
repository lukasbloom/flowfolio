"""Concentration analytics service.

Services stage changes only. Routers own commit and rollback boundaries.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, InvalidOperation

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.constants import ZERO
from app.models import ConcentrationMute, Instrument, Transaction, UserSetting
from app.schemas.concentration import (
    ConcentrationOffender,
    ConcentrationResponse,
    MutedHolding,
)
from app.services.market_data import load_market_data
from app.services.perf import calculate_open_lot_basis_batch

DEFAULT_THRESHOLD = Decimal("0.25")


async def get_concentration_offenders(
    session: AsyncSession, display_currency: str = "EUR"
) -> ConcentrationResponse:
    threshold = await _get_threshold(session)
    muted_ids = await _muted_instrument_ids(session)
    as_of = clock.today()

    holding_stmt = (
        select(Transaction.account_id, Transaction.instrument_id)
        .where(Transaction.deleted_at.is_(None))
        .group_by(Transaction.account_id, Transaction.instrument_id)
    )
    holding_result = await session.execute(holding_stmt)
    holdings = list(holding_result.all())
    instrument_ids = {instrument_id for _, instrument_id in holdings}

    instruments_by_id: dict[str, Instrument] = {}
    if instrument_ids:
        instrument_result = await session.execute(
            select(Instrument).where(Instrument.id.in_(instrument_ids))
        )
        instruments_by_id = {
            instrument.id: instrument for instrument in instrument_result.scalars()
        }

    # Bulk-load latest-quote-per-instrument + FX once at as_of
    # (single-rate consumer) instead of per-holding round-trips.
    snapshot = await load_market_data(
        session, as_of=as_of, instrument_ids=instrument_ids
    )

    # Batch the per-holding lot-basis (3 queries each)
    # into request-constant grouped loads. Batched results are byte-identical to
    # the per-holding helper (see calculate_open_lot_basis_batch docstring).
    lot_basis_by_holding = await calculate_open_lot_basis_batch(session)

    values_by_instrument: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for account_id, instrument_id in holdings:
        basis = lot_basis_by_holding.get((account_id, instrument_id))
        if basis is None or basis.open_quantity <= ZERO:
            continue
        quote = snapshot.latest_quote(instrument_id)
        if quote is None:
            continue
        value = snapshot.convert(
            basis.open_quantity * quote.price,
            quote.currency,
            display_currency,
        )
        values_by_instrument[instrument_id] += value

    total_nw = sum(values_by_instrument.values(), ZERO)
    offenders: list[ConcentrationOffender] = []
    if total_nw > ZERO:
        for instrument_id, value in values_by_instrument.items():
            if instrument_id in muted_ids:
                continue
            percent = value / total_nw
            instrument = instruments_by_id.get(instrument_id)
            if percent > threshold and instrument is not None:
                offenders.append(
                    ConcentrationOffender(
                        instrument_id=instrument.id,
                        instrument_symbol=instrument.symbol,
                        percent=percent,
                    )
                )

    offenders.sort(key=lambda item: item.percent, reverse=True)
    return ConcentrationResponse(threshold=threshold, offenders=offenders)


async def list_muted_instruments(session: AsyncSession) -> list[MutedHolding]:
    stmt = (
        select(ConcentrationMute.instrument_id, Instrument.symbol, Instrument.name)
        .join(Instrument, Instrument.id == ConcentrationMute.instrument_id)
        .order_by(Instrument.symbol.asc())
    )
    result = await session.execute(stmt)
    return [
        MutedHolding(
            instrument_id=instrument_id,
            instrument_symbol=symbol,
            instrument_name=name,
        )
        for instrument_id, symbol, name in result.all()
    ]


async def add_mute(session: AsyncSession, instrument_id: str) -> None:
    existing = await session.get(ConcentrationMute, instrument_id)
    if existing is None:
        session.add(ConcentrationMute(instrument_id=instrument_id))
        await session.flush()


async def remove_mute(session: AsyncSession, instrument_id: str) -> bool:
    result = await session.execute(
        delete(ConcentrationMute).where(ConcentrationMute.instrument_id == instrument_id)
    )
    return bool(result.rowcount)


async def _get_threshold(session: AsyncSession) -> Decimal:
    result = await session.execute(
        select(UserSetting.value).where(UserSetting.key == "concentration_threshold")
    )
    raw_value = result.scalar_one_or_none()
    if raw_value is None:
        return DEFAULT_THRESHOLD
    try:
        return Decimal(raw_value)
    except (InvalidOperation, TypeError):
        return DEFAULT_THRESHOLD


async def _muted_instrument_ids(session: AsyncSession) -> set[str]:
    result = await session.execute(select(ConcentrationMute.instrument_id))
    return set(result.scalars())
