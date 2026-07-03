"""FX rate router. Powers the txn-form prefill and the FX history table.

Only EUR/USD pairs are supported.
Walk-back to last published Frankfurter business day is implicit in the
upstream service; this router simply delegates to get_or_fetch_fx_rate.
fx_rate rows are stored EUR-base (rate = USD per 1 EUR). For UI requests
asking USD→EUR, we still return the EUR-base row; the frontend inverts as
needed at display time. Storing only one direction keeps the cache tight.
POST /api/fx/manual creates/upserts a manual override row.

AuthMiddleware (registered in main.py) gates every path on this router, no
explicit auth dependency needed.
"""
from __future__ import annotations

from datetime import date, timedelta

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.database import get_db
from app.models.fx_rate import FxRate
from app.schemas.fx_rate import VALID_CURRENCIES, FxRateCreate, FxRateResponse
from app.services.fx import get_or_fetch_fx_rate

router = APIRouter(prefix="/api/fx", tags=["fx"])


@router.get("", response_model=list[FxRateResponse])
async def list_rates(
    timeframe: str = Query("3m", pattern="^(1m|3m|1y|all)$"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """History table. Default 3m / 50 rows / page."""
    cutoff_map = {
        "1m": clock.today() - timedelta(days=30),
        "3m": clock.today() - timedelta(days=90),
        "1y": clock.today() - timedelta(days=365),
        "all": None,
    }
    cutoff = cutoff_map[timeframe]
    stmt = select(FxRate).where(
        FxRate.base_currency == "EUR", FxRate.quote_currency == "USD"
    )
    if cutoff is not None:
        stmt = stmt.where(FxRate.date >= cutoff)
    stmt = stmt.order_by(FxRate.date.desc()).limit(limit).offset(offset)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.post("/manual", response_model=FxRateResponse, status_code=201)
async def create_manual_override(
    body: FxRateCreate, db: AsyncSession = Depends(get_db)
):
    """User supplies an explicit rate (broker-actual or API-down fallback).

    Upsert: if a row already exists for (date, base, quote), update its rate
    and flip source to 'manual'.
    """
    if body.source != "manual":
        raise HTTPException(400, "POST /api/fx/manual requires source='manual'")

    existing_stmt = select(FxRate).where(
        FxRate.date == body.date,
        FxRate.base_currency == body.base_currency,
        FxRate.quote_currency == body.quote_currency,
    )
    existing = (await db.execute(existing_stmt)).scalar_one_or_none()
    if existing is not None:
        existing.rate = body.rate
        existing.source = "manual"
        await db.commit()
        await db.refresh(existing)
        return existing

    new_row = FxRate(
        date=body.date,
        base_currency=body.base_currency,
        quote_currency=body.quote_currency,
        rate=body.rate,
        source="manual",
    )
    db.add(new_row)
    await db.commit()
    await db.refresh(new_row)
    return new_row


@router.get("/{on_date}", response_model=FxRateResponse)
async def get_rate_for_date(
    on_date: date,
    from_currency: str = Query("USD", alias="from"),
    to_currency: str = Query("EUR", alias="to"),
    db: AsyncSession = Depends(get_db),
):
    """On-demand rate for the txn-form prefill.

    The cache stores EUR-base rows only. We accept any (from, to) ∈ {EUR, USD}
    pair and resolve to the canonical EUR→USD row; the frontend inverts when
    displaying USD→EUR. Same-currency requests are 400.
    """
    if from_currency not in VALID_CURRENCIES or to_currency not in VALID_CURRENCIES:
        raise HTTPException(400, f"only {VALID_CURRENCIES} supported")
    if from_currency == to_currency:
        raise HTTPException(400, "from must differ from to")

    base, quote = "EUR", "USD"
    async with httpx.AsyncClient() as client:
        try:
            row = await get_or_fetch_fx_rate(db, client, on_date, base=base, quote=quote)
        except ValueError as e:
            raise HTTPException(502, f"fx upstream error: {e}")
    await db.commit()
    await db.refresh(row)
    return row
