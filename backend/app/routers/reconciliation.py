"""Reconciliation router.

Two endpoints:
- GET /api/reconciliation/preview — diff between app-computed holdings and a
  hypothetical broker snapshot at a chosen as-of date.
- POST /api/reconciliation/events — atomic batch save of accept/reject/
  dismiss decisions; writes the reconciliation row + adjustment txns; the
  Reject-drift flow lands real txns through POST /api/transactions before
  this call (linked via reconciliation_id passthrough).
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.database import get_db
from app.models.account import Account
from app.models.reconciliation import Reconciliation
from app.schemas.reconciliation import (
    ReconciliationCreate,
    ReconciliationPreviewResponse,
    ReconciliationResponse,
)
from app.services.reconciliation import build_preview, save_event

router = APIRouter(prefix="/api/reconciliation", tags=["reconciliation"])


async def _last_reconciled_for(
    db: AsyncSession, account_id: str
) -> Optional[date]:
    stmt = select(func.max(Reconciliation.snapshot_date)).where(
        Reconciliation.account_id == account_id
    )
    result = await db.execute(stmt)
    return result.scalar()


@router.get("/preview", response_model=ReconciliationPreviewResponse)
async def get_preview(
    account_id: str = Query(..., min_length=1),
    snapshot_date: date = Query(...),
    db: AsyncSession = Depends(get_db),
) -> ReconciliationPreviewResponse:
    # 404 if account does not exist (no cross-tenant concern in single-user app).
    acct = await db.execute(select(Account).where(Account.id == account_id))
    if acct.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="account not found")

    # Future-date guard: reject before invoking the service. Defense-in-depth
    # alongside the Pydantic validator on ReconciliationCreate (the GET shape
    # has no Pydantic body, so the router enforces it explicitly). Compare in
    # the user's local calendar (clock.today_local) — UTC date.today() is up to
    # 2h behind Madrid and wrongly rejects a "today" snapshot near local midnight.
    if snapshot_date > clock.today_local():
        raise HTTPException(
            status_code=422,
            detail="snapshot_date cannot be in the future",
        )

    rows = await build_preview(db, account_id=account_id, snapshot_date=snapshot_date)
    last = await _last_reconciled_for(db, account_id)
    return ReconciliationPreviewResponse(
        account_id=account_id,
        snapshot_date=snapshot_date,
        rows=rows,
        last_reconciled_date=last,
    )


@router.post("/events", response_model=ReconciliationResponse, status_code=201)
async def create_event(
    body: ReconciliationCreate,
    db: AsyncSession = Depends(get_db),
) -> ReconciliationResponse:
    # Account must exist; FK would catch this but a 404 is friendlier than 500.
    acct = await db.execute(select(Account).where(Account.id == body.account_id))
    if acct.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="account not found")

    try:
        event = await save_event(db, body)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=422, detail=f"integrity error: {exc.orig}")
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=422, detail=str(exc))

    await db.refresh(event)
    # _rejected_txn_ids is a transient attribute set by save_event (not a
    # mapped column); read it explicitly because model_validate would discard it.
    rejected_ids: list[str] = getattr(event, "_rejected_txn_ids", [])
    return ReconciliationResponse(
        id=event.id,
        account_id=event.account_id,
        snapshot_date=event.snapshot_date,
        created_at=event.created_at,
        notes=event.notes,
        holdings_snapshot=event.holdings_snapshot,
        rejected_txn_ids=rejected_ids,
    )
