"""Reconciliation service.

Owns the only system path (besides accrual) that writes Transaction rows
bypassing the manual-create Pydantic guard. The guard at
backend/app/schemas/transaction.py:36-42 explicitly rejects
txn_type IN {"yield","adjustment"} from manual API callers; reconciliation
writes via SQLAlchemy ORM directly.

Caller-commits convention: this service stages reconciliation rows and
adjustment txns via session.add() + session.flush(). The router commits
on success.
"""
from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from typing import Optional

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import ZERO
from app.core.enums import DISPOSAL_TXN_TYPES
from app.models.instrument import Instrument
from app.models.reconciliation import Reconciliation
from app.models.transaction import Transaction
from app.schemas.reconciliation import (
    ReconciliationCreate,
    ReconciliationPreviewRow,
)
from app.services.fifo import recompute_fifo_for_pair
from app.services.fx import get_or_fetch_fx_rate
from app.services.networth import _fx_on_or_before, _load_fx, _load_quotes
from app.services.quotes import MissingFxRateError
from app.services.quotes import quote_on_or_before as _quote_on_or_before

logger = logging.getLogger(__name__)


async def _write_adjustment_txn(
    session: AsyncSession,
    *,
    account_id: str,
    instrument_id: str,
    snapshot_date: date,
    delta_qty: Decimal,
    reconciliation_id: str,
    notes: str,
) -> Transaction:
    """ORM-direct adjustment write. Mirrors services/accrual.py:206-219.

    Adjustments carry no price/FX/cost-basis (broker drift has no observable
    price). Quantity is the SIGNED delta — positive top-up, negative trim,
    zero dismiss.
    """
    txn = Transaction(
        account_id=account_id,
        instrument_id=instrument_id,
        txn_type="adjustment",
        date=snapshot_date,
        quantity=delta_qty,
        unit_price=None,
        price_currency=None,
        fx_rate_to_eur=None,
        cost_basis_eur=None,
        fee_eur=ZERO,
        notes=notes,
        source="adjustment",
        reconciliation_id=reconciliation_id,
    )
    session.add(txn)
    await session.flush()
    return txn


async def build_preview(
    session: AsyncSession,
    account_id: str,
    snapshot_date: date,
) -> list[ReconciliationPreviewRow]:
    """Return one row per non-zero holding in `account_id` as of
    `snapshot_date` (inclusive). Each row carries app_qty plus EUR-value
    derived via networth-service price/FX-as-of helpers.
    """
    # 1. Sum signed qty per instrument through snapshot_date. quantity is
    #    TEXT-backed (DecimalText) — sum and the !=ZERO open-set HAVING move to
    #    Python; a SQL SUM/HAVING would do float arithmetic on the text values.
    raw_qty_stmt = (
        select(Transaction.instrument_id, Transaction.quantity)
        .where(
            Transaction.account_id == account_id,
            Transaction.deleted_at.is_(None),
            Transaction.date <= snapshot_date,
        )
    )
    qty_by_instrument: dict[str, Decimal] = {}
    for instrument_id, qty in await session.execute(raw_qty_stmt):
        qty_by_instrument[instrument_id] = qty_by_instrument.get(instrument_id, ZERO) + qty
    # Open-set: drop holdings that net to exactly zero (the closed positions).
    qty_rows = [
        (instrument_id, total)
        for instrument_id, total in qty_by_instrument.items()
        if total != ZERO
    ]
    if not qty_rows:
        return []

    instrument_ids = [instrument_id for instrument_id, _ in qty_rows]

    # 2. Load instrument metadata.
    inst_stmt = select(Instrument).where(Instrument.id.in_(instrument_ids))
    instruments = {
        inst.id: inst
        for inst in (await session.execute(inst_stmt)).scalars().all()
    }

    # 3. Load price quotes + FX rates as-of snapshot_date.
    # _load_quotes returns dict[str, list[PriceQuote]] keyed by instrument_id.
    # _load_fx returns dict[date, Decimal] of EUR-base rates (USD per 1 EUR).
    quotes_by_instrument = await _load_quotes(session, snapshot_date)
    fx_by_date = await _load_fx(session, snapshot_date)

    # 4. Build rows.
    rows: list[ReconciliationPreviewRow] = []
    for instrument_id, total_qty in qty_rows:
        inst = instruments.get(instrument_id)
        if inst is None:
            continue
        # total_qty is already an exact Decimal from the Python sum above.
        qty = total_qty
        # _quote_on_or_before returns PriceQuote | None.
        quote = _quote_on_or_before(
            quotes_by_instrument.get(inst.id, []), snapshot_date
        )
        value_eur: Optional[Decimal] = None
        has_price = quote is not None and quote.price > ZERO
        # Instrument.base_currency is the field exposed on the model;
        # the preview schema names it `price_currency` for parity with the
        # transaction column.
        price_ccy = inst.base_currency
        if has_price:
            assert quote is not None  # mypy: narrowed by has_price
            if price_ccy == "EUR":
                value_eur = qty * quote.price
            else:
                # _fx_on_or_before raises MissingFxRateError when no rate
                # exists at or before the snapshot date. Treat that as a
                # missing-price condition for the row rather than 500ing
                # the whole preview.
                try:
                    fx = _fx_on_or_before(fx_by_date, snapshot_date)
                except MissingFxRateError:
                    has_price = False
                    fx = None
                if fx is not None and fx > ZERO:
                    # fx is EUR-base (USD per 1 EUR). EUR-value = qty * price_USD / fx.
                    value_eur = qty * quote.price / fx
                else:
                    has_price = False
        rows.append(
            ReconciliationPreviewRow(
                instrument_id=inst.id,
                instrument_symbol=inst.symbol,
                instrument_name=inst.name,
                instrument_type=inst.instrument_type,
                display_decimals=inst.display_decimals,
                price_currency=price_ccy,
                app_qty=qty,
                app_value_eur=value_eur,
                has_price=has_price,
            )
        )
    rows.sort(key=lambda row: row.instrument_symbol)
    return rows


async def _current_qty_map(
    session: AsyncSession, account_id: str
) -> dict[str, Decimal]:
    """Sum signed quantity per instrument across the FULL history for an
    account (no date filter), excluding soft-deleted rows.

    Used by save_event to derive the adjustment delta against the user's
    current app-side position. The snapshot_date on a Reconciliation event
    is the EFFECTIVE date of the adjustment (it determines FIFO ordering
    via recompute_fifo_for_pair when negative); it is not an
    as-of-date for the diff. The user types the broker's CURRENT qty into
    the form, and the adjustment must close the gap between current
    app-state and that broker value — not the gap between the
    snapshot-date app-state and that broker value.
    """
    # quantity is TEXT-backed — sum the signed quantities per instrument in
    # Python (a SQL SUM would coerce the text values back to float).
    stmt = select(Transaction.instrument_id, Transaction.quantity).where(
        Transaction.account_id == account_id,
        Transaction.deleted_at.is_(None),
    )
    result = await session.execute(stmt)
    qty_map: dict[str, Decimal] = {}
    for instrument_id, qty in result:
        qty_map[instrument_id] = qty_map.get(instrument_id, ZERO) + qty
    return qty_map


async def save_event(
    session: AsyncSession,
    payload: ReconciliationCreate,
) -> Reconciliation:
    """Atomically stage a reconciliation event and process all decisions.

    Caller commits. On rollback, all reconciliation + adjustment rows are
    discarded (single SQLAlchemy transaction).

    Decimal-purity invariant: delta_qty is derived server-side as
    snapshot_qty − app_qty using Python `decimal`. The DriftDecision schema
    does NOT carry delta_qty; the client only declares the action.

    `app_qty` for delta computation is the CURRENT signed-sum across the
    full history (see `_current_qty_map`), NOT a date-filtered view.
    `build_preview` (the diff editor) does filter by snapshot_date for the
    EUR-value column, but the delta math uses current state so an
    accept-drift correctly closes the gap between the broker's reported
    qty and the user's current app-state.
    """
    # Build snapshot_qty lookup from the typed holdings array.
    snapshot_map: dict[str, Decimal] = {
        h.instrument_id: h.snapshot_qty for h in payload.holdings
    }
    # app-qty derived from current signed-sum (no date filter).
    app_qty_map = await _current_qty_map(session, payload.account_id)

    event = Reconciliation(
        account_id=payload.account_id,
        snapshot_date=payload.snapshot_date,
        notes=payload.notes,
        holdings_snapshot=[h.model_dump(mode="json") for h in payload.holdings],
    )
    session.add(event)
    await session.flush()  # populate event.id

    for decision in payload.decisions:
        if decision.action == "matched":
            continue
        # Decimal arithmetic only — no float conversion anywhere on this line.
        snapshot_qty = snapshot_map.get(decision.instrument_id, ZERO)
        app_qty = app_qty_map.get(decision.instrument_id, ZERO)
        delta_qty = snapshot_qty - app_qty

        if decision.action == "accept":
            # A no-op accept (snapshot == app) would otherwise persist a
            # 0-qty adjustment row that pollutes the txn list and audit export
            # without changing balances. Skip the write entirely; the
            # reconciliation event itself already records the user's review.
            if delta_qty == ZERO:
                continue
            await _write_adjustment_txn(
                session,
                account_id=payload.account_id,
                instrument_id=decision.instrument_id,
                snapshot_date=payload.snapshot_date,
                delta_qty=delta_qty,
                reconciliation_id=event.id,
                notes=(
                    f"reconciliation {payload.snapshot_date.isoformat()}: "
                    f"app {app_qty} → actual {snapshot_qty}"
                ),
            )
            # Re-match the whole pair in canonical FIFO order (plan 015). The old
            # snapshot_date-scoped recompute only re-ran disposals dated on or
            # after the snapshot, so a back-dated adjustment that changes which
            # lot an EARLIER sell should draw from left that sell on stale
            # attribution. A pair-wide recompute is the only sound scope.
            await recompute_fifo_for_pair(
                session,
                payload.account_id,
                decision.instrument_id,
            )
        elif decision.action == "dismiss":
            # Distinguish None (no reason field at all) from "" (user opened
            # the dialog and confirmed without typing). Collapsing both to the
            # same fallback would lose audit signal.
            reason = (
                decision.dismiss_reason
                if decision.dismiss_reason is not None
                else "no reason given"
            )
            await _write_adjustment_txn(
                session,
                account_id=payload.account_id,
                instrument_id=decision.instrument_id,
                snapshot_date=payload.snapshot_date,
                delta_qty=ZERO,
                reconciliation_id=event.id,
                notes=f"dismissed: {reason}",
            )
        elif decision.action == "reject":
            # Real txn is created INSIDE this same transaction by the
            # rejected_txns loop below (for decimal-purity and
            # atomicity). Nothing to do in this branch:
            # decision.action="reject" is now purely declarative; the txn
            # body lives in payload.rejected_txns.
            continue

    # --- Reject txns: write inside this same transaction ---
    # Quantity is derived server-side via Python Decimal from
    # abs(snapshot_qty − app_qty) — mirrors the accept path and closes the
    # CLAUDE.md "NEVER float for money" gap that the previous Stage-2
    # POST /api/transactions flow violated (Number() coercion in the drawer).
    rejected_txn_ids: list[str] = []
    # Lazy-init a single httpx client used to auto-fetch ECB FX for any USD
    # reject row whose payload omits fx_rate_to_eur. Created on first need so
    # all-EUR reconciliations don't pay the connection-pool setup cost.
    fx_client: Optional[httpx.AsyncClient] = None
    try:
        for rtxn in payload.rejected_txns:
            snap_qty = snapshot_map.get(rtxn.instrument_id, ZERO)
            app_qty_val = app_qty_map.get(rtxn.instrument_id, ZERO)
            delta_abs = abs(snap_qty - app_qty_val)
            if delta_abs == ZERO:
                # Guard: snapshot == app means no quantity to record. The frontend
                # only stages Reject on drift rows, so this should not occur in
                # normal flow — log and skip rather than persist a 0-qty txn.
                logger.warning(
                    "rejected_txn for instrument %s has zero delta — skipping",
                    rtxn.instrument_id,
                )
                continue

            effective_date = (
                rtxn.txn_date if rtxn.txn_date is not None else payload.snapshot_date
            )

            # cost_basis_eur = qty * unit_price converted to EUR.
            # For EUR-priced txns we lock fx_rate_to_eur=1 (same convention used
            # everywhere else in the project — see services/networth, schemas/transaction).
            #
            # USD branch: if payload supplies fx_rate_to_eur, lock it on the row
            # (broker-markup case, mirrors routers/transactions.py:100). If not,
            # auto-fetch ECB EUR/USD rate as-of effective_date via Frankfurter —
            # this honors the drawer's "leave blank to fetch" promise and stops
            # the previous silent fx=1 default that persisted USD numbers in the
            # EUR cost_basis_eur column. Failure to fetch raises ValueError;
            # the router maps that to 422 and rolls back the whole save.
            if rtxn.price_currency == "USD":
                if rtxn.fx_rate_to_eur is not None:
                    fx = rtxn.fx_rate_to_eur
                    stored_fx: Decimal | None = rtxn.fx_rate_to_eur
                else:
                    if fx_client is None:
                        fx_client = httpx.AsyncClient()
                    try:
                        fx_row = await get_or_fetch_fx_rate(
                            session, fx_client, effective_date,
                            base="EUR", quote="USD",
                        )
                    except ValueError as exc:
                        raise ValueError(
                            f"USD reject for instrument {rtxn.instrument_id} "
                            f"requires fx_rate_to_eur; none provided and ECB "
                            f"lookup failed: {exc}"
                        )
                    fx = fx_row.rate
                    stored_fx = fx_row.rate
            else:
                fx = Decimal("1")
                stored_fx = Decimal("1")

            cost_basis_eur = (delta_abs * rtxn.unit_price) / fx

            # Sign convention (mirrors routers/transactions.py:62): sell/spend
            # rows are stored with NEGATIVE signed quantity (they consume lots
            # and reduce the holding). buy/yield/adjustment rows stay positive.
            # The rest of the codebase (signed-sum balance, FIFO
            # match_lots_for_sell which calls abs(), _quantity_after_events)
            # depends on this invariant.
            signed_qty = (
                -delta_abs if rtxn.txn_type in DISPOSAL_TXN_TYPES else delta_abs
            )

            reject_txn = Transaction(
                account_id=payload.account_id,
                instrument_id=rtxn.instrument_id,
                txn_type=rtxn.txn_type,
                date=effective_date,
                quantity=signed_qty,
                unit_price=rtxn.unit_price,
                price_currency=rtxn.price_currency,
                fx_rate_to_eur=stored_fx,
                cost_basis_eur=cost_basis_eur,
                fee_eur=rtxn.fee_eur,
                source="manual",
                reconciliation_id=event.id,
                notes=rtxn.notes,
            )
            session.add(reject_txn)
            await session.flush()  # populate reject_txn.id

            # Re-match the whole pair in canonical FIFO order (plan 015). This
            # single pair-wide recompute serves both reject shapes: for a
            # sell/spend reject it matches the new disposal (the recompute INCLUDES
            # the row we just inserted, so no separate match_lots_for_sell call is
            # needed) AND re-attributes any later disposals it now competes with.
            # For a back-dated buy reject it re-attributes existing disposals onto
            # the new lot they should draw from. The old date >= effective_date
            # scope was unsound (it skipped earlier disposals holding a lot dated
            # on or after the new row) and never ran at all for buy rejects. A
            # yield reject is not lot-affecting, so the recompute is a harmless
            # no-op for it.
            await recompute_fifo_for_pair(
                session,
                payload.account_id,
                rtxn.instrument_id,
            )

            rejected_txn_ids.append(reject_txn.id)
    finally:
        if fx_client is not None:
            await fx_client.aclose()

    # Stash the new IDs as a transient attribute for the router to read
    # into ReconciliationResponse. This survives db.refresh() because it is
    # not a mapped column — refresh only reloads ORM-managed columns.
    event._rejected_txn_ids = rejected_txn_ids  # type: ignore[attr-defined]

    await session.flush()
    return event
