from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core import clock
from app.core.database import get_db
from app.core.enums import (
    ACQUISITION_TXN_TYPES,
    LOT_AFFECTING_TXN_TYPES,
    signed_quantity,
)
from app.models.account import Account
from app.models.instrument import Instrument
from app.models.lot_alloc import LotAlloc
from app.models.transaction import Transaction
from app.models.txn_audit import TxnAudit
from app.schemas.audit import AuditEvent
from app.schemas.transaction import (
    TransactionCreate,
    TransactionResponse,
    TransactionUpdate,
)
from app.services.audit import AUDITED_FIELDS, compute_field_diff, write_audit_event
from app.services.cost_basis import compute_cost_basis
from app.services.fifo import recompute_fifo_for_pair
from app.services.fx import FxUpstreamError, resolve_locked_fx_rate

router = APIRouter(prefix="/api/transactions", tags=["transactions"])


# Fields whose edit changes FIFO matching or lot economics: quantity/unit_price/fx
# feed realized gains, date feeds FIFO ordering, and price_currency re-locks
# fx_rate_to_eur when its value actually changes. A notes-only or fee-only edit
# skips the recompute.
_FIFO_RELEVANT_FIELDS = frozenset(
    {"quantity", "unit_price", "fx_rate_to_eur", "date", "price_currency"}
)


def _fx_upstream_502(exc: FxUpstreamError) -> HTTPException:
    return HTTPException(
        status_code=502,
        detail=(
            f"fx upstream error: {exc} — retry or supply "
            "fx_rate_to_eur explicitly"
        ),
    )


@router.post("", response_model=TransactionResponse, status_code=201)
async def create_transaction(body: TransactionCreate, db: AsyncSession = Depends(get_db)):
    # Note: sell is rejected at the Pydantic layer (linked trades are the only
    # entry path for sells), so signed_quantity never sees one here in practice.
    txn = Transaction(
        account_id=body.account_id,
        instrument_id=body.instrument_id,
        txn_type=body.txn_type,
        date=body.date,
        quantity=signed_quantity(body.txn_type, body.quantity),
        unit_price=body.unit_price,
        price_currency=body.price_currency,
        fx_rate_to_eur=body.fx_rate_to_eur,
        fee_eur=body.fee_eur,
        notes=body.notes,
        source=body.source or "manual",  # pass-through from TransactionCreate; default fallback
        reconciliation_id=body.reconciliation_id,
    )

    # Lock fx_rate_to_eur at write time (EUR-base rate, USD per 1 EUR,
    # cost_basis_eur = price / rate). See services/fx.resolve_locked_fx_rate.
    try:
        txn.fx_rate_to_eur = await resolve_locked_fx_rate(
            db, body.price_currency, body.date, body.fx_rate_to_eur
        )
    except FxUpstreamError as exc:
        await db.rollback()
        raise _fx_upstream_502(exc)

    txn.cost_basis_eur = compute_cost_basis(txn)
    db.add(txn)
    await db.flush()  # get txn.id before FIFO runs

    # Any lot-affecting row (a disposal that must consume lots, or a back-dated
    # lot source that changes which lots existing disposals should hold)
    # converges the whole pair to canonical FIFO. Insufficient lots raise
    # ValueError from the rematch.
    if body.txn_type in LOT_AFFECTING_TXN_TYPES:
        try:
            await recompute_fifo_for_pair(db, txn.account_id, txn.instrument_id)
        except ValueError as exc:
            await db.rollback()
            raise HTTPException(status_code=422, detail=str(exc))

    await db.commit()
    # Reload with lot_allocs eagerly
    result = await db.execute(
        select(Transaction)
        .where(Transaction.id == txn.id)
        .options(selectinload(Transaction.lot_allocs))
    )
    txn_out = result.scalar_one()
    resp = TransactionResponse.model_validate(txn_out)
    resp.lot_alloc_count = len(txn_out.lot_allocs)
    return resp


@router.get("", response_model=list[TransactionResponse])
async def list_transactions(
    account_id: Optional[str] = None,
    instrument_id: Optional[str] = None,
    txn_type: Optional[str] = None,
    include_deleted: bool = False,
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Transaction).options(selectinload(Transaction.lot_allocs))
    if not include_deleted:
        stmt = stmt.where(Transaction.deleted_at.is_(None))
    if account_id:
        stmt = stmt.where(Transaction.account_id == account_id)
    if instrument_id:
        stmt = stmt.where(Transaction.instrument_id == instrument_id)
    if txn_type:
        stmt = stmt.where(Transaction.txn_type == txn_type)
    stmt = stmt.order_by(Transaction.date.desc(), Transaction.created_at.desc())
    result = await db.execute(stmt)
    txns = result.scalars().all()

    # Compute lot_alloc_count per transaction
    txn_ids = [t.id for t in txns]
    sell_counts: dict[str, int] = {}
    buy_counts: dict[str, int] = {}
    if txn_ids:
        # Count lot_alloc rows by sell_txn_id
        sell_stmt = (
            select(LotAlloc.sell_txn_id, func.count(LotAlloc.id).label("cnt"))
            .where(LotAlloc.sell_txn_id.in_(txn_ids))
            .group_by(LotAlloc.sell_txn_id)
        )
        sell_result = await db.execute(sell_stmt)
        sell_counts = {row.sell_txn_id: row.cnt for row in sell_result}

        # Count lot_alloc rows by buy_txn_id
        buy_stmt = (
            select(LotAlloc.buy_txn_id, func.count(LotAlloc.id).label("cnt"))
            .where(LotAlloc.buy_txn_id.in_(txn_ids))
            .group_by(LotAlloc.buy_txn_id)
        )
        buy_result = await db.execute(buy_stmt)
        buy_counts = {row.buy_txn_id: row.cnt for row in buy_result}

    # Bulk-fetch account names and instrument symbols for the returned txns
    # so the ledger UI can render them without N+1 lookups.
    account_ids = {t.account_id for t in txns}
    instrument_ids = {t.instrument_id for t in txns}
    account_names: dict[str, str] = {}
    instrument_symbols: dict[str, str] = {}
    # Hydrate per-instrument context (type +
    # display_decimals override) so the txn-list table can format
    # quantity at the right precision without a second fetch.
    instrument_types: dict[str, str] = {}
    instrument_display_decimals: dict[str, int | None] = {}
    if account_ids:
        rows = await db.execute(
            select(Account.id, Account.name).where(Account.id.in_(account_ids))
        )
        account_names = {r.id: r.name for r in rows}
    if instrument_ids:
        rows = await db.execute(
            select(
                Instrument.id,
                Instrument.symbol,
                Instrument.instrument_type,
                Instrument.display_decimals,
            ).where(Instrument.id.in_(instrument_ids))
        )
        for r in rows:
            instrument_symbols[r.id] = r.symbol
            instrument_types[r.id] = r.instrument_type
            instrument_display_decimals[r.id] = r.display_decimals

    responses = []
    for txn in txns:
        resp = TransactionResponse.model_validate(txn)
        resp.lot_alloc_count = sell_counts.get(txn.id, 0) + buy_counts.get(txn.id, 0)
        resp.account_name = account_names.get(txn.account_id)
        resp.instrument_symbol = instrument_symbols.get(txn.instrument_id)
        resp.instrument_type = instrument_types.get(txn.instrument_id)
        resp.display_decimals = instrument_display_decimals.get(txn.instrument_id)
        responses.append(resp)
    return responses


@router.get("/{txn_id}/audit", response_model=list[AuditEvent])
async def get_audit_history(txn_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(TxnAudit)
        .where(TxnAudit.transaction_id == txn_id)
        .order_by(TxnAudit.changed_at.desc())
    )
    return result.scalars().all()


@router.get("/{txn_id}", response_model=TransactionResponse)
async def get_transaction(txn_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Transaction)
        .where(Transaction.id == txn_id)
        .where(Transaction.deleted_at.is_(None))
        .options(selectinload(Transaction.lot_allocs))
    )
    txn = result.scalar_one_or_none()
    if not txn:
        raise HTTPException(status_code=404, detail="Transaction not found")
    resp = TransactionResponse.model_validate(txn)
    resp.lot_alloc_count = len(txn.lot_allocs)
    return resp


@router.put("/{txn_id}", response_model=TransactionResponse)
async def update_transaction(
    txn_id: str, body: TransactionUpdate, db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(Transaction)
        .where(Transaction.id == txn_id)
        .options(selectinload(Transaction.lot_allocs))
    )
    txn = result.scalar_one_or_none()
    if not txn:
        raise HTTPException(status_code=404, detail="Transaction not found")

    # Cannot edit a tombstoned row
    if txn.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Transaction not found")

    update_data = body.model_dump(exclude_unset=True)

    # Prevent explicitly clearing the price fields on a buy/spend.
    # We only reject the explicit-null case (`{"unit_price": null}`), leaving
    # the field absent from the PUT body is fine, even on legacy rows that
    # already have nulls (you should be able to edit notes on a broken row
    # without being forced to fix every column at once).
    if txn.txn_type in ACQUISITION_TXN_TYPES:
        cleared = [
            field
            for field in ("unit_price", "price_currency")
            if field in update_data and update_data[field] is None
        ]
        if cleared:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Cannot clear {', '.join(cleared)} on a {txn.txn_type} — "
                    "set a value or delete the transaction."
                ),
            )

    # Mirror TransactionCreate.validate_quantity: quantity is user-facing and
    # always positive, the router infers the stored sign from txn_type.
    # Adjustment is the deliberate exception (reconciliation trims are
    # negative, top-ups positive, sign-flip edits are legitimate and
    # convergence-tested), so it skips this check entirely.
    if (
        "quantity" in update_data
        and update_data["quantity"] is not None
        and txn.txn_type != "adjustment"
        and update_data["quantity"] <= Decimal("0")
    ):
        raise HTTPException(
            status_code=422,
            detail="quantity must be positive (sign is inferred from txn_type)",
        )

    # Capture before-snapshot for audit diff (BEFORE mutating txn)
    before_snapshot: dict = {field: getattr(txn, field, None) for field in AUDITED_FIELDS}

    # Apply updates
    for field, value in update_data.items():
        if field == "quantity" and value is not None:
            setattr(txn, field, signed_quantity(txn.txn_type, value))
        else:
            setattr(txn, field, value)

    # FX re-lock when a PUT genuinely CHANGES price_currency. Editing an EUR row
    # (locked rate 1) to USD without supplying a rate would otherwise silently
    # turn a $100 price into a €100 cost basis. Gated on the value actually
    # changing so a same-currency re-send never touches the locked rate, and
    # skipped when the same PUT supplied fx_rate_to_eur explicitly (broker-rate
    # override, already applied above). A date-only edit also keeps the locked
    # rate: locked-at-transaction-time is the documented semantic.
    currency_changed = (
        "price_currency" in update_data
        and update_data["price_currency"] != before_snapshot["price_currency"]
    )
    if currency_changed and "fx_rate_to_eur" not in update_data:
        try:
            txn.fx_rate_to_eur = await resolve_locked_fx_rate(
                db, txn.price_currency, txn.date, None
            )
        except FxUpstreamError as exc:
            await db.rollback()
            raise _fx_upstream_502(exc)

    txn.cost_basis_eur = compute_cost_basis(txn)

    diff = compute_field_diff(before_snapshot, update_data)
    if diff:
        await write_audit_event(db, txn.id, "edit", diff)

    # Converge the pair when a lot-relevant field changed on a lot-affecting
    # txn. price_currency only counts when its value actually changed (see
    # currency_changed above): a no-op re-send mutates no lot economics.
    fifo_relevant_keys = (
        update_data.keys() if currency_changed else update_data.keys() - {"price_currency"}
    )
    if txn.txn_type in LOT_AFFECTING_TXN_TYPES and (
        _FIFO_RELEVANT_FIELDS & fifo_relevant_keys
    ):
        await db.flush()
        try:
            await recompute_fifo_for_pair(db, txn.account_id, txn.instrument_id)
        except ValueError as exc:
            await db.rollback()
            raise HTTPException(status_code=422, detail=str(exc))

    await db.commit()
    result = await db.execute(
        select(Transaction)
        .where(Transaction.id == txn.id)
        .options(selectinload(Transaction.lot_allocs))
    )
    txn_out = result.scalar_one()
    resp = TransactionResponse.model_validate(txn_out)
    resp.lot_alloc_count = len(txn_out.lot_allocs)
    return resp


@router.delete("/{txn_id}", status_code=204)
async def delete_transaction(txn_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Transaction).where(Transaction.id == txn_id))
    txn = result.scalar_one_or_none()
    if not txn:
        raise HTTPException(status_code=404, detail="Transaction not found")

    # Idempotent failure, second delete is invalid
    if txn.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Transaction not found")

    # Capture trade_pair_id before mutating
    pair_id = txn.trade_pair_id

    # Soft delete, set deleted_at
    txn.deleted_at = clock.now()

    # Write audit row for the delete
    await write_audit_event(
        db, txn.id, "delete", {"deleted_at": {"old": None, "new": "now"}}
    )

    # Converge the pair after the soft-delete (and after the paired row on a
    # linked-trade cascade): the recompute drops the deleted row's allocations
    # and rematches the survivors. Deleting a consumed buy can leave a sell
    # uncovered; that raises ValueError and the whole delete is rejected with
    # 422. Product decision: you cannot orphan a dependent sell, absorb it
    # with other lots or delete the sell first.
    try:
        if txn.txn_type in LOT_AFFECTING_TXN_TYPES:
            await db.flush()
            await recompute_fifo_for_pair(db, txn.account_id, txn.instrument_id)

        # Linked-pair cascade, soft-delete the paired transaction
        if pair_id is not None:
            pair_result = await db.execute(
                select(Transaction).where(
                    Transaction.trade_pair_id == pair_id,
                    Transaction.id != txn.id,
                    Transaction.deleted_at.is_(None),
                )
            )
            paired = pair_result.scalar_one_or_none()
            if paired is not None:
                paired.deleted_at = clock.now()
                await write_audit_event(
                    db,
                    paired.id,
                    "delete",
                    {"deleted_at": {"old": None, "new": "now"}},
                )
                if paired.txn_type in LOT_AFFECTING_TXN_TYPES:
                    await db.flush()
                    await recompute_fifo_for_pair(
                        db, paired.account_id, paired.instrument_id
                    )
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=422,
            detail=f"Cannot delete: remaining lots cannot cover existing sells ({exc})",
        )

    await db.commit()
