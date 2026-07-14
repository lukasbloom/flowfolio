from decimal import Decimal
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete as sql_delete
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core import clock
from app.core.database import get_db
from app.core.enums import ACQUISITION_TXN_TYPES, DISPOSAL_TXN_TYPES
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
from app.services.audit import AUDITED_FIELDS, _stringify, write_audit_event
from app.services.cost_basis import compute_cost_basis as _compute_cost_basis
from app.services.fifo import (
    delete_lot_allocs_for_sell,
    match_lots_for_sell,
    recompute_fifo_for_pair,
)
from app.services.fifo_convergence import recompute_pair_if_competing_disposal
from app.services.fx import get_or_fetch_fx_rate

router = APIRouter(prefix="/api/transactions", tags=["transactions"])


def _compute_diff_from_snapshots(
    before: dict, after_payload: dict
) -> dict:
    """Compute field-level diff from two plain dicts.

    Only fields present in both AUDITED_FIELDS and after_payload are compared.
    Used in the PUT handler where txn has already been mutated before diff computation.
    """
    diff: dict = {}
    for field in AUDITED_FIELDS:
        if field not in after_payload:
            continue
        before_val = before.get(field)
        after_val = after_payload[field]
        if _stringify(before_val) != _stringify(after_val):
            diff[field] = {"old": _stringify(before_val), "new": _stringify(after_val)}
    return diff


# Fields whose edit changes FIFO matching or lot economics: quantity/unit_price/fx
# feed realized gains, date feeds FIFO ordering, and price_currency triggers the
# FX re-lock in update_transaction, which mutates fx_rate_to_eur, when the edit
# actually changes its value. update_transaction only counts price_currency here
# when the value differs from the pre-update one, since a no-op re-send doesn't
# touch fx_rate_to_eur and so has nothing to recompute. A notes-only or fee-only
# edit skips recompute.
_FIFO_RELEVANT_FIELDS = frozenset(
    {"quantity", "unit_price", "fx_rate_to_eur", "date", "price_currency"}
)


async def _owns_sell_side_allocs(db: AsyncSession, txn_id: str) -> bool:
    """True when this txn consumed open lots and so owns sell-side LotAlloc rows.

    A sell, a spend, and a DOWNWARD (negative) adjustment all consume lots, so
    their allocs are keyed by sell_txn_id. Selecting by ownership rather than by
    txn_type or quantity sign keeps release robust when an adjustment's quantity
    sign flips (its role changes but its stale allocs must still be cleared).
    """
    row = await db.execute(
        select(LotAlloc.id).where(LotAlloc.sell_txn_id == txn_id).limit(1)
    )
    return row.scalar_one_or_none() is not None


async def _release_and_recompute_for_deleted(
    db: AsyncSession, txn: Transaction
) -> None:
    """Release the lot allocations a just-soft-deleted txn is involved in and
    re-run FIFO for its pair.

    A txn that consumed lots (a sell, a spend, or a downward adjustment) releases
    its own sell-side allocs and then re-matches the whole pair so later
    disposals move onto the lots it freed (plan 015). A buy or an upward
    adjustment that later sells consumed releases those buy-side allocs and
    re-matches the pair. If the remaining open lots can no longer cover those
    sells, recompute_fifo_for_pair raises ValueError (caller maps to 422). A
    disposal delete only ever grows availability, so its recompute cannot uncover
    a remaining sell.
    """
    if await _owns_sell_side_allocs(db, txn.id):
        # Free this disposal's lots, then converge the pair to canonical FIFO so
        # later disposals rematch onto the freed lots instead of keeping their
        # newer-lot attribution.
        await delete_lot_allocs_for_sell(db, txn.id)
        await db.flush()
        await recompute_fifo_for_pair(db, txn.account_id, txn.instrument_id)
        return
    if txn.txn_type in ("buy", "adjustment"):
        consumed = await db.execute(
            select(LotAlloc.id).where(LotAlloc.buy_txn_id == txn.id).limit(1)
        )
        if consumed.scalar_one_or_none() is not None:
            await db.execute(
                sql_delete(LotAlloc).where(LotAlloc.buy_txn_id == txn.id)
            )
            await db.flush()
            await recompute_fifo_for_pair(db, txn.account_id, txn.instrument_id)


@router.post("", response_model=TransactionResponse, status_code=201)
async def create_transaction(body: TransactionCreate, db: AsyncSession = Depends(get_db)):
    # Sign convention: buy/yield/adjustment are positive; sell/spend are negative (consume lots)
    # Note: sell is rejected at Pydantic layer so it never reaches here in practice.
    signed_qty = -body.quantity if body.txn_type in DISPOSAL_TXN_TYPES else body.quantity

    txn = Transaction(
        account_id=body.account_id,
        instrument_id=body.instrument_id,
        txn_type=body.txn_type,
        date=body.date,
        quantity=signed_qty,
        unit_price=body.unit_price,
        price_currency=body.price_currency,
        fx_rate_to_eur=body.fx_rate_to_eur,
        fee_eur=body.fee_eur,
        notes=body.notes,
        source=body.source or "manual",  # pass-through from TransactionCreate; default fallback
        reconciliation_id=body.reconciliation_id,
    )

    # FX auto-lock:
    # fx_rate_to_eur stores the EUR-base rate (USD per 1 EUR), e.g. 1.0512.
    # cost_basis_eur = price_USD / fx_rate_to_eur (NOT × fx_rate_to_eur).
    # See backend/app/models/transaction.py.
    if body.price_currency == "USD" and body.fx_rate_to_eur is None:
        # No explicit rate: fetch from Frankfurter and lock immutably on this row
        async with httpx.AsyncClient() as fx_client:
            try:
                fx_row = await get_or_fetch_fx_rate(
                    db, fx_client, body.date, base="EUR", quote="USD"
                )
            except ValueError as exc:
                await db.rollback()
                raise HTTPException(
                    status_code=502,
                    detail=(
                        f"fx upstream error: {exc} — retry or supply "
                        "fx_rate_to_eur explicitly"
                    ),
                )
        txn.fx_rate_to_eur = fx_row.rate
    elif body.price_currency == "USD" and body.fx_rate_to_eur is not None:
        # User supplied explicit rate (broker markup, also EUR-base, USD per
        # 1 EUR). Warm the fx_rate cache for history but do NOT
        # overwrite the txn's locked rate. Cache warming is best-effort.
        async with httpx.AsyncClient() as fx_client:
            try:
                await get_or_fetch_fx_rate(
                    db, fx_client, body.date, base="EUR", quote="USD"
                )
            except ValueError:
                pass
    elif body.price_currency == "EUR":
        # Identity rate; never call Frankfurter for EUR↔EUR
        txn.fx_rate_to_eur = Decimal("1")

    txn.cost_basis_eur = _compute_cost_basis(txn)
    db.add(txn)
    await db.flush()  # get txn.id before FIFO runs

    # Spend transactions consume lots same as a sell (already signed negative above)
    if body.txn_type == "spend":
        try:
            await match_lots_for_sell(db, txn)
        except ValueError as exc:
            await db.rollback()
            raise HTTPException(status_code=422, detail=str(exc))

    # Canonical FIFO convergence on the create path (plan 015). A back-dated buy
    # or upward adjustment can precede lots that existing disposals already
    # consumed, and a back-dated disposal self-matches against availability that
    # other disposals' allocs contaminate. Both change attribution, so re-match
    # the whole pair in canonical (date asc, created_at asc) order whenever the
    # new row is lot-affecting AND any OTHER disposal (sell/spend) or downward
    # adjustment exists on the pair. There is no date bound: match_lots_for_sell
    # ignores lot dates, so an EARLIER-dated disposal can hold a lot dated on or
    # after the new row too. Adding a lot or clearing a self-match contaminant
    # never uncovers a sell, so a create that succeeded above cannot start 422ing
    # here.
    lot_affecting = body.txn_type in (DISPOSAL_TXN_TYPES | {"buy", "adjustment"})
    if lot_affecting:
        try:
            await recompute_pair_if_competing_disposal(
                db, txn.account_id, txn.instrument_id, txn.id
            )
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

    # Capture before-snapshot for audit diff (BEFORE mutating txn)
    before_snapshot: dict = {field: getattr(txn, field, None) for field in AUDITED_FIELDS}

    # Apply updates
    for field, value in update_data.items():
        if field == "quantity" and value is not None:
            # Maintain sign convention for sell/spend
            signed = value if txn.txn_type not in DISPOSAL_TXN_TYPES else -value
            setattr(txn, field, signed)
        else:
            setattr(txn, field, value)

    # FX re-lock: mirror create_transaction's auto-lock when a PUT genuinely
    # CHANGES price_currency. Editing an existing EUR row (fx_rate_to_eur=1) to
    # USD without supplying a rate would otherwise leave the locked rate at 1,
    # silently turning e.g. a $100 price into a €100 cost basis. Gated on the
    # value actually changing, not just the key being present, so a client
    # re-sending the same currency unchanged leaves the locked-at-transaction-time
    # rate alone and never calls Frankfurter.
    currency_changed = (
        "price_currency" in update_data
        and update_data["price_currency"] != before_snapshot["price_currency"]
    )
    if currency_changed:
        if txn.price_currency == "EUR" and "fx_rate_to_eur" not in update_data:
            # Identity rate, never call Frankfurter for EUR<->EUR
            txn.fx_rate_to_eur = Decimal("1")
        elif txn.price_currency == "USD" and "fx_rate_to_eur" not in update_data:
            # No explicit rate: fetch from Frankfurter and lock immutably on this row
            async with httpx.AsyncClient() as fx_client:
                try:
                    fx_row = await get_or_fetch_fx_rate(
                        db, fx_client, txn.date, base="EUR", quote="USD"
                    )
                except ValueError as exc:
                    await db.rollback()
                    raise HTTPException(
                        status_code=502,
                        detail=(
                            f"fx upstream error: {exc} — retry or supply "
                            "fx_rate_to_eur explicitly"
                        ),
                    )
            txn.fx_rate_to_eur = fx_row.rate
        # else: the same PUT supplied fx_rate_to_eur explicitly (broker-rate
        # override). The apply-updates loop above already stored it verbatim.
    # If price_currency is untouched, leave the locked rate alone even when
    # date changes. Locked-at-transaction-time is the documented semantic,
    # and silently re-fetching on a date edit would overwrite a deliberate
    # broker rate.

    txn.cost_basis_eur = _compute_cost_basis(txn)

    # Compute diff using before snapshot vs the incoming update_data
    # compare_payload maps AUDITED_FIELDS present in update_data against before values
    diff = _compute_diff_from_snapshots(before_snapshot, update_data)
    if diff:
        await write_audit_event(db, txn.id, "edit", diff)

    # Recompute FIFO when a lot-affecting field changed on a lot-bearing txn:
    # disposals consume lots, buys/adjustments ARE the lots. The recompute runs
    # pair-wide (a sell can consume a buy dated after it, so per-row rematching
    # is order-sensitive) and replaces the old self-only match_lots_for_sell.
    is_sell_now = txn.txn_type in DISPOSAL_TXN_TYPES
    affects_lots = is_sell_now or txn.txn_type in ("buy", "adjustment")
    # price_currency only feeds FIFO here when its value actually changed (see
    # currency_changed above): a no-op re-send mutates no lot economics and
    # would otherwise trigger a pointless full-pair recompute.
    fifo_relevant_keys = (
        update_data.keys() if currency_changed else update_data.keys() - {"price_currency"}
    )
    if affects_lots and (_FIFO_RELEVANT_FIELDS & fifo_relevant_keys):
        await db.flush()
        # A downward adjustment edited into a positive top-up flips from a lot
        # CONSUMER to a lot SOURCE. recompute_fifo_for_pair only clears and
        # rematches rows that are STILL disposals, so it skips this row and would
        # leave its trim-era sell-side allocs in place, then rematch every other
        # disposal against that contaminated availability (plan 015 flip-ordering
        # fix). Clear those stale allocs BEFORE the recompute so the pair
        # converges to canonical FIFO and the same PUT stays idempotent.
        if (
            txn.txn_type == "adjustment"
            and txn.quantity >= Decimal("0")
            and await _owns_sell_side_allocs(db, txn.id)
        ):
            await delete_lot_allocs_for_sell(db, txn.id)
            await db.flush()
        # The pair recompute clears and rematches every row that stays a disposal,
        # so the old unconditional pre-release was redundant (plan 015 drops it).
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

    # Release lot allocations and recompute FIFO for the deleted row (and the
    # paired row on a linked-trade cascade). Deleting a consumed buy re-matches
    # the pair; if the remaining lots cannot cover its sells the whole delete is
    # rejected with 422 and rolled back. Product decision: you cannot orphan
    # a dependent sell, absorb it with other lots or delete the sell first.
    try:
        await _release_and_recompute_for_deleted(db, txn)

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
                await _release_and_recompute_for_deleted(db, paired)
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=422,
            detail=f"Cannot delete: remaining lots cannot cover existing sells ({exc})",
        )

    await db.commit()
