import logging
from datetime import date
from decimal import Decimal

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.constants import ZERO
from app.core.database import get_db
from app.models.instrument import Instrument
from app.models.transaction import Transaction
from app.schemas.backfill import (
    BackfillPreviewResponse,
    BulkBackfillItem,
    BulkBackfillResponse,
)
from app.schemas.instrument import InstrumentCreate, InstrumentResponse
from app.schemas.tag import InstrumentHoldingResponse
from app.services.backfill import backfill_fx_history, backfill_instrument_history
from app.services.pricing.errors import PriceProviderRateLimited
from app.services.tags import list_holdings_for_instrument

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/instruments", tags=["instruments"])

# ZERO (zero-Decimal sentinel for the held=true SUM/HAVING clause) is imported
# from app.core.constants — single source of truth shared across services/routers.

# Shared predicate between the preview + bulk endpoints.
# `ft` and `manual` price sources require manual NAV entries, the service
# layer surfaces them with `status="manual_history_required"` rather than
# making a network call. Matches the gate at backend/app/services/backfill.py
# line ~50.
_SYNTHETIC_PRICE_SOURCES: frozenset[str] = frozenset({"ft", "manual"})


async def _earliest_first_txn_date(db: AsyncSession) -> date | None:
    """MIN(transaction.date) across instruments that CAN be backfilled
    (non-synthetic price_source) and that have at least one non-soft-deleted
    transaction. Returns None when no eligible instrument has any txn.

    Shared by `GET /backfill-preview` (so the dialog shows the user the
    earliest date the bulk loop will sweep from) and the bulk endpoint's
    one-shot FX window (so we hit Frankfurter once, not N times).
    """
    stmt = (
        select(func.min(Transaction.date))
        .join(Instrument, Instrument.id == Transaction.instrument_id)
        .where(
            Transaction.deleted_at.is_(None),
            Instrument.price_source.not_in(_SYNTHETIC_PRICE_SOURCES),
        )
    )
    return await db.scalar(stmt)


@router.post("", response_model=InstrumentResponse, status_code=201)
async def create_instrument(body: InstrumentCreate, db: AsyncSession = Depends(get_db)):
    inst = Instrument(**body.model_dump())
    db.add(inst)
    try:
        await db.commit()
    except IntegrityError as exc:
        # Duplicate (symbol, instrument_type) collides with the Instrument
        # UniqueConstraint. Catch it here for a clean 409 instead of leaking a
        # 500 IntegrityError; the catch (not a pre-check select) is the
        # race-safe guarantee. Mirrors backend/app/routers/tags.py.
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="An instrument with this symbol already exists.",
        ) from exc
    await db.refresh(inst)
    return inst


@router.get("", response_model=list[InstrumentResponse])
async def list_instruments(
    held: bool = False,
    db: AsyncSession = Depends(get_db),
):
    """List instruments.

    By default returns every instrument in the catalog (the historical
    behavior — every existing caller relies on this, notably the
    missing-price warning hint in NetWorthChart.tsx).

    With `?held=true`, narrows the response to instruments the user
    currently holds (summed transaction quantity across all accounts is
    non-zero, ignoring soft-deleted transactions). Used by
    `InstrumentMultiSelect` to keep closed positions out of the chart
    filter dropdown without affecting other consumers of the full list.
    """
    stmt = select(Instrument).order_by(Instrument.symbol)
    if held:
        # quantity is TEXT-backed (DecimalText) — a SQL SUM/HAVING would coerce
        # the text values to float, and float dust could keep a fully-closed
        # holding in the "held" set. Sum signed quantity per instrument in
        # Python and keep only those with a non-zero net.
        qty_rows = await db.execute(
            select(Transaction.instrument_id, Transaction.quantity).where(
                Transaction.deleted_at.is_(None)
            )
        )
        net_by_instrument: dict[str, Decimal] = {}
        for instrument_id, qty in qty_rows:
            net_by_instrument[instrument_id] = (
                net_by_instrument.get(instrument_id, ZERO) + qty
            )
        held_ids = [iid for iid, net in net_by_instrument.items() if net != ZERO]
        stmt = stmt.where(Instrument.id.in_(held_ids))
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/backfill-preview", response_model=BackfillPreviewResponse)
async def backfill_preview(db: AsyncSession = Depends(get_db)):
    """Summary counts for the bulk-backfill confirmation dialog.

    - `eligible_count` — instruments with a non-synthetic price source AND
      at least one non-soft-deleted transaction. These are the rows the bulk
      loop will actually call out to an upstream price provider for.
    - `synthetic_count` — instruments with `price_source ∈ {ft, manual}`
      AND at least one transaction. Surfaced verbatim by the bulk endpoint
      as `manual_history_required`; the dialog mentions them so the user
      knows they'll be skipped automatically.
    - Instruments with zero transactions are in NEITHER bucket — they
      would no-op as `no_transactions` if the bulk loop ran.
    - `estimated_api_calls = eligible_count` — one TwelveData/Binance call
      per eligible instrument is the floor. FX calls are folded into a
      single Frankfurter range request after the loop so they're not in
      the estimate.
    """
    # eligible_count: instruments with a non-synthetic price_source that
    # have at least one non-soft-deleted transaction.
    eligible_stmt = (
        select(func.count(func.distinct(Instrument.id)))
        .join(Transaction, Transaction.instrument_id == Instrument.id)
        .where(
            Transaction.deleted_at.is_(None),
            Instrument.price_source.not_in(_SYNTHETIC_PRICE_SOURCES),
        )
    )
    eligible_count = (await db.scalar(eligible_stmt)) or 0

    synthetic_stmt = (
        select(func.count(func.distinct(Instrument.id)))
        .join(Transaction, Transaction.instrument_id == Instrument.id)
        .where(
            Transaction.deleted_at.is_(None),
            Instrument.price_source.in_(_SYNTHETIC_PRICE_SOURCES),
        )
    )
    synthetic_count = (await db.scalar(synthetic_stmt)) or 0

    earliest = await _earliest_first_txn_date(db)
    return BackfillPreviewResponse(
        eligible_count=eligible_count,
        synthetic_count=synthetic_count,
        earliest_first_txn_date=earliest,
        estimated_api_calls=eligible_count,
    )


@router.post("/backfill-all", response_model=BulkBackfillResponse)
async def trigger_bulk_backfill(db: AsyncSession = Depends(get_db)):
    """Iterate every instrument, calling the existing per-instrument backfill
    service. Per-instrument commit boundaries mean a late failure does not
    roll back earlier successes; rate-limit on one instrument is logged and
    surfaced via `status='rate_limited'` while the loop continues.

    Always returns 200 — the batch itself succeeds even when individual
    instruments error. `rate_limited_count` exposes the per-item failures
    so the FE can toast a warning summary.
    """
    # Pull the ids/symbols once — we deliberately do NOT keep the ORM
    # instances around for the loop body. A rollback inside the loop expires
    # every attached instance; the next iteration's first attribute read
    # then triggers a sync lazy-load under the asyncio engine, which raises
    # MissingGreenlet. Re-fetching the Instrument inside each iteration
    # keeps every access well-defined.
    id_symbol_rows = (
        await db.execute(
            select(Instrument.id, Instrument.symbol).order_by(Instrument.symbol)
        )
    ).all()

    items: list[BulkBackfillItem] = []
    total_inserted = 0
    rate_limited = 0
    end = clock.today()

    async with httpx.AsyncClient() as client:
        for inst_id, inst_symbol in id_symbol_rows:
            # Re-fetch the ORM instance per iteration so the service layer
            # (which reads `.price_source`, `.base_currency`, `.ticker_override`,
            # `.symbol`) always sees a fresh, non-expired object.
            inst = (
                await db.execute(
                    select(Instrument).where(Instrument.id == inst_id)
                )
            ).scalar_one()
            first_txn_date = await db.scalar(
                select(func.min(Transaction.date)).where(
                    Transaction.instrument_id == inst_id,
                    Transaction.deleted_at.is_(None),
                )
            )
            if first_txn_date is None:
                items.append(
                    BulkBackfillItem(
                        instrument_id=inst_id,
                        symbol=inst_symbol,
                        status="no_transactions",
                        inserted_prices=0,
                        skipped_existing=0,
                    )
                )
                continue
            try:
                result = await backfill_instrument_history(
                    db, client, inst, first_txn_date, end
                )
                items.append(
                    BulkBackfillItem(
                        instrument_id=inst_id,
                        symbol=inst_symbol,
                        status=result.status,
                        inserted_prices=result.inserted_prices,
                        skipped_existing=result.skipped_existing,
                    )
                )
                total_inserted += result.inserted_prices
                # Per-instrument commit boundary preserves earlier
                # successes when a later instrument errors.
                await db.commit()
            except PriceProviderRateLimited as e:
                await db.rollback()
                logger.warning(
                    "bulk_backfill_rate_limited instrument=%s err=%s",
                    inst_id,
                    e,
                )
                items.append(
                    BulkBackfillItem(
                        instrument_id=inst_id,
                        symbol=inst_symbol,
                        status="rate_limited",
                        inserted_prices=0,
                        skipped_existing=0,
                    )
                )
                rate_limited += 1
                continue
            except ValueError as e:
                await db.rollback()
                logger.warning(
                    "bulk_backfill_failed instrument=%s err=%s",
                    inst_id,
                    e,
                )
                items.append(
                    BulkBackfillItem(
                        instrument_id=inst_id,
                        symbol=inst_symbol,
                        status="failed",
                        inserted_prices=0,
                        skipped_existing=0,
                    )
                )
                continue

        # One FX backfill across the global window. If no instrument had any
        # eligible transactions, skip the call and leave the FX count at 0.
        total_fx = 0
        global_earliest = await _earliest_first_txn_date(db)
        if global_earliest is not None:
            try:
                total_fx = await backfill_fx_history(
                    db, client, global_earliest, end
                )
                await db.commit()
            except (PriceProviderRateLimited, ValueError) as e:
                await db.rollback()
                logger.warning("bulk_backfill_fx_failed err=%s", e)

    return BulkBackfillResponse(
        items=items,
        total_inserted_prices=total_inserted,
        total_inserted_fx_rates=total_fx,
        rate_limited_count=rate_limited,
    )


@router.get("/{instrument_id}", response_model=InstrumentResponse)
async def get_instrument(instrument_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Instrument).where(Instrument.id == instrument_id))
    inst = result.scalar_one_or_none()
    if not inst:
        raise HTTPException(status_code=404, detail="Instrument not found")
    return inst


@router.put("/{instrument_id}", response_model=InstrumentResponse)
async def update_instrument(
    instrument_id: str, body: InstrumentCreate, db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(Instrument).where(Instrument.id == instrument_id))
    inst = result.scalar_one_or_none()
    if not inst:
        raise HTTPException(status_code=404, detail="Instrument not found")
    for field, value in body.model_dump(exclude={"id"}).items():
        setattr(inst, field, value)
    try:
        await db.commit()
    except IntegrityError as exc:
        # Same guard as create: renaming to a colliding (symbol, instrument_type)
        # returns a clean 409 rather than a 500.
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="An instrument with this symbol already exists.",
        ) from exc
    await db.refresh(inst)
    return inst


@router.delete("/{instrument_id}", status_code=204)
async def delete_instrument(instrument_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Instrument).where(Instrument.id == instrument_id))
    inst = result.scalar_one_or_none()
    if not inst:
        raise HTTPException(status_code=404, detail="Instrument not found")

    # BLOCK pre-check: transactions are the one instrument_id FK we must never
    # silently destroy. FIFO lots, realized gains, and cost basis all hang off
    # them. Count ALL physical rows including soft-deleted ones (a referencing
    # row is a referencing row regardless of soft-delete; soft-deleting is not
    # deleting, and the FK is physical).
    txn_count = await db.scalar(
        select(func.count())
        .select_from(Transaction)
        .where(Transaction.instrument_id == instrument_id)
    )
    if txn_count and txn_count > 0:
        raise HTTPException(
            status_code=409,
            detail=(
                "Cannot delete: transactions still reference this instrument. "
                "Delete those transactions first."
            ),
        )

    # The four instrument-owned child FKs (price_quote, apy_config, holding_tag,
    # concentration_mute) carry ON DELETE CASCADE, so the DB removes them during
    # the delete commit. The try/except is the belt-and-suspenders guard so any
    # FK we did not classify can never surface as a 500, mirrors the
    # create_instrument IntegrityError->409 idiom above.
    await db.delete(inst)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail=(
                "Cannot delete: this instrument is still referenced. "
                "Remove the referencing records first."
            ),
        ) from exc


@router.post("/{instrument_id}/backfill", status_code=202)
async def trigger_instrument_backfill(
    instrument_id: str, db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(Instrument).where(Instrument.id == instrument_id))
    instrument = result.scalar_one_or_none()
    if not instrument:
        raise HTTPException(status_code=404, detail="Instrument not found")

    first_txn_date = await db.scalar(
        select(func.min(Transaction.date)).where(
            Transaction.instrument_id == instrument_id
        )
    )
    if first_txn_date is None:
        return {
            "instrument_id": instrument_id,
            "status": "no_transactions",
            "inserted_prices": 0,
            "skipped_existing": 0,
        }

    end = clock.today()
    try:
        async with httpx.AsyncClient() as client:
            price_result = await backfill_instrument_history(
                db, client, instrument, first_txn_date, end
            )
            inserted_fx_rates = await backfill_fx_history(db, client, first_txn_date, end)
        await db.commit()
    except PriceProviderRateLimited as e:
        # Still don't leak provider names, but the *kind* of failure
        # (rate-limit) is a generic state worth surfacing so the user knows
        # to retry later instead of treating it as a permanent failure.
        await db.rollback()
        logger.warning(
            "backfill_rate_limited instrument=%s err=%s", instrument_id, e
        )
        raise HTTPException(
            status_code=429,
            detail="Price provider rate limit hit — try again in a few minutes.",
        ) from None
    except ValueError as e:
        # Log full detail server-side; return a generic message so we
        # don't leak provider names, request shapes, or symbol-to-id mappings
        # to API consumers.
        await db.rollback()
        logger.warning(
            "backfill_failed instrument=%s err=%s", instrument_id, e
        )
        raise HTTPException(status_code=502, detail="Backfill failed") from None

    return {
        "instrument_id": instrument_id,
        "status": price_result.status,
        "inserted_prices": price_result.inserted_prices,
        "skipped_existing": price_result.skipped_existing,
        "inserted_fx_rates": inserted_fx_rates,
    }


@router.get("/{instrument_id}/holdings", response_model=list[InstrumentHoldingResponse])
async def get_instrument_holdings(
    instrument_id: str, db: AsyncSession = Depends(get_db)
):
    """List the (account, instrument) pairs the user holds for this instrument.

    Each element carries the tags currently attached to that pair. Returns
    an empty list (not 404) when the user has never recorded a non-deleted
    transaction for this instrument — the FE renders an empty-state
    explanation rather than treating the absence as an error.

    Consumed by the frontend HoldingTagsSection on /instruments/[id].
    Tags filter every dashboard when applied via
    the header chip; this route returns the truth-of-record for which tags
    are attached to which (account, instrument) pair.
    """
    rows = await list_holdings_for_instrument(db, instrument_id)
    # rows is already a list[dict] matching InstrumentHoldingResponse shape;
    # Pydantic validates on serialization via response_model.
    return rows
