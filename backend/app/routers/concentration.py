from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import CURRENCY_PATTERN
from app.core.database import get_db
from app.schemas.concentration import ConcentrationResponse, MutedHolding
from app.services.concentration import (
    add_mute,
    get_concentration_offenders,
    list_muted_instruments,
    remove_mute,
)

router = APIRouter(prefix="/api/concentration", tags=["concentration"])


@router.get("", response_model=ConcentrationResponse)
async def get_concentration(
    currency: str = Query("EUR", pattern=CURRENCY_PATTERN),
    db: AsyncSession = Depends(get_db),
):
    return await get_concentration_offenders(db, display_currency=currency)


@router.get("/mutes", response_model=list[MutedHolding])
async def list_mutes(db: AsyncSession = Depends(get_db)):
    return await list_muted_instruments(db)


@router.post("/mute/{instrument_id}", status_code=204)
async def mute(instrument_id: str, db: AsyncSession = Depends(get_db)):
    await add_mute(db, instrument_id)
    await db.commit()


@router.delete("/mute/{instrument_id}", status_code=204)
async def unmute(instrument_id: str, db: AsyncSession = Depends(get_db)):
    deleted = await remove_mute(db, instrument_id)
    await db.commit()
    if not deleted:
        raise HTTPException(status_code=404, detail="Mute not found")
