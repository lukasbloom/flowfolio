"""Manual NAV override + price reads. Source-tagged history."""
from __future__ import annotations

from datetime import date as date_type, timedelta
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.price_quote import PriceQuote
from app.schemas.price_quote import ManualNavOverride, PriceQuoteResponse

router = APIRouter(prefix="/api/prices", tags=["prices"])
MAX_MANUAL_PRICE = Decimal("1e12")

# Local resolver — deliberately NOT reusing networth._resolve_range because its
# "all" branch is bounded by the first transaction date, which is wrong for
# prices (price history may predate the first txn for backfilled instruments).
_PRICE_TIMEFRAME_DAYS = {"1m": 30, "3m": 90, "1y": 365}


def _resolve_price_range(
    timeframe: str,
    from_date: date_type | None,
    to_date: date_type | None,
) -> tuple[date_type | None, date_type | None]:
    """Returns (start, end). None on either side means 'no bound on that side'.

    Custom-range validation happens in the router before this is called.
    """
    if timeframe == "custom":
        return from_date, to_date
    if timeframe == "all":
        return None, None
    days = _PRICE_TIMEFRAME_DAYS[timeframe]
    end = date_type.today()
    return end - timedelta(days=days), end


@router.post("/manual", response_model=PriceQuoteResponse, status_code=201)
async def create_manual_nav(body: ManualNavOverride, db: AsyncSession = Depends(get_db)):
    """User supplies an explicit NAV for an (instrument, date) pair."""
    if body.price > MAX_MANUAL_PRICE:
        raise HTTPException(422, "manual price is implausibly high")

    existing_stmt = select(PriceQuote).where(
        PriceQuote.instrument_id == body.instrument_id,
        PriceQuote.date == body.date,
        PriceQuote.source == "manual",  # source="manual"
    )
    existing = (await db.execute(existing_stmt)).scalar_one_or_none()
    if existing is not None:
        existing.price = body.price
        existing.currency = body.currency
        await db.commit()
        await db.refresh(existing)
        return existing

    new_row = PriceQuote(
        instrument_id=body.instrument_id,
        date=body.date,
        price=body.price,
        currency=body.currency,
        source="manual",
    )
    db.add(new_row)
    await db.commit()
    await db.refresh(new_row)
    return new_row


@router.get("/{instrument_id}/latest", response_model=PriceQuoteResponse)
async def get_latest_quote(instrument_id: str, db: AsyncSession = Depends(get_db)):
    """Manual wins over API for the same (instrument, date) pair."""
    today = date_type.today()
    # manual_today_stmt: explicit same-day manual override check.
    stmt = select(PriceQuote).where(
        PriceQuote.instrument_id == instrument_id,
        PriceQuote.date == today,
        PriceQuote.source == "manual",
    )
    manual_result = await db.execute(stmt)
    manual = manual_result.scalar_one_or_none()
    if manual is not None:
        return manual

    stmt = (
        select(PriceQuote)
        .where(PriceQuote.instrument_id == instrument_id)
        .order_by(PriceQuote.date.desc(), PriceQuote.fetched_at.desc())
        .limit(1)
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, f"no price_quote rows for instrument {instrument_id}")
    return row


@router.get("/{instrument_id}/history", response_model=list[PriceQuoteResponse])
async def get_price_history(
    instrument_id: str,
    source: Optional[str] = Query(None, pattern="^(finnhub|alpha_vantage|coingecko|ft|manual)$"),
    timeframe: str = Query("all", pattern="^(1m|3m|1y|all|custom)$"),
    from_date: date_type | None = Query(None, alias="from"),
    to_date: date_type | None = Query(None, alias="to"),
    order: str = Query("asc", pattern="^(asc|desc)$"),
    limit: Optional[int] = Query(None, ge=1, le=10000),
    db: AsyncSession = Depends(get_db),
):
    """Date-range-filtered price history.

    - Default ``timeframe`` is ``all`` so callers without query params get every
      row (the legacy 50-row default truncated silently).
    - Default ordering is chronological ASC — matches the chart's expected
      reading order. ``NavHistoryTab`` opts back into DESC via ``?order=desc``.
    - ``limit`` is optional with a generous 10000 upper bound as a safety hatch.
    - 422 error strings mirror ``/api/networth`` byte-for-byte so the frontend's
      error-handling path is identical between the two endpoints.
    """
    if timeframe == "custom" and (from_date is None or to_date is None):
        raise HTTPException(422, "custom timeframe requires both from and to dates")
    if from_date is not None and to_date is not None and from_date > to_date:
        raise HTTPException(422, "from must be on or before to")

    range_start, range_end = _resolve_price_range(timeframe, from_date, to_date)

    stmt = select(PriceQuote).where(PriceQuote.instrument_id == instrument_id)
    if source is not None:
        stmt = stmt.where(PriceQuote.source == source)
    if range_start is not None:
        stmt = stmt.where(PriceQuote.date >= range_start)
    if range_end is not None:
        stmt = stmt.where(PriceQuote.date <= range_end)
    if order == "desc":
        stmt = stmt.order_by(PriceQuote.date.desc(), PriceQuote.fetched_at.desc())
    else:
        stmt = stmt.order_by(PriceQuote.date.asc(), PriceQuote.fetched_at.asc())
    if limit is not None:
        stmt = stmt.limit(limit)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.delete("/manual/{quote_id}", status_code=204)
async def delete_manual_quote(quote_id: str, db: AsyncSession = Depends(get_db)):
    row = await db.get(PriceQuote, quote_id)
    if row is None:
        raise HTTPException(404, "price_quote not found")
    if row.source != "manual":
        raise HTTPException(400, f"only manual quotes are deletable; got source={row.source!r}")
    await db.delete(row)
    await db.commit()
    return None
