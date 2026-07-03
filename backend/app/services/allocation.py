"""Allocation analytics service.

Services do not commit or roll back transactions; routers own transaction
boundaries.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.constants import ZERO
from app.models import Account, Instrument
from app.schemas.allocation import AllocationResponse, AllocationSlice
from app.services.market_data import load_market_data
from app.services.perf import calculate_open_lot_basis_batch
from app.services.quotes import (
    load_holdings,
)

Dimension = Literal["type", "risk", "account", "banked"]


async def get_allocation_slices(
    session: AsyncSession,
    dimension: Dimension,
    display_currency: str = "EUR",
    tag_filter: str | None = None,
) -> AllocationResponse:
    as_of = clock.today()
    holdings, accounts_by_id, instruments_by_id = await load_holdings(session, tag_filter)

    # Bulk-load latest-quote-per-instrument + FX once at as_of
    # (single-rate consumer) instead of per-holding latest_quote/convert_currency
    # round-trips. Snapshot accessors are byte-identical to the per-call helpers.
    snapshot = await load_market_data(
        session,
        as_of=as_of,
        instrument_ids={instrument_id for _, instrument_id in holdings},
    )

    # Batch the per-holding lot-basis (3 queries each)
    # into request-constant grouped loads. Batched results are byte-identical to
    # the per-holding helper (see calculate_open_lot_basis_batch docstring).
    lot_basis_by_holding = await calculate_open_lot_basis_batch(session)

    values_by_label: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for account_id, instrument_id in holdings:
        account = accounts_by_id.get(account_id)
        instrument = instruments_by_id.get(instrument_id)
        if account is None or instrument is None:
            continue

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
        values_by_label[_label_for(dimension, account, instrument)] += value

    total = sum(values_by_label.values(), ZERO)
    slices = [
        AllocationSlice(
            label=label,
            value=value,
            percent=ZERO if total <= ZERO else value / total,
        )
        for label, value in sorted(
            values_by_label.items(), key=lambda item: item[1], reverse=True
        )
    ]
    return AllocationResponse(
        dimension=dimension,
        currency=display_currency,
        total=total,
        slices=slices,
    )


def _label_for(dimension: Dimension, account: Account, instrument: Instrument) -> str:
    if dimension == "type":
        return instrument.instrument_type
    if dimension == "risk":
        return instrument.risk_level
    if dimension == "account":
        return account.name
    if dimension == "banked":
        return "Banked" if account.is_banked else "Non-banked"
    raise ValueError(f"unsupported allocation dimension: {dimension}")
