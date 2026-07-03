from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import CURRENCY_PATTERN
from app.core.database import get_db
from app.schemas.allocation import AllocationResponse
from app.services.allocation import get_allocation_slices

router = APIRouter(prefix="/api/allocation", tags=["allocation"])


@router.get("", response_model=AllocationResponse)
async def list_allocation(
    dimension: Literal["type", "risk", "account", "banked"] = Query(
        ..., pattern="^(type|risk|account|banked)$"
    ),
    currency: str = Query("EUR", pattern=CURRENCY_PATTERN),
    tag: str | None = Query(None, max_length=64),
    db: AsyncSession = Depends(get_db),
):
    return await get_allocation_slices(
        db,
        dimension=dimension,
        display_currency=currency,
        tag_filter=tag,
    )
