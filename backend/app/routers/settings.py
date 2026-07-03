from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.schemas.settings import SettingsResponse, SettingUpdate
from app.services.settings import get_settings, upsert_setting

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("", response_model=SettingsResponse)
async def list_settings(db: AsyncSession = Depends(get_db)):
    return SettingsResponse(settings=await get_settings(db))


@router.put("/{key}", status_code=204)
async def update_setting(
    body: SettingUpdate,
    key: str = Path(..., max_length=64, pattern="^[a-z_]+$"),
    db: AsyncSession = Depends(get_db),
):
    try:
        row = await upsert_setting(db, key, body.value)
        await db.flush()
        await db.refresh(row)
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await db.commit()
