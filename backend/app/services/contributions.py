"""Contribution analytics service.

Services do not commit or roll back transactions; routers own transaction
boundaries.
"""

from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.constants import VALUE_SCALE, ZERO
from app.models import HoldingTag, LotAlloc, PriceQuote, Tag, Transaction
from app.schemas.contributions import ContributionBucket, SeriesPoint
from app.services.cost_basis import _cost_basis_at, _load_allocations, _open_lots_at
from app.services.date_cursor import ForwardCursor
from app.services.market_data import load_market_data
from app.services.quotes import MissingFxRateError, convert
from app.services.quotes import convert_currency as _convert_currency

# Re-exported here so existing imports (`from app.services.contributions import
# _cost_basis_at`, etc.) continue to work — this module owned them historically
# and a couple of internal callers may still reach in for the names. The
# canonical source of truth is now ``app.services.cost_basis``.
__all__ = (
    "_cost_basis_at",
    "_load_allocations",
    "get_contribution_segments",
    "get_cost_basis_series",
)


async def get_cost_basis_series(
    session: AsyncSession,
    display_currency: str = "EUR",
    tag_filter: str | None = None,
    instrument_ids: list[str] | None = None,
) -> tuple[list[SeriesPoint], list[SeriesPoint]]:
    txns = await _load_transactions(
        session, tag_filter=tag_filter, instrument_ids=instrument_ids
    )
    if not txns:
        return [], []

    start = min(txn.date for txn in txns)
    end = clock.today()
    # Adjustments are intentionally EXCLUDED from contribution bars.
    # An adjustment is a reconciliation correction, NOT a cash contribution,
    # so contributions must not double-count drift remediation as new money in.
    buy_txns = [txn for txn in txns if txn.txn_type == "buy"]
    sell_txn_ids = {txn.id for txn in txns if txn.txn_type in {"sell", "spend"}}
    allocations = await _load_allocations(session, sell_txn_ids)
    # NOTE: contributions keeps its OWN _load_quotes for the value loop — that
    # ordering carries the documented manual-source tiebreak DRIFT vs. networth
    # (see _load_quotes below). Do NOT route these value quotes through the
    # snapshot's latest_quote, which uses perf semantics. Only the per-day FX
    # conversions move to the snapshot.
    quotes_by_instrument = await _load_quotes(session, end)

    # get_cost_basis_series converts PER DAY (`current` varies
    # across the loop), so it needs an FX-BY-DATE map, not a single rate. Build
    # the snapshot once up to `end` and resolve each day's rate via the per-date
    # convert(..., as_of=current) — identical to the prior per-day
    # _convert_currency(session, ..., current) DB calls (networth _fx_on_or_before
    # semantics). instrument_ids=set() because we only use the snapshot's FX map
    # here (quotes come from contributions' own _load_quotes above).
    snapshot = await load_market_data(session, as_of=end, instrument_ids=set())

    cost_basis_points: list[SeriesPoint] = []
    value_points: list[SeriesPoint] = []
    positions: dict[tuple[str, str], Decimal] = defaultdict(lambda: ZERO)
    txn_index = 0

    # Forward cursors replace the per-day linear scans (mirrors networth): the
    # loop advances `current` monotonically and quotes / fx dates are sorted, so a
    # forward-only index reproduces `[x for x in xs if x.date <= current][-1]`.
    quote_cursors: dict[str, ForwardCursor] = {}

    def _quote_cursor(iid: str) -> ForwardCursor:
        cur = quote_cursors.get(iid)
        if cur is None:
            cur = ForwardCursor(quotes_by_instrument.get(iid, []), key=lambda q: q.date)
            quote_cursors[iid] = cur
        return cur

    fx_dates_sorted = snapshot.sorted_fx_dates()
    fx_cursor = ForwardCursor(fx_dates_sorted, key=lambda d: d)

    # Memoize each open lot's OWN buy-date fx rate once (buy_date is fixed per
    # lot, not monotonic), and cache the FIFO open-lot decomposition — it changes
    # only on buy/sell/spend dates, so recompute only when the frontier advanced.
    buy_rate_cache: dict[date, Decimal] = {}

    def _buy_rate(buy_date: date) -> Decimal:
        rate = buy_rate_cache.get(buy_date)
        if rate is None:
            i = bisect_right(fx_dates_sorted, buy_date) - 1
            if i < 0:
                raise MissingFxRateError(
                    f"missing EUR/USD FX rate for {buy_date.isoformat()}"
                )
            rate = snapshot.rate_at(fx_dates_sorted[i])
            buy_rate_cache[buy_date] = rate
        return rate

    cached_open_lots: list[tuple[date, Decimal]] | None = None

    current = start
    while current <= end:
        txn_index_before = txn_index
        while txn_index < len(txns) and txns[txn_index].date <= current:
            txn = txns[txn_index]
            positions[(txn.account_id, txn.instrument_id)] += txn.quantity
            txn_index += 1

        # Transaction-time FX: convert each open buy lot's EUR cost basis at ITS
        # OWN buy-date EUR/USD rate (memoized), so the line only steps on
        # transaction days. EUR display short-circuits (from==to). The open-lot
        # set is recomputed only when the frontier advanced (it changes only on
        # buy/sell/spend dates); the per-lot sum still runs every day.
        if cached_open_lots is None or txn_index != txn_index_before:
            cached_open_lots = _open_lots_at(buy_txns, allocations, current)
        cost_basis_display = ZERO
        for buy_date, open_eur in cached_open_lots:
            if display_currency == "EUR":
                cost_basis_display += open_eur
            else:
                cost_basis_display += convert(
                    open_eur, "EUR", display_currency, _buy_rate(buy_date)
                )
        cost_basis_points.append(
            SeriesPoint(date=current, value=cost_basis_display)
        )

        day_fx_date = fx_cursor.at(current)
        total_value = ZERO
        for (_, instrument_id), quantity in positions.items():
            if quantity <= ZERO:
                continue
            quote = _quote_cursor(instrument_id).at(current)
            if quote is None:
                continue
            if quote.currency == display_currency:
                total_value += quantity * quote.price
            elif day_fx_date is None:
                raise MissingFxRateError(
                    f"missing EUR/USD FX rate for {current.isoformat()}"
                )
            else:
                total_value += convert(
                    quantity * quote.price,
                    quote.currency,
                    display_currency,
                    snapshot.rate_at(day_fx_date),
                )
        value_points.append(
            SeriesPoint(date=current, value=total_value.quantize(VALUE_SCALE))
        )
        current += timedelta(days=1)

    return cost_basis_points, value_points


async def get_contribution_segments(
    session: AsyncSession,
    period: Literal["month", "year"] = "month",
    display_currency: str = "EUR",
    tag_filter: str | None = None,
    instrument_ids: list[str] | None = None,
) -> list[ContributionBucket]:
    txns = await _load_transactions(
        session, tag_filter=tag_filter, instrument_ids=instrument_ids
    )
    if not txns:
        return []

    disposing_ids = {txn.id for txn in txns if txn.txn_type in {"sell", "spend"}}
    allocations = await _load_allocations(session, disposing_ids)
    buckets: dict[date, dict[str, Decimal | str | date]] = {}

    for txn in txns:
        period_start = _period_start(txn.date, period)
        if period_start not in buckets:
            buckets[period_start] = {
                "period_label": _period_label(period_start, period),
                "period_start": period_start,
                "deposits": ZERO,
                "spendings": ZERO,
                "realized_gains": ZERO,
                "yield_amount": ZERO,
            }
        bucket = buckets[period_start]
        if txn.txn_type == "buy" and txn.trade_pair_id is None:
            bucket["deposits"] = bucket["deposits"] + (txn.cost_basis_eur or ZERO)
        elif txn.txn_type == "sell" and txn.trade_pair_id is not None:
            bucket["realized_gains"] = bucket["realized_gains"] + _realized_for(
                allocations, txn.id
            )
        elif txn.txn_type == "spend":
            # The realized_gains segment is reserved for linked-sells. Spends contribute ONLY to spendings.
            bucket["spendings"] = bucket["spendings"] + _consumed_basis_for(
                allocations, txn.id
            )
        elif txn.txn_type == "yield":
            bucket["yield_amount"] = bucket["yield_amount"] + (txn.cost_basis_eur or ZERO)

    response: list[ContributionBucket] = []
    as_of = clock.today()
    for bucket in sorted(buckets.values(), key=lambda item: item["period_start"]):
        response.append(
            ContributionBucket(
                period_label=bucket["period_label"],
                period_start=bucket["period_start"],
                deposits=_quantize_value(
                    await _convert_currency(
                        session, bucket["deposits"], "EUR", display_currency, as_of
                    )
                ),
                spendings=_quantize_value(
                    await _convert_currency(
                        session, bucket["spendings"], "EUR", display_currency, as_of
                    )
                ),
                realized_gains=_quantize_value(
                    await _convert_currency(
                        session, bucket["realized_gains"], "EUR", display_currency, as_of
                    )
                ),
                yield_amount=_quantize_value(
                    await _convert_currency(
                        session, bucket["yield_amount"], "EUR", display_currency, as_of
                    )
                ),
            )
        )
    return response


async def _load_transactions(
    session: AsyncSession,
    *,
    tag_filter: str | None = None,
    instrument_ids: list[str] | None = None,
) -> list[Transaction]:
    stmt = (
        select(Transaction)
        .where(Transaction.deleted_at.is_(None))
        .order_by(Transaction.date.asc(), Transaction.created_at.asc())
    )
    if tag_filter is not None:
        stmt = stmt.where(
            select(HoldingTag.account_id)
            .join(Tag, Tag.id == HoldingTag.tag_id)
            .where(
                HoldingTag.account_id == Transaction.account_id,
                HoldingTag.instrument_id == Transaction.instrument_id,
                Tag.name == tag_filter,
            )
            .exists()
        )
    # Empty list = no filter (full portfolio); a non-empty
    # list narrows BOTH cost_basis_series and portfolio_value_series via the
    # shared transaction load. Cost basis only counts transactions for the
    # selected instruments; portfolio value only mark-to-markets the same
    # subset because the `positions` defaultdict downstream is built from
    # the same `txns` list.
    if instrument_ids:
        stmt = stmt.where(Transaction.instrument_id.in_(instrument_ids))
    result = await session.execute(stmt)
    return list(result.scalars())


async def _load_quotes(
    session: AsyncSession, end: date
) -> dict[str, list[PriceQuote]]:
    # NOTE: intentional drift from networth._load_quotes. networth's ORDER BY
    # adds a manual-source precedence tiebreak —
    #   case((PriceQuote.source == "manual", 1), else_=0).asc()
    # — before fetched_at, so for a (instrument, date) pair with BOTH a manual
    # and an auto quote, networth's tail-element pick (_quote_on_or_before)
    # returns the manual one. contributions deliberately keeps the simpler
    # (instrument_id, date, fetched_at) ordering it has always used; adding the
    # tiebreak here could change which same-date quote contributions selects and
    # therefore its emitted values. Kept distinct to preserve behavior; only the
    # shared scan helper (quote_on_or_before) is unified.
    stmt = (
        select(PriceQuote)
        .where(PriceQuote.date <= end)
        .order_by(PriceQuote.instrument_id.asc(), PriceQuote.date.asc(), PriceQuote.fetched_at.asc())
    )
    result = await session.execute(stmt)
    quotes_by_instrument: dict[str, list[PriceQuote]] = defaultdict(list)
    for quote in result.scalars():
        quotes_by_instrument[quote.instrument_id].append(quote)
    return quotes_by_instrument


def _consumed_basis_for(
    allocations: list[tuple[LotAlloc, Transaction, Transaction]], sell_txn_id: str
) -> Decimal:
    total = ZERO
    for alloc, buy, _ in allocations:
        if alloc.sell_txn_id != sell_txn_id:
            continue
        if buy.quantity > ZERO and buy.cost_basis_eur is not None:
            total += buy.cost_basis_eur * alloc.quantity / buy.quantity
    return total


def _realized_for(
    allocations: list[tuple[LotAlloc, Transaction, Transaction]], sell_txn_id: str
) -> Decimal:
    return sum(
        (
            alloc.realized_gain_eur or ZERO
            for alloc, _, _ in allocations
            if alloc.sell_txn_id == sell_txn_id
        ),
        ZERO,
    )


def _period_start(on_date: date, period: Literal["month", "year"]) -> date:
    if period == "year":
        return on_date.replace(month=1, day=1)
    return on_date.replace(day=1)


def _period_label(period_start: date, period: Literal["month", "year"]) -> str:
    if period == "year":
        return str(period_start.year)
    return period_start.strftime("%b %y")


def _quantize_value(value: Decimal) -> Decimal:
    return value.quantize(VALUE_SCALE)
