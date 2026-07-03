"""APY rate configuration CRUD with effective-from-history cascade."""
from __future__ import annotations

from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.apy_config import ApyConfig
from app.models.transaction import Transaction
from app.schemas.apy_config import ApyConfigCreate, ApyConfigResponse, ApyConfigUpdate

router = APIRouter(prefix="/api/apy-config", tags=["apy_config"])


@router.post("", response_model=ApyConfigResponse, status_code=201)
async def create_apy_config(body: ApyConfigCreate, db: AsyncSession = Depends(get_db)):
    """Create an APY config and close the prior open row for the pair."""
    prior_stmt = select(ApyConfig).where(
        ApyConfig.account_id == body.account_id,
        ApyConfig.instrument_id == body.instrument_id,
        ApyConfig.effective_to.is_(None),
    )
    prior = (await db.execute(prior_stmt)).scalar_one_or_none()
    if prior is not None:
        if prior.effective_from == body.effective_from:
            raise HTTPException(
                409,
                f"apy_config already exists for (account_id={body.account_id}, "
                f"instrument_id={body.instrument_id}, effective_from={body.effective_from})",
            )
        if prior.effective_from >= body.effective_from:
            raise HTTPException(
                400,
                f"new effective_from ({body.effective_from}) must be after prior open row "
                f"effective_from ({prior.effective_from})",
            )
        prior.effective_to = body.effective_from - timedelta(days=1)

    new_row = ApyConfig(**body.model_dump())
    db.add(new_row)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            409,
            f"apy_config already exists for (account_id={body.account_id}, "
            f"instrument_id={body.instrument_id}, effective_from={body.effective_from})",
        ) from None
    await db.refresh(new_row)
    return new_row


@router.get("", response_model=list[ApyConfigResponse])
async def list_apy_configs(
    account_id: Optional[str] = None,
    instrument_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    stmt = select(ApyConfig)
    if account_id is not None:
        stmt = stmt.where(ApyConfig.account_id == account_id)
    if instrument_id is not None:
        stmt = stmt.where(ApyConfig.instrument_id == instrument_id)
    stmt = stmt.order_by(
        ApyConfig.account_id, ApyConfig.instrument_id, ApyConfig.effective_from
    )
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/{config_id}", response_model=ApyConfigResponse)
async def get_apy_config(config_id: str, db: AsyncSession = Depends(get_db)):
    row = await db.get(ApyConfig, config_id)
    if row is None:
        raise HTTPException(404, "apy_config not found")
    return row


@router.patch("/{config_id}", response_model=ApyConfigResponse)
async def update_apy_config(
    config_id: str, body: ApyConfigUpdate, db: AsyncSession = Depends(get_db)
):
    row = await db.get(ApyConfig, config_id)
    if row is None:
        raise HTTPException(404, "apy_config not found")
    update_data = body.model_dump(exclude_unset=True)
    if "effective_from" in update_data:
        raise HTTPException(400, "effective_from is immutable (cascade integrity)")
    for field, value in update_data.items():
        setattr(row, field, value)
    await db.commit()
    await db.refresh(row)
    return row


@router.delete("/{config_id}", status_code=204)
async def delete_apy_config(config_id: str, db: AsyncSession = Depends(get_db)):
    row = await db.get(ApyConfig, config_id)
    if row is None:
        raise HTTPException(404, "apy_config not found")
    ref_stmt = select(Transaction.id).where(Transaction.apy_config_id == config_id).limit(1)
    referenced = (await db.execute(ref_stmt)).scalar_one_or_none()
    if referenced is not None:
        raise HTTPException(
            409,
            "apy_config is referenced by yield transactions. "
            "PATCH effective_to to close it instead, or delete the yield txns first.",
        )
    await db.delete(row)
    await db.commit()
    return None
