from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.account import Account
from app.models.reconciliation import Reconciliation
from app.schemas.account import AccountCreate, AccountResponse

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


@router.post("", response_model=AccountResponse, status_code=201)
async def create_account(body: AccountCreate, db: AsyncSession = Depends(get_db)):
    acct = Account(**body.model_dump())
    db.add(acct)
    await db.commit()
    await db.refresh(acct)
    return acct


@router.get("", response_model=list[AccountResponse])
async def list_accounts(db: AsyncSession = Depends(get_db)):
    # Pre-aggregate MAX(snapshot_date) per account in a
    # single query so each AccountResponse row carries last_reconciled_date
    # without a per-account N+1.
    last_recon_stmt = (
        select(
            Reconciliation.account_id,
            func.max(Reconciliation.snapshot_date).label("last_date"),
        )
        .group_by(Reconciliation.account_id)
    )
    last_recon_rows = (await db.execute(last_recon_stmt)).all()
    last_recon_map: dict[str, date] = {
        row.account_id: row.last_date for row in last_recon_rows
    }

    result = await db.execute(select(Account).order_by(Account.name))
    accounts = result.scalars().all()
    out: list[AccountResponse] = []
    for acct in accounts:
        resp = AccountResponse.model_validate(acct)
        resp.last_reconciled_date = last_recon_map.get(acct.id)
        out.append(resp)
    return out


@router.get("/{account_id}", response_model=AccountResponse)
async def get_account(account_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Account).where(Account.id == account_id))
    acct = result.scalar_one_or_none()
    if not acct:
        raise HTTPException(status_code=404, detail="Account not found")
    return acct


@router.put("/{account_id}", response_model=AccountResponse)
async def update_account(
    account_id: str, body: AccountCreate, db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(Account).where(Account.id == account_id))
    acct = result.scalar_one_or_none()
    if not acct:
        raise HTTPException(status_code=404, detail="Account not found")
    for field, value in body.model_dump().items():
        setattr(acct, field, value)
    await db.commit()
    await db.refresh(acct)
    return acct


@router.delete("/{account_id}", status_code=204)
async def delete_account(account_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Account).where(Account.id == account_id))
    acct = result.scalar_one_or_none()
    if not acct:
        raise HTTPException(status_code=404, detail="Account not found")
    await db.delete(acct)
    await db.commit()
