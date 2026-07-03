from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.constants import (
    ONE,
    RATIO_SCALE,
    TIMEFRAMES,
    ZERO,
)
from app.core.constants import (
    PERF_UNIT_SCALE as UNIT_SCALE,
)
from app.models import (
    LotAlloc,
    PriceQuote,
    Transaction,
)
from app.services.quotes import (
    MissingFxRateError,
    load_holdings,
)
from app.services.quotes import (
    convert_currency as _convert_currency,
)
from app.services.quotes import (
    first_buy_date as _first_buy_date,
)
from app.services.quotes import (
    latest_eur_usd_rate as _latest_eur_usd_rate,
)
from app.services.quotes import (
    latest_quote as _latest_quote,
)

INSUFFICIENT_HISTORY_DAYS = 7
# UNIT_SCALE (perf's 1e-18 quantization) and RATIO_SCALE (1e-16) are imported
# from app.core.constants above; UNIT_SCALE is aliased from PERF_UNIT_SCALE.

# MissingFxRateError, _convert_currency, _latest_eur_usd_rate, _latest_quote,
# and _first_buy_date now live in app.services.quotes and are re-exported here
# (under their historical leading-underscore names) so existing imports such as
# `from app.services.perf import _convert_currency, _latest_quote` keep working.
__all__ = [
    "MissingFxRateError",
    "_convert_currency",
    "_latest_eur_usd_rate",
    "_latest_quote",
    "_first_buy_date",
    "calculate_twrr",
    "calculate_open_lot_basis",
    "get_performance_rows",
    "PerfRow",
    "OpenLotBasis",
    "TwrrResult",
]


@dataclass(frozen=True)
class OpenLotBasis:
    account_id: str
    instrument_id: str
    open_buy_quantity: Decimal
    open_buy_basis_eur: Decimal
    open_quantity: Decimal
    avg_cost_eur: Decimal | None


@dataclass(frozen=True)
class TwrrResult:
    twrr: Decimal | None
    twrr_annualized: bool
    period_days: int | None
    reason: str | None
    boundary_dates: tuple[date, ...]


@dataclass(frozen=True)
class PerfRow:
    account_id: str
    account_name: str
    instrument_id: str
    instrument_symbol: str
    instrument_name: str
    instrument_type: str
    # Surfaced from instrument.display_decimals so the
    # PerfTable can format quantity with the right precision per row.
    display_decimals: int | None
    risk_level: str | None
    is_banked: bool
    quantity: Decimal
    avg_cost: Decimal | None
    current_price: Decimal | None
    current_price_fetched_at: object | None
    percent_return: Decimal | None
    realized_eur: Decimal | None
    twrr: Decimal | None
    twrr_annualized: bool
    twrr_period_days: int | None
    twrr_reason: str | None
    open_buy_basis: Decimal
    current_value: Decimal | None
    # Open/closed discriminator. Default keeps existing call sites compiling.
    status: str = "open"
    # Closed-only fields (None for open rows). Mirrors ClosedPositionRow shape.
    last_close: Decimal | None = None
    last_close_date: date | None = None
    twrr_window_days: int | None = None


def _divide(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    if denominator <= ZERO:
        return None
    return numerator / denominator


def _quantize_unit(value: Decimal | None) -> Decimal | None:
    if value is None:
        return None
    return value.quantize(UNIT_SCALE)


def _quantize_ratio(value: Decimal | None) -> Decimal | None:
    if value is None:
        return None
    # No .normalize(), quantize to the fixed RATIO_SCALE.
    return value.quantize(RATIO_SCALE)


def _open_lot_basis_from_parts(
    account_id: str,
    instrument_id: str,
    buy_txns: list[Transaction],
    consumed_by_buy: dict[str, Decimal],
    open_quantity: Decimal,
) -> OpenLotBasis:
    """Pure reducer shared by the per-holding and batched lot-basis paths.

    Given this (account, instrument)'s buy/adjustment txns (qty>0, ordered
    date/created_at asc), the LotAlloc consumed-sums keyed by buy_txn_id, and the
    coalesced total open_quantity, produce the OpenLotBasis. Arithmetic is
    byte-identical to the original per-holding loop (same open_qty<=ZERO skip,
    same cost_basis_eur*open_qty/buy.quantity proration, same avg-cost quantize).
    """
    open_buy_quantity = ZERO
    open_buy_basis_eur = ZERO
    for buy in buy_txns:
        consumed = consumed_by_buy.get(buy.id, ZERO)
        open_qty = buy.quantity - consumed
        if open_qty <= ZERO:
            continue
        open_buy_quantity += open_qty
        if buy.cost_basis_eur is not None and buy.quantity > ZERO:
            open_buy_basis_eur += buy.cost_basis_eur * open_qty / buy.quantity

    avg_cost_eur = _quantize_unit(_divide(open_buy_basis_eur, open_quantity))
    return OpenLotBasis(
        account_id=account_id,
        instrument_id=instrument_id,
        open_buy_quantity=open_buy_quantity,
        open_buy_basis_eur=open_buy_basis_eur,
        open_quantity=open_quantity,
        avg_cost_eur=avg_cost_eur,
    )


async def calculate_open_lot_basis(
    session: AsyncSession, account_id: str, instrument_id: str
) -> OpenLotBasis:
    # quantity is TEXT-backed (DecimalText): the qty>0 sign filter moves to
    # Python after fetch; SQL SUM/comparison would coerce the text to float.
    buy_stmt = (
        select(Transaction)
        .where(
            Transaction.account_id == account_id,
            Transaction.instrument_id == instrument_id,
            # Positive adjustments contribute open lots (cost_basis_eur=NULL handled by guard at line ~138).
            Transaction.txn_type.in_(("buy", "adjustment")),
            Transaction.deleted_at.is_(None),
        )
        .order_by(Transaction.date.asc(), Transaction.created_at.asc())
    )
    buy_result = await session.execute(buy_stmt)
    buy_txns = [txn for txn in buy_result.scalars() if txn.quantity > ZERO]
    buy_ids = [txn.id for txn in buy_txns]

    consumed_by_buy: dict[str, Decimal] = defaultdict(lambda: ZERO)
    if buy_ids:
        alloc_stmt = select(LotAlloc.buy_txn_id, LotAlloc.quantity).where(
            LotAlloc.buy_txn_id.in_(buy_ids)
        )
        alloc_result = await session.execute(alloc_stmt)
        for buy_txn_id, qty in alloc_result:
            consumed_by_buy[buy_txn_id] += qty

    # Sum signed quantity in Python (TEXT column — no SQL SUM).
    qty_rows = await session.execute(
        select(Transaction.quantity).where(
            Transaction.account_id == account_id,
            Transaction.instrument_id == instrument_id,
            Transaction.deleted_at.is_(None),
        )
    )
    open_quantity = sum((q for (q,) in qty_rows), ZERO)

    return _open_lot_basis_from_parts(
        account_id, instrument_id, buy_txns, consumed_by_buy, open_quantity
    )


async def calculate_open_lot_basis_batch(
    session: AsyncSession,
) -> dict[tuple[str, str], OpenLotBasis]:
    """Batched lot-basis for ALL holdings — request-constant query count.

    Replaces the per-holding 3-query pattern (buy txns / lot-alloc sums /
    total qty) with three grouped queries. Each predicate replicates the
    per-holding call site EXACTLY:
      - buys: txn_type in (buy, adjustment), quantity>0, deleted_at IS NULL,
        ordered (date asc, created_at asc) — same as calculate_open_lot_basis.
      - lot-alloc consumed: GROUP BY buy_txn_id over those buy ids.
      - total qty: coalesce(sum(quantity), ZERO) GROUP BY (account_id,
        instrument_id), deleted_at IS NULL — same coalesce-to-ZERO semantics.
    Output values are identical to the per-holding function.
    """
    # 1. All buy/adjustment lots, ordered so each per-(acct,inst) bucket inherits
    #    the (date asc, created_at asc) order the per-holding query used. The
    #    quantity>0 sign filter moves to Python (TEXT column — see plan 006).
    buy_stmt = (
        select(Transaction)
        .where(
            Transaction.txn_type.in_(("buy", "adjustment")),
            Transaction.deleted_at.is_(None),
        )
        .order_by(Transaction.date.asc(), Transaction.created_at.asc())
    )
    buy_result = await session.execute(buy_stmt)
    buys_by_holding: dict[tuple[str, str], list[Transaction]] = {}
    all_buy_ids: list[str] = []
    for buy in buy_result.scalars():
        if buy.quantity <= ZERO:
            continue
        buys_by_holding.setdefault((buy.account_id, buy.instrument_id), []).append(buy)
        all_buy_ids.append(buy.id)

    # 2. Consumed-sum per buy id across ALL buys (Python sum — TEXT column).
    consumed_by_buy: dict[str, Decimal] = defaultdict(lambda: ZERO)
    if all_buy_ids:
        alloc_stmt = select(LotAlloc.buy_txn_id, LotAlloc.quantity).where(
            LotAlloc.buy_txn_id.in_(all_buy_ids)
        )
        alloc_result = await session.execute(alloc_stmt)
        for buy_txn_id, qty in alloc_result:
            consumed_by_buy[buy_txn_id] += qty

    # 3. Total open quantity per (account, instrument), summed in Python.
    #    coalesce-to-ZERO is preserved by qty_by_holding.get(..., ZERO) below.
    qty_stmt = (
        select(
            Transaction.account_id,
            Transaction.instrument_id,
            Transaction.quantity,
        )
        .where(Transaction.deleted_at.is_(None))
        .order_by(Transaction.account_id, Transaction.instrument_id)
    )
    qty_result = await session.execute(qty_stmt)
    qty_by_holding: dict[tuple[str, str], Decimal] = defaultdict(lambda: ZERO)
    for account_id_, instrument_id_, qty in qty_result:
        qty_by_holding[(account_id_, instrument_id_)] += qty

    out: dict[tuple[str, str], OpenLotBasis] = {}
    # Iterate every holding that has either buys OR a total-qty row, so a holding
    # with no buy/adjustment lots (e.g. only yields) still yields an OpenLotBasis
    # with open_buy_quantity=ZERO — matching the per-holding function's behavior.
    holdings = set(buys_by_holding) | set(qty_by_holding)
    for account_id, instrument_id in holdings:
        out[(account_id, instrument_id)] = _open_lot_basis_from_parts(
            account_id,
            instrument_id,
            buys_by_holding.get((account_id, instrument_id), []),
            consumed_by_buy,
            qty_by_holding.get((account_id, instrument_id), ZERO),
        )
    return out


def _quotes_in_window_from_preload(
    preloaded: list[PriceQuote], start: date, end: date
) -> list[PriceQuote]:
    """Filter a per-instrument preloaded quote list to the TWRR window.

    Replicates _quotes_in_window's predicate EXACTLY: start <= date <= end
    (inclusive both ends). The preloaded list is the instrument's full history
    ordered (date asc, fetched_at asc) with NO manual-source tiebreak — see
    _load_twrr_quotes — so the windowed slice matches the SQL result row-for-row,
    and _price_on_or_before's eligible[-1] pick selects the same quote.
    """
    return [q for q in preloaded if start <= q.date <= end]


async def calculate_twrr(
    session: AsyncSession,
    account_id: str,
    instrument_id: str,
    start: date | None,
    end: date,
    *,
    first_buy: date | None = None,
    preloaded_quotes: list[PriceQuote] | None = None,
    preloaded_txns: list[Transaction] | None = None,
) -> TwrrResult:
    """Compute TWRR for one (account, instrument).

    `first_buy` may be passed in if the caller has already resolved it
    (get_performance_rows resolves it once and reuses it for both the
    `_quote_day_count` lookup and TWRR sub-period bounds).

    `preloaded_quotes` (instrument's full quote history,
    date<=end, ordered date/fetched_at asc, NO manual tiebreak) and
    `preloaded_txns` (this holding's txns, date<=end, ordered date/created_at
    asc) let the batched caller skip the per-holding _quotes_in_window /
    _transactions_for_position DB round-trips. When omitted, the original DB
    queries run so direct callers (closed.py) keep working unchanged.
    """
    if first_buy is None:
        first_buy = await _first_buy_date(session, account_id, instrument_id)
    if first_buy is None:
        return TwrrResult(None, False, None, "no_position", ())

    period_start = max(start, first_buy) if start is not None else first_buy
    if preloaded_quotes is not None:
        quotes = _quotes_in_window_from_preload(preloaded_quotes, period_start, end)
    else:
        quotes = await _quotes_in_window(session, instrument_id, period_start, end)
    quote_days = {quote.date for quote in quotes}
    if len(quote_days) < 2:
        return TwrrResult(None, False, None, "insufficient_history", ())

    start_price = _price_on_or_before(quotes, period_start)
    end_price = _price_on_or_before(quotes, end)
    if start_price is None or end_price is None:
        return TwrrResult(None, False, None, "missing_price", ())

    if preloaded_txns is not None:
        # _transactions_for_position filters date<=end; the preload is already
        # date<=end for this holding (end == as_of), so use it directly.
        txns = preloaded_txns
    else:
        txns = await _transactions_for_position(
            session, account_id, instrument_id, end=end
        )
    boundary_dates = tuple(
        sorted(
            {
                txn.date
                for txn in txns
                if txn.txn_type in {"buy", "sell", "adjustment"} and period_start < txn.date < end
            }
        )
    )
    dates = (period_start, *boundary_dates, end)

    linked_return = ONE
    for idx in range(len(dates) - 1):
        sub_start = dates[idx]
        sub_end = dates[idx + 1]
        sub_start_price = _price_on_or_before(quotes, sub_start)
        sub_end_price = _price_on_or_before(quotes, sub_end)
        if sub_start_price is None or sub_end_price is None:
            continue
        start_quantity = _quantity_after_events(txns, sub_start)
        end_quantity = _quantity_after_internal_events(txns, sub_end)
        start_value = start_quantity * sub_start_price
        end_value = end_quantity * sub_end_price
        if start_quantity <= ZERO:
            continue
        period_return = _divide(end_value - start_value, start_value)
        if period_return is None:
            continue
        linked_return *= ONE + period_return

    twrr = linked_return - ONE
    period_days = (end - period_start).days
    annualized = period_days >= 365
    if annualized and period_days != 365:
        years = Decimal(period_days) / Decimal("365")
        base = ONE + twrr
        if years > ZERO and base > ZERO:
            twrr = base ** (ONE / years) - ONE
        else:
            # Total loss (twrr == -1) — annualization is mathematically
            # undefined (Decimal raises on 0**fractional). Fall back to the
            # raw period TWRR rather than 500ing the entire dashboard.
            annualized = False

    return TwrrResult(_quantize_ratio(twrr), annualized, period_days, None, boundary_dates)


async def get_performance_rows(
    session: AsyncSession,
    timeframe: str,
    display_currency: str,
    today: date | None = None,
    tag_filter: str | None = None,
    include_closed: bool = False,  # Opt-in for /compare AllocationDrill.
    from_date: date | None = None,
    to_date: date | None = None,
) -> list[PerfRow]:
    # "custom" widens the regex without entering the preset
    # TIMEFRAMES map — the [start, as_of] window comes from `from_date`/`to_date`
    # instead of a (days, today) computation.
    if timeframe != "custom" and timeframe not in TIMEFRAMES:
        raise ValueError(f"unsupported timeframe: {timeframe}")

    if timeframe == "custom":
        if from_date is None or to_date is None:
            raise ValueError("custom timeframe requires from_date and to_date")
        start = from_date
        as_of = to_date
    else:
        as_of = today or clock.today()
        days = TIMEFRAMES[timeframe]
        start = None if days is None else as_of - timedelta(days=days)

    # Load distinct holdings (optionally tag-filtered) and batch-load
    # Accounts/Instruments before the per-holding loop to avoid N+1 round-trips.
    # Extracted to services.quotes.load_holdings (shared with closed/allocation).
    holdings, accounts_by_id, instruments_by_id = await load_holdings(session, tag_filter)

    # Build the request-scoped market-data snapshot once (quotes
    # + FX at as_of) so the per-holding loop below reads quotes/FX from memory
    # instead of firing _latest_quote + _convert_currency DB round-trips per row.
    # perf converts everything at the single as_of, so the single-rate accessors
    # are correct here. Byte-identical to the per-call quotes.* helpers.
    from app.services.market_data import load_market_data

    snapshot = await load_market_data(
        session,
        as_of=as_of,
        instrument_ids={instrument_id for _, instrument_id in holdings},
    )

    # Batch the per-holding lot-basis + TWRR
    # quote/txn queries into request-constant grouped loads. The batched results
    # are byte-identical to the per-holding helpers (see their docstrings).
    lot_basis_by_holding = await calculate_open_lot_basis_batch(session)
    twrr_quotes_by_instrument = await _load_twrr_quotes(
        session, {instrument_id for _, instrument_id in holdings}, as_of
    )
    twrr_txns_by_holding = await _load_twrr_transactions(session, as_of)

    from app.services.realized import get_realized_per_holding

    realized_rows = await get_realized_per_holding(
        session, display_currency=display_currency, tag_filter=tag_filter
    )
    realized_by_instrument = {
        row.instrument_id: row.realized_eur for row in realized_rows
    }

    rows: list[PerfRow] = []
    for account_id, instrument_id in holdings:
        # Batched lot-basis lookup (request-constant) — identical value to the
        # per-holding calculate_open_lot_basis. Fall back to the per-holding call
        # only if a holding is somehow absent from the batch (defensive; the
        # batch covers every (account, instrument) with a txn row).
        basis = lot_basis_by_holding.get((account_id, instrument_id))
        if basis is None:
            basis = await calculate_open_lot_basis(session, account_id, instrument_id)
        if basis.open_quantity <= ZERO:
            continue

        account = accounts_by_id.get(account_id)
        instrument = instruments_by_id.get(instrument_id)
        if account is None or instrument is None:
            continue

        quote = snapshot.latest_quote(instrument_id)
        current_price: Decimal | None = None
        current_value: Decimal | None = None
        missing_fx = False
        if quote is not None:
            try:
                current_price = snapshot.convert(
                    quote.price,
                    quote.currency,
                    display_currency,
                )
                current_value = snapshot.convert(
                    basis.open_quantity * quote.price,
                    quote.currency,
                    display_currency,
                )
            except MissingFxRateError:
                missing_fx = True

        try:
            open_buy_basis = snapshot.convert(
                basis.open_buy_basis_eur,
                "EUR",
                display_currency,
            )
        except MissingFxRateError:
            missing_fx = True
            open_buy_basis = basis.open_buy_basis_eur if display_currency == "EUR" else ZERO

        avg_cost: Decimal | None = None
        if basis.avg_cost_eur is not None:
            try:
                avg_cost = snapshot.convert(
                    basis.avg_cost_eur,
                    "EUR",
                    display_currency,
                )
            except MissingFxRateError:
                missing_fx = True

        percent_return = None
        twrr_reason = None
        if missing_fx:
            twrr_reason = "missing_fx"
        elif open_buy_basis <= ZERO:
            twrr_reason = "non_positive_basis"
        elif current_value is not None:
            percent_return = (current_value - open_buy_basis) / open_buy_basis

        # Resolve first_buy_date once and reuse it for both the TWRR
        # window resolution and the quote-day-count "all-timeframe" fallback.
        # Avoids two redundant round-trips per holding under the `all`
        # timeframe and removes a torn-read window where the two queries could
        # disagree if a buy is inserted between them.
        first_buy_date = await _first_buy_date(session, account_id, instrument_id)
        # Feed TWRR the preloaded quote/txn lists so its
        # _quotes_in_window / _transactions_for_position round-trips are skipped.
        preloaded_quotes = twrr_quotes_by_instrument.get(instrument_id, [])
        preloaded_txns = twrr_txns_by_holding.get((account_id, instrument_id), [])
        twrr = await calculate_twrr(
            session,
            account_id,
            instrument_id,
            start,
            as_of,
            first_buy=first_buy_date,
            preloaded_quotes=preloaded_quotes,
            preloaded_txns=preloaded_txns,
        )
        # quote-day count over the preloaded list — same inclusive [start, end]
        # bounds as the SQL COUNT(DISTINCT date).
        quote_days = _quote_day_count_from_preload(
            preloaded_quotes,
            start or first_buy_date or as_of,
            as_of,
        )
        if quote_days < INSUFFICIENT_HISTORY_DAYS:
            twrr = TwrrResult(None, False, None, "insufficient_history", ())
        rows.append(
            PerfRow(
                account_id=account.id,
                account_name=account.name,
                instrument_id=instrument.id,
                instrument_symbol=instrument.symbol,
                instrument_name=instrument.name,
                instrument_type=instrument.instrument_type,
                display_decimals=instrument.display_decimals,
                risk_level=getattr(instrument, "risk_level", None),
                is_banked=account.is_banked,
                quantity=basis.open_quantity,
                avg_cost=avg_cost,
                current_price=current_price,
                # When the FX conversion of current_price failed, we
                # null current_price/current_value but still hold a quote
                # object. Travel the timestamp with the price — a non-null
                # fetched_at on a row whose price could not be expressed in
                # display_currency would mislead freshness-badge consumers.
                current_price_fetched_at=(
                    quote.fetched_at
                    if quote is not None and current_price is not None
                    else None
                ),
                percent_return=percent_return,
                # Temporarily fill with ZERO; the correct account-attribution
                # pass runs after the loop because we need every row's current_value
                # to pick the largest-holding account per instrument.
                realized_eur=ZERO,
                twrr=twrr.twrr,
                twrr_annualized=twrr.twrr_annualized,
                twrr_period_days=twrr.period_days,
                twrr_reason=twrr_reason or twrr.reason,
                open_buy_basis=open_buy_basis,
                current_value=current_value,
                status="open",
            )
        )

    # Attribute realized_eur to a single representative row per
    # instrument — the open row with the largest current_value — and leave
    # ZERO on the other accounts. The realized service aggregates LotAlloc
    # per instrument (it cannot today expose per-(account, instrument)
    # realized totals), so emitting the same realized total on every account
    # row holding that instrument double-counted realized gains for any
    # instrument held in more than one account (e.g. USDC in Revolut and
    # Bit2Me). Picking the largest-holding row makes the displayed total
    # match the realized service's instrument total without duplication.
    # Rows whose current_value is None (missing FX / no quote) sort last so
    # a priced row wins; ties resolve by account_id for determinism.
    best_row_idx_by_instrument: dict[str, int] = {}
    for idx, row in enumerate(rows):
        existing_idx = best_row_idx_by_instrument.get(row.instrument_id)
        if existing_idx is None:
            best_row_idx_by_instrument[row.instrument_id] = idx
            continue
        existing = rows[existing_idx]
        existing_value = existing.current_value if existing.current_value is not None else Decimal("-1")
        candidate_value = row.current_value if row.current_value is not None else Decimal("-1")
        if candidate_value > existing_value or (
            candidate_value == existing_value and row.account_id < existing.account_id
        ):
            best_row_idx_by_instrument[row.instrument_id] = idx

    for instrument_id, best_idx in best_row_idx_by_instrument.items():
        realized = realized_by_instrument.get(instrument_id, ZERO)
        if realized != ZERO:
            rows[best_idx] = replace(rows[best_idx], realized_eur=realized)

    # Opt-in merge of closed positions for unified /compare AllocationDrill.
    # Closed rows are timeframe-invariant (final-period TWRR); we drop the timeframe param
    # when calling get_closed_positions but propagate display_currency and tag_filter.
    if include_closed:
        from app.services.closed import get_closed_positions  # local — avoids circular import

        closed_rows = await get_closed_positions(
            session,
            display_currency=display_currency,
            tag_filter=tag_filter,
        )
        for cr in closed_rows:
            rows.append(
                PerfRow(
                    account_id=cr.account_id,
                    account_name=cr.account_name,
                    instrument_id=cr.instrument_id,
                    instrument_symbol=cr.instrument_symbol,
                    instrument_name=cr.instrument_name or "",
                    instrument_type=cr.instrument_type,
                    display_decimals=cr.display_decimals,
                    risk_level=None,
                    is_banked=False,
                    quantity=cr.quantity,
                    avg_cost=cr.avg_cost,
                    current_price=None,
                    current_price_fetched_at=None,
                    percent_return=cr.percent_return,
                    realized_eur=cr.realized_eur,
                    twrr=cr.twrr,
                    twrr_annualized=cr.twrr_annualized,
                    twrr_period_days=cr.twrr_window_days,  # legacy field — backwards compat
                    twrr_reason=None,
                    open_buy_basis=ZERO,
                    current_value=None,
                    status="closed",
                    last_close=cr.last_close,
                    last_close_date=cr.last_close_date,
                    twrr_window_days=cr.twrr_window_days,
                )
            )

    return rows


async def _transactions_for_position(
    session: AsyncSession, account_id: str, instrument_id: str, end: date
) -> list[Transaction]:
    stmt = (
        select(Transaction)
        .where(
            Transaction.account_id == account_id,
            Transaction.instrument_id == instrument_id,
            Transaction.date <= end,
            Transaction.deleted_at.is_(None),
        )
        .order_by(Transaction.date.asc(), Transaction.created_at.asc())
    )
    result = await session.execute(stmt)
    return list(result.scalars())


async def _quotes_in_window(
    session: AsyncSession, instrument_id: str, start: date, end: date
) -> list[PriceQuote]:
    stmt = (
        select(PriceQuote)
        .where(
            PriceQuote.instrument_id == instrument_id,
            PriceQuote.date >= start,
            PriceQuote.date <= end,
        )
        .order_by(PriceQuote.date.asc(), PriceQuote.fetched_at.asc())
    )
    result = await session.execute(stmt)
    return list(result.scalars())


async def _quote_day_count(
    session: AsyncSession, instrument_id: str, start: date, end: date
) -> int:
    stmt = select(func.count(func.distinct(PriceQuote.date))).where(
        PriceQuote.instrument_id == instrument_id,
        PriceQuote.date >= start,
        PriceQuote.date <= end,
    )
    result = await session.execute(stmt)
    return result.scalar_one()


async def _load_twrr_quotes(
    session: AsyncSession, instrument_ids: set[str], as_of: date
) -> dict[str, list[PriceQuote]]:
    """Preload every instrument's quote history (date <= as_of) for TWRR.

    CRITICAL ORDERING (checker equivalence_safety): the ORDER BY here is
    strictly (date asc, fetched_at asc) — the SAME as _quotes_in_window — and
    deliberately OMITS the manual-source tiebreak that
    quotes._load_quotes / networth._load_quotes insert
    (case((source=="manual",1),else_=0).asc()) between date and fetched_at.
    That tiebreak would change which same-date row lands last in the list, and
    _price_on_or_before picks eligible[-1], so a different same-date ordering
    would select a DIFFERENT quote and silently break byte-identity. Do NOT add
    the manual tiebreak here — match _quotes_in_window, not _load_quotes.

    Window filtering to [period_start, end] happens in Python per-call via
    _quotes_in_window_from_preload (inclusive both ends, matching the SQL).
    """
    stmt = (
        select(PriceQuote)
        .where(
            PriceQuote.instrument_id.in_(instrument_ids),
            PriceQuote.date <= as_of,
        )
        .order_by(PriceQuote.date.asc(), PriceQuote.fetched_at.asc())
    )
    result = await session.execute(stmt)
    by_instrument: dict[str, list[PriceQuote]] = {}
    for q in result.scalars():
        by_instrument.setdefault(q.instrument_id, []).append(q)
    return by_instrument


async def _load_twrr_transactions(
    session: AsyncSession, as_of: date
) -> dict[tuple[str, str], list[Transaction]]:
    """Preload every holding's transactions (date <= as_of) for TWRR.

    Predicate + ordering mirror _transactions_for_position exactly: date <=
    as_of, deleted_at IS NULL, ordered (date asc, created_at asc). Grouped per
    (account_id, instrument_id).
    """
    stmt = (
        select(Transaction)
        .where(
            Transaction.date <= as_of,
            Transaction.deleted_at.is_(None),
        )
        .order_by(Transaction.date.asc(), Transaction.created_at.asc())
    )
    result = await session.execute(stmt)
    by_holding: dict[tuple[str, str], list[Transaction]] = {}
    for txn in result.scalars():
        by_holding.setdefault((txn.account_id, txn.instrument_id), []).append(txn)
    return by_holding


def _quote_day_count_from_preload(
    preloaded: list[PriceQuote], start: date, end: date
) -> int:
    """Distinct-date count over a preloaded list, matching the SQL COUNT(DISTINCT
    date) WHERE date>=start AND date<=end — same inclusive bounds. Manual-source
    ordering is irrelevant to a distinct-date count; only the bounds must match.
    """
    return len({q.date for q in preloaded if start <= q.date <= end})


def _price_on_or_before(quotes: list[PriceQuote], on_date: date) -> Decimal | None:
    eligible = [quote for quote in quotes if quote.date <= on_date]
    if not eligible:
        return None
    return eligible[-1].price


def _quantity_after_events(txns: list[Transaction], on_date: date) -> Decimal:
    return sum(
        (txn.quantity for txn in txns if txn.date <= on_date),
        ZERO,
    )


def _quantity_after_internal_events(txns: list[Transaction], on_date: date) -> Decimal:
    # Trade events on a sub-period boundary date define the *next* sub-period,
    # so they must NOT be counted toward the quantity at the end of this one
    # (otherwise a buy that opens sub-period N+1 would also inflate the
    # end-of-N quantity, double-counting it across the TWRR sub-period seam).
    # Non-trade events (yield accrual, dividends, etc.) on the boundary date
    # belong to this sub-period and are included through `on_date` inclusive.
    trade_qty_before_boundary = _quantity_after_events(
        [txn for txn in txns if txn.txn_type in {"buy", "sell", "adjustment"}],
        on_date - timedelta(days=1),
    )
    non_trade_qty_through_boundary = sum(
        (
            txn.quantity
            for txn in txns
            if txn.date <= on_date
            and txn.txn_type not in {"buy", "sell", "adjustment"}
        ),
        ZERO,
    )
    return trade_qty_before_boundary + non_trade_qty_through_boundary


