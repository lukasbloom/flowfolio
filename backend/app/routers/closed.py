from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import CURRENCY_PATTERN
from app.core.database import get_db
from app.routers._timeframe import validate_custom_timeframe
from app.schemas.closed import ClosedPositionRow
from app.services.closed import get_closed_positions

router = APIRouter(prefix="/api/closed", tags=["closed"])


@router.get("", response_model=list[ClosedPositionRow])
async def list_closed(
    # Mirror /api/perf's contract so PerfTable can share the
    # timeframe/from/to wire shape across modes. Closed positions are
    # timeframe-invariant for the presets (already-closed; final-period TWRR),
    # so non-"custom" timeframe values are accepted-but-not-used. For "custom",
    # we filter rows whose last_close_date falls in [from_date, to_date].
    timeframe: str = Query("1y", pattern="^(1m|3m|1y|all|custom)$"),
    currency: str = Query("EUR", pattern=CURRENCY_PATTERN),
    tag: str | None = Query(None, max_length=64),
    from_date: date | None = Query(None, alias="from"),
    to_date: date | None = Query(None, alias="to"),
    db: AsyncSession = Depends(get_db),
):
    validate_custom_timeframe(timeframe, from_date, to_date)

    rows = await get_closed_positions(db, display_currency=currency, tag_filter=tag)

    if timeframe == "custom" and from_date is not None and to_date is not None:
        rows = [
            row
            for row in rows
            if row.last_close_date is not None
            and from_date <= row.last_close_date <= to_date
        ]
    return rows
