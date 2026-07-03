from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import CURRENCY_PATTERN
from app.core.database import get_db
from app.schemas.realized import RealizedResponse
from app.services.realized import get_realized_per_holding, get_realized_totals

router = APIRouter(prefix="/api/realized", tags=["realized"])


@router.get("", response_model=RealizedResponse)
async def get_realized(
    currency: str = Query("EUR", pattern=CURRENCY_PATTERN),
    tag: str | None = Query(None, max_length=64),
    db: AsyncSession = Depends(get_db),
):
    totals = await get_realized_totals(db, display_currency=currency, tag_filter=tag)
    per = await get_realized_per_holding(db, display_currency=currency, tag_filter=tag)
    return RealizedResponse(totals=totals, per_holding=per)
