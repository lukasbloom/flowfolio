from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import CURRENCY_PATTERN
from app.core.database import get_db
from app.schemas.contributions import ContributionsResponse
from app.services.contributions import get_contribution_segments, get_cost_basis_series

router = APIRouter(prefix="/api/contributions", tags=["contributions"])


@router.get("", response_model=ContributionsResponse)
async def get_contributions(
    period: Literal["month", "year"] = Query("month", pattern="^(month|year)$"),
    currency: str = Query("EUR", pattern=CURRENCY_PATTERN),
    tag: str | None = Query(None, max_length=64),
    # Per-chart instrument multi-select. Repeated
    # ?instrument_id=<uuid>&instrument_id=<uuid> params parse into a list;
    # empty list = full portfolio (today's behavior).
    instrument_id: list[str] = Query(default_factory=list),
    db: AsyncSession = Depends(get_db),
):
    cost_basis, value = await get_cost_basis_series(
        db,
        display_currency=currency,
        tag_filter=tag,
        instrument_ids=instrument_id,
    )
    buckets = await get_contribution_segments(
        db,
        period=period,
        display_currency=currency,
        tag_filter=tag,
        instrument_ids=instrument_id,
    )
    return ContributionsResponse(
        currency=currency,
        period=period,
        cost_basis_series=cost_basis,
        portfolio_value_series=value,
        buckets=buckets,
    )
