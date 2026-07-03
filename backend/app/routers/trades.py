import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.schemas.trade import TradeCreate, TradeResponse
from app.services.trades import FxUpstreamError, create_linked_trade

router = APIRouter(prefix="/api/trades", tags=["trades"])


@router.post("", response_model=TradeResponse, status_code=201)
async def create_trade(body: TradeCreate, db: AsyncSession = Depends(get_db)):
    async with httpx.AsyncClient() as fx_client:
        try:
            sell_txn, buy_txn = await create_linked_trade(
                db,
                fx_client,
                sold=body.sold,
                received=body.received,
                trade_date=body.date,
                notes=body.notes,
            )
        except FxUpstreamError as exc:
            await db.rollback()
            raise HTTPException(
                status_code=502,
                detail=(
                    f"fx upstream error: {exc} — retry or supply "
                    "fx_rate_to_eur explicitly on the USD leg"
                ),
            )
        except ValueError as exc:
            await db.rollback()
            raise HTTPException(status_code=422, detail=str(exc))

    await db.commit()
    return TradeResponse(
        trade_pair_id=sell_txn.trade_pair_id,
        sold_txn_id=sell_txn.id,
        received_txn_id=buy_txn.id,
    )
