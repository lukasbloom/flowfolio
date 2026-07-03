from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import CURRENCY_PATTERN
from app.core.database import get_db
from app.routers._timeframe import validate_custom_timeframe
from app.schemas.perf import PerfHoldingResponse
from app.services.perf import get_performance_rows

router = APIRouter(prefix="/api/perf", tags=["performance"])


@router.get("", response_model=list[PerfHoldingResponse])
async def get_performance(
    timeframe: str = Query("1y", pattern="^(1m|3m|1y|all|custom)$"),
    currency: str = Query("EUR", pattern=CURRENCY_PATTERN),
    tag: str | None = Query(None, max_length=64),
    include_closed: bool = Query(False),  # Opt-in for /compare AllocationDrill.
    # Aliased so the URL reads ?timeframe=custom&from=YYYY-MM-DD&to=YYYY-MM-DD.
    # The dates are silently ignored when timeframe != "custom" so stale URL params don't 422.
    from_date: date | None = Query(None, alias="from"),
    to_date: date | None = Query(None, alias="to"),
    db: AsyncSession = Depends(get_db),
):
    validate_custom_timeframe(timeframe, from_date, to_date)
    return await get_performance_rows(
        db,
        timeframe=timeframe,
        display_currency=currency,
        tag_filter=tag,
        include_closed=include_closed,
        from_date=from_date if timeframe == "custom" else None,
        to_date=to_date if timeframe == "custom" else None,
    )
