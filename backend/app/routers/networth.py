from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import CURRENCY_PATTERN
from app.core.database import get_db
from app.schemas.networth import NetWorthResponse
from app.services.networth import get_networth_series

router = APIRouter(prefix="/api/networth", tags=["networth"])


@router.get("", response_model=NetWorthResponse)
async def get_networth(
    timeframe: str = Query("1y", pattern="^(1m|3m|1y|all|custom)$"),
    currency: str = Query("EUR", pattern=CURRENCY_PATTERN),
    from_date: date | None = Query(None, alias="from"),
    to_date: date | None = Query(None, alias="to"),
    # Per-chart instrument multi-select. Repeated
    # ?instrument_id=<uuid>&instrument_id=<uuid> params parse into a list;
    # a single value parses into a one-element list (back-compat with the
    # instrument detail page). Empty list = full portfolio (today's behavior).
    instrument_id: list[str] = Query(default_factory=list),
    # Optional cost-basis series + global tag filter so the
    # dashboard can layer the cost-basis line atop the value line without a
    # second endpoint. ``include_cost_basis`` defaults False, old callers
    # (instrument detail page) keep their slim response.
    include_cost_basis: bool = Query(False),
    tag: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    # NOTE: deliberately NOT routed through routers._timeframe.validate_custom_timeframe.
    # networth's guard differs in BEHAVIOR from perf/closed: the from>to check runs
    # for every timeframe (not just "custom"), and the message strings differ
    # ("both from and to dates", "from must be on or before to"). Kept inline to
    # preserve those byte-for-byte.
    if timeframe == "custom" and (from_date is None or to_date is None):
        raise HTTPException(
            status_code=422,
            detail="custom timeframe requires both from and to dates",
        )
    if from_date is not None and to_date is not None and from_date > to_date:
        raise HTTPException(status_code=422, detail="from must be on or before to")

    return await get_networth_series(
        db,
        timeframe=timeframe,
        display_currency=currency,
        start=from_date,
        end=to_date,
        instrument_ids=instrument_id,
        tag_filter=tag,
        include_cost_basis=include_cost_basis,
    )
