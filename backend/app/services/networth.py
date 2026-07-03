from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import case, func, null, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.constants import TIMEFRAMES as _BASE_TIMEFRAMES
from app.core.constants import VALUE_SCALE, ZERO
from app.models import FxRate, HoldingTag, Instrument, PriceQuote, Tag, Transaction
from app.services.cost_basis import _load_allocations, _open_lots_at
from app.services.quotes import MissingFxRateError, convert
from app.services.quotes import quote_on_or_before as _quote_on_or_before

# networth's value/cost-basis quantization scale (1e-8). Aliased from the
# centralized VALUE_SCALE; the historical local name UNIT_SCALE is retained.
UNIT_SCALE = VALUE_SCALE
# networth extends the preset timeframes with a "custom" window (resolved from
# explicit from/to dates). Build a fresh dict so the shared TIMEFRAMES mapping
# in app.core.constants is not mutated.
TIMEFRAMES = {**_BASE_TIMEFRAMES, "custom": None}

# MissingFxRateError now lives in app.services.quotes (unified with perf's
# formerly-separate but behaviorally identical class); re-exported here so any
# `from app.services.networth import MissingFxRateError` import keeps working.
__all__ = ["MissingFxRateError", "get_networth_series", "aggregate_points", "build_markers"]


@dataclass(frozen=True)
class DailyPoint:
    date: date
    value: Decimal


@dataclass(frozen=True)
class NetWorthPoint:
    date: date
    value: Decimal


@dataclass(frozen=True)
class NetWorthMarker:
    date: date
    type: str
    instrument_id: str | None
    instrument_symbol: str | None
    quantity: Decimal | None
    value: Decimal
    count: int
    # Surface per-instrument context so the chart
    # tooltip can format quantity with the right precision. None for
    # aggregate markers (no single instrument). Defaulted at the end of
    # the field list to preserve backwards-compatible positional
    # construction at existing call sites and tests.
    instrument_type: str | None = None
    display_decimals: int | None = None


@dataclass(frozen=True)
class NetWorthSeries:
    points: list[NetWorthPoint]
    markers: list[NetWorthMarker]
    aggregation: str
    warnings: list[str]
    # Aggregated cost-basis series, populated when the
    # caller passes ``include_cost_basis=True``. Empty list otherwise so
    # the response shape stays identical for legacy callers (instrument
    # detail page) that omit the flag.
    cost_basis_series: list[NetWorthPoint] = field(default_factory=list)


async def get_networth_series(
    session: AsyncSession,
    timeframe: str,
    display_currency: str,
    start: date | None = None,
    end: date | None = None,
    instrument_id: str | None = None,
    instrument_ids: list[str] | None = None,
    tag_filter: str | None = None,
    include_cost_basis: bool = False,
) -> NetWorthSeries:
    if timeframe not in TIMEFRAMES:
        raise ValueError(f"unsupported timeframe: {timeframe}")
    if display_currency not in {"EUR", "USD"}:
        raise ValueError(f"unsupported display currency: {display_currency}")

    # Accept either the legacy single `instrument_id` (kept
    # for backward compat with any in-tree callers) or the new
    # `instrument_ids` list. Normalize to a single list of strings; an empty
    # list means "no filter" (full portfolio).
    effective_ids: list[str] = list(instrument_ids or [])
    if instrument_id is not None and instrument_id not in effective_ids:
        effective_ids.append(instrument_id)

    range_start, range_end = await _resolve_range(
        session,
        timeframe,
        start,
        end,
        instrument_ids=effective_ids,
        tag_filter=tag_filter,
    )
    if range_start is None:
        return NetWorthSeries([], [], _aggregation_for_range(timeframe, None), [])

    transactions = await _load_transactions(
        session, range_end, instrument_ids=effective_ids, tag_filter=tag_filter
    )
    instruments = await _load_instruments(session)
    quotes_by_instrument = await _load_quotes(session, range_end)
    fx_by_date = await _load_fx(session, range_end)

    # Index priced transactions per instrument so the replay can fall
    # back to a buy/sell unit_price when no real PriceQuote exists yet (e.g.
    # an instrument was just added and the nightly price-refresh job hasn't
    # backfilled historical quotes). Yield txns don't carry unit_price, so
    # they are naturally excluded by the None check.
    # `transactions` is already sorted date-ascending (see _load_transactions),
    # so each per-instrument bucket inherits that order — no extra sort.
    txns_with_price_by_instrument: dict[str, list[Transaction]] = defaultdict(list)
    for txn in transactions:
        if txn.unit_price is not None and txn.price_currency is not None:
            txns_with_price_by_instrument[txn.instrument_id].append(txn)

    # When the caller asks for a cost-basis series we load
    # FIFO allocations once for every disposing txn in the (already
    # tag/instrument-filtered) txn set, then compute per-day open-lot basis
    # in EUR inside the same daily loop and convert to ``display_currency``
    # via the same FX path the value series uses. When the flag is off we do
    # NO extra DB work and emit ``cost_basis_series=[]``.
    cost_basis_buys: list[Transaction] = []
    cost_basis_allocations: list = []
    if include_cost_basis:
        cost_basis_buys = [txn for txn in transactions if txn.txn_type == "buy"]
        sell_txn_ids = {
            txn.id for txn in transactions if txn.txn_type in {"sell", "spend"}
        }
        cost_basis_allocations = await _load_allocations(session, sell_txn_ids)

    positions: dict[tuple[str, str], Decimal] = defaultdict(lambda: ZERO)
    txn_index = 0
    daily_points: list[DailyPoint] = []
    daily_cost_basis: list[DailyPoint] = []
    warnings: list[str] = []
    seen_warnings: set[str] = set()
    current = range_start
    while current <= range_end:
        while txn_index < len(transactions) and transactions[txn_index].date <= current:
            txn = transactions[txn_index]
            positions[(txn.account_id, txn.instrument_id)] += txn.quantity
            txn_index += 1

        total = ZERO
        for (_, instrument_id), quantity in positions.items():
            if quantity <= ZERO:
                continue
            quote = _quote_on_or_before(quotes_by_instrument.get(instrument_id, []), current)
            if quote is not None:
                price, price_currency = quote.price, quote.currency
            else:
                # Fall back to the most-recent priced txn for this
                # instrument when no real PriceQuote exists yet. Carries the
                # last-known trade price forward — same algorithm
                # `_quote_on_or_before` uses for stale weekend quotes, just
                # over a different source. Most useful for the gap between a
                # buy and the first market quote, but the fallback applies
                # any time a PriceQuote is missing — so a stale trade price
                # may persist for arbitrary spans if real quotes never land.
                synthetic = _synthetic_quote_on_or_before(
                    txns_with_price_by_instrument.get(instrument_id, []), current
                )
                if synthetic is None:
                    warning = f"missing_price:{instrument_id}:{current.isoformat()}"
                    if warning not in seen_warnings:
                        warnings.append(warning)
                        seen_warnings.add(warning)
                    continue
                price, price_currency = synthetic
            try:
                total += _convert_amount(
                    amount=quantity * price,
                    from_currency=price_currency,
                    to_currency=display_currency,
                    fx_by_date=fx_by_date,
                    as_of=current,
                )
            except MissingFxRateError:
                # Degrade gracefully: surface as a per-day warning (matching
                # the missing_price pattern) and skip this holding's
                # contribution rather than 500ing the whole replay.
                warning = f"missing_fx:{current.isoformat()}"
                if warning not in seen_warnings:
                    warnings.append(warning)
                    seen_warnings.add(warning)
                continue

        daily_points.append(DailyPoint(date=current, value=_quantize_value(total)))

        if include_cost_basis:
            # Transaction-time FX (cost-basis-line-drifts-daily): convert each
            # open buy lot's EUR cost basis at ITS OWN transaction-date EUR/USD
            # rate and sum in display currency, so the cost-basis line only steps
            # on transaction days in every display currency instead of drifting
            # daily with FX on no-transaction days. EUR display short-circuits
            # (from==to) so the per-lot sum equals the EUR basis — byte-identical
            # to the prior whole-sum _cost_basis_at conversion. Kept consistent
            # with contributions.get_cost_basis_series so both endpoints agree.
            cost_display = ZERO
            for buy_date, open_eur in _open_lots_at(
                cost_basis_buys, cost_basis_allocations, current
            ):
                try:
                    cost_display += _convert_amount(
                        amount=open_eur,
                        from_currency="EUR",
                        to_currency=display_currency,
                        fx_by_date=fx_by_date,
                        as_of=buy_date,
                    )
                except MissingFxRateError:
                    # Same degrade-gracefully posture as the value loop above:
                    # surface a per-day missing_fx warning (deduped) and add
                    # this lot's EUR amount unconverted so the chart still draws.
                    warning = f"missing_fx:{current.isoformat()}"
                    if warning not in seen_warnings:
                        warnings.append(warning)
                        seen_warnings.add(warning)
                    cost_display += open_eur
            daily_cost_basis.append(
                DailyPoint(date=current, value=_quantize_value(cost_display))
            )

        current += timedelta(days=1)

    aggregation = _aggregation_for_range(timeframe, (range_end - range_start).days)
    aggregated_points = aggregate_points(daily_points, timeframe, range_start, range_end)
    # Aggregate cost-basis using the same bucket policy as
    # the value series so dates align bucket-for-bucket on the chart x-axis.
    aggregated_cost_basis = (
        aggregate_points(daily_cost_basis, timeframe, range_start, range_end)
        if include_cost_basis
        else []
    )
    # Marker x-positions need to land on a real point.date so the
    # frontend's exact-date find() places them on the line, not at y=0 on the
    # x-axis (and so axis-trigger tooltips actually surface them). Each
    # aggregated point IS a bucket anchor — invert into a {key: anchor_date}
    # map and snap every marker to the anchor of the bucket it falls in.
    anchors_by_key = {_bucket_key(p.date, aggregation): p.date for p in aggregated_points}

    marker_txns = [txn for txn in transactions if range_start <= txn.date <= range_end]
    for txn in marker_txns:
        setattr(txn, "_networth_instrument", instruments.get(txn.instrument_id))

    markers = build_markers(
        marker_txns,
        aggregation,
        display_currency=display_currency,
        fx_by_date=fx_by_date,
    )
    markers = _snap_marker_dates(markers, anchors_by_key, aggregation)

    return NetWorthSeries(
        points=aggregated_points,
        markers=markers,
        aggregation=aggregation,
        warnings=warnings,
        cost_basis_series=aggregated_cost_basis,
    )


def aggregate_points(
    points: list[DailyPoint], timeframe: str, start: date, end: date
) -> list[NetWorthPoint]:
    aggregation = _aggregation_for_range(timeframe, (end - start).days)
    if aggregation == "daily":
        return [NetWorthPoint(date=point.date, value=point.value) for point in points]

    # Each key maps to the *last* daily replay point that falls in the bucket.
    # Iteration order is document order (Python ≥3.7 dicts), so overwriting a key
    # does NOT re-anchor its position — grouped.values() is always in
    # earliest-bucket-first order, matching the chart x-axis.
    # Choosing the last point rather than the first is intentional: for a partial
    # week or month the displayed value is "current as of latest available day"
    # rather than "value as of bucket start", which is the more useful default
    # for a portfolio dashboard.
    grouped: dict[object, DailyPoint] = {}
    for point in points:
        grouped[_bucket_key(point.date, aggregation)] = point

    return [NetWorthPoint(date=point.date, value=point.value) for point in grouped.values()]


def build_markers(
    transactions: list[Transaction],
    aggregation: str,
    *,
    display_currency: str = "EUR",
    fx_by_date: dict[date, Decimal] | None = None,
) -> list[NetWorthMarker]:
    """Construct chart markers in the requested display currency.

    Buys use `cost_basis_eur` as the EUR-denominated value, then convert.
    Sells don't carry `cost_basis_eur` (FIFO consumption is recorded in
    lot_alloc), so compute gross proceeds from `quantity * unit_price /
    fx_rate_to_eur`. Yield buckets sum `cost_basis_eur` across the rollup.
    """
    fx_by_date = fx_by_date or {}
    markers: list[NetWorthMarker] = []
    yield_groups: dict[tuple[object, str], list[Transaction]] = defaultdict(list)

    for txn in transactions:
        if txn.txn_type == "buy":
            # cost_basis_eur is the canonical buy value, but it can be
            # null/zero on legacy or back-dated imports that only recorded
            # share counts. Fall back to `quantity * unit_price / fx_rate`
            # — the same formula sells use below — so the tooltip surfaces
            # a meaningful number when at least the trade-time price is on
            # the row. Final fallback is ZERO (truly unpriced rows). The
            # `>= ZERO` test is intentional: a legitimate cost_basis_eur is
            # always non-negative, so a negative value (corrupted import)
            # falls through to the unit_price branch instead of being
            # silently surfaced.
            if txn.cost_basis_eur is not None and txn.cost_basis_eur > ZERO:
                value_eur = txn.cost_basis_eur
            elif txn.unit_price is not None and txn.fx_rate_to_eur is not None:
                value_eur = abs(txn.quantity) * txn.unit_price / txn.fx_rate_to_eur
            else:
                value_eur = ZERO
            instrument = _txn_instrument(txn)
            markers.append(
                NetWorthMarker(
                    date=txn.date,
                    type="buy",
                    instrument_id=txn.instrument_id,
                    instrument_symbol=instrument.symbol if instrument is not None else None,
                    instrument_type=instrument.instrument_type if instrument is not None else None,
                    display_decimals=instrument.display_decimals if instrument is not None else None,
                    quantity=txn.quantity,
                    value=_convert_marker_value(value_eur, display_currency, fx_by_date, txn.date),
                    count=1,
                )
            )
        elif txn.txn_type == "sell":
            # Sells don't stamp cost_basis_eur (FIFO via lot_alloc).
            # Gross proceeds = |quantity| * unit_price / fx_rate_to_eur.
            proceeds_eur = ZERO
            if txn.unit_price is not None and txn.fx_rate_to_eur is not None:
                proceeds_eur = abs(txn.quantity) * txn.unit_price / txn.fx_rate_to_eur
            instrument = _txn_instrument(txn)
            markers.append(
                NetWorthMarker(
                    date=txn.date,
                    type="sell",
                    instrument_id=txn.instrument_id,
                    instrument_symbol=instrument.symbol if instrument is not None else None,
                    instrument_type=instrument.instrument_type if instrument is not None else None,
                    display_decimals=instrument.display_decimals if instrument is not None else None,
                    quantity=txn.quantity,
                    value=_convert_marker_value(
                        proceeds_eur, display_currency, fx_by_date, txn.date
                    ),
                    count=1,
                )
            )
        elif txn.txn_type == "yield":
            # Rollup yields by the SAME bucket as the chart aggregation,
            # not by an independent timeframe-based scheme. Otherwise after
            # marker date-snapping, two distinct rollup groups (e.g. Jan-month
            # and Feb-month under the old timeframe="1y" yearly→monthly rollup)
            # can collide on a single weekly anchor and stack as duplicate
            # markers on the same x-position.
            yield_groups[(_bucket_key(txn.date, aggregation), txn.instrument_id)].append(txn)

    for (_, instrument_id), txns in yield_groups.items():
        first = min(txns, key=lambda item: item.date)
        instrument = _txn_instrument(first)
        value_eur = sum((txn.cost_basis_eur or ZERO for txn in txns), ZERO)
        markers.append(
            NetWorthMarker(
                date=first.date,
                type="yield",
                instrument_id=instrument_id,
                instrument_symbol=instrument.symbol if instrument is not None else None,
                instrument_type=instrument.instrument_type if instrument is not None else None,
                display_decimals=instrument.display_decimals if instrument is not None else None,
                quantity=sum((txn.quantity for txn in txns), ZERO),
                value=_convert_marker_value(
                    value_eur, display_currency, fx_by_date, first.date
                ),
                count=len(txns),
            )
        )

    return sorted(markers, key=lambda marker: (marker.date, marker.type))


def _convert_marker_value(
    value_eur: Decimal,
    display_currency: str,
    fx_by_date: dict[date, Decimal],
    as_of: date,
) -> Decimal:
    """Convert a EUR-denominated marker value into the chart's display currency.

    Falls back to the EUR amount when the FX cache has no row at-or-before
    `as_of`, preserving the "degrade gracefully" stance for markers.
    """
    if display_currency == "EUR" or value_eur == ZERO:
        return value_eur
    try:
        return _convert_amount(
            amount=value_eur,
            from_currency="EUR",
            to_currency=display_currency,
            fx_by_date=fx_by_date,
            as_of=as_of,
        )
    except MissingFxRateError:
        return value_eur


def _apply_tag_exists_subquery(stmt, tag_filter: str | None):
    """Mirror the (account_id, instrument_id, tag.name) exists-subquery used
    in services/contributions.py so /api/networth applies the SAME filter
    semantics. No-op when ``tag_filter`` is None — caller stays unchanged.
    """
    if tag_filter is None:
        return stmt
    return stmt.where(
        select(HoldingTag.account_id)
        .join(Tag, Tag.id == HoldingTag.tag_id)
        .where(
            HoldingTag.account_id == Transaction.account_id,
            HoldingTag.instrument_id == Transaction.instrument_id,
            Tag.name == tag_filter,
        )
        .exists()
    )


async def _resolve_range(
    session: AsyncSession,
    timeframe: str,
    start: date | None,
    end: date | None,
    *,
    instrument_ids: list[str] | None = None,
    tag_filter: str | None = None,
) -> tuple[date | None, date]:
    range_end = end or clock.today()
    if timeframe == "custom":
        if start is None or end is None:
            raise ValueError("custom timeframe requires start and end")
        if start > end:
            raise ValueError("start must be on or before end")
        return start, end
    if timeframe == "all":
        stmt = select(func.min(Transaction.date)).where(Transaction.deleted_at.is_(null()))
        # Narrow the "first txn" lookup to the selected
        # instruments so the range starts at their earliest activity, not
        # the portfolio's earliest activity.
        if instrument_ids:
            stmt = stmt.where(Transaction.instrument_id.in_(instrument_ids))
        # When a tag filter is set, the "all" range starts
        # at the earliest TAGGED activity, not the portfolio's earliest day.
        stmt = _apply_tag_exists_subquery(stmt, tag_filter)
        result = await session.execute(stmt)
        first_txn = result.scalar_one_or_none()
        return first_txn, range_end
    days = TIMEFRAMES[timeframe]
    if days is None:
        return None, range_end
    return range_end - timedelta(days=days), range_end


async def _load_transactions(
    session: AsyncSession,
    end: date,
    *,
    instrument_ids: list[str] | None = None,
    tag_filter: str | None = None,
) -> list[Transaction]:
    stmt = (
        select(Transaction)
        .where(Transaction.date <= end)
        .where(Transaction.deleted_at.is_(None))
        .order_by(Transaction.date.asc(), Transaction.created_at.asc())
    )
    # Empty list = no filter (full portfolio); a non-empty
    # list narrows to those instruments via SQL IN(). Same semantics as the
    # service-layer guard.
    if instrument_ids:
        stmt = stmt.where(Transaction.instrument_id.in_(instrument_ids))
    # Tag filter applies to BOTH the value-replay txn set
    # AND the marker stream — both consume the result of this single load,
    # so the chart stays internally consistent with no second filter pass.
    stmt = _apply_tag_exists_subquery(stmt, tag_filter)
    result = await session.execute(stmt)
    return list(result.scalars())


async def _load_instruments(session: AsyncSession) -> dict[str, Instrument]:
    result = await session.execute(select(Instrument))
    return {instrument.id: instrument for instrument in result.scalars()}


async def _load_quotes(
    session: AsyncSession, end: date
) -> dict[str, list[PriceQuote]]:
    stmt = (
        select(PriceQuote)
        .where(PriceQuote.date <= end)
        .order_by(
            PriceQuote.instrument_id.asc(),
            PriceQuote.date.asc(),
            case((PriceQuote.source == "manual", 1), else_=0).asc(),
            PriceQuote.fetched_at.asc(),
        )
    )
    result = await session.execute(stmt)
    quotes_by_instrument: dict[str, list[PriceQuote]] = defaultdict(list)
    for quote in result.scalars():
        quotes_by_instrument[quote.instrument_id].append(quote)
    return quotes_by_instrument


async def _load_fx(session: AsyncSession, end: date) -> dict[date, Decimal]:
    stmt = (
        select(FxRate)
        .where(
            FxRate.base_currency == "EUR",
            FxRate.quote_currency == "USD",
            FxRate.date <= end,
        )
        .order_by(FxRate.date.asc(), FxRate.fetched_at.asc())
    )
    result = await session.execute(stmt)
    return {rate.date: rate.rate for rate in result.scalars()}


def _synthetic_quote_on_or_before(
    priced_txns: list[Transaction], as_of: date
) -> tuple[Decimal, str] | None:
    """Return (unit_price, price_currency) of the most-recent priced txn ≤ as_of.

    Mirrors `_quote_on_or_before` semantics. Caller pre-filters `priced_txns`
    to those carrying both `unit_price` and `price_currency`, so the None
    re-checks below are belt-and-braces.
    """
    eligible = [t for t in priced_txns if t.date <= as_of]
    if not eligible:
        return None
    txn = eligible[-1]
    if txn.unit_price is None or txn.price_currency is None:
        return None
    return txn.unit_price, txn.price_currency


def _convert_amount(
    *,
    amount: Decimal,
    from_currency: str,
    to_currency: str,
    fx_by_date: dict[date, Decimal],
    as_of: date,
) -> Decimal:
    if from_currency == to_currency:
        return amount
    # Load the rate from the in-memory fx_by_date map (networth's batch-loaded
    # FX cache), then delegate the arithmetic to the shared `convert` helper so
    # the EUR<->USD direction logic lives in exactly one place.
    fx_rate = _fx_on_or_before(fx_by_date, as_of)
    return convert(amount, from_currency, to_currency, fx_rate)


def _fx_on_or_before(fx_by_date: dict[date, Decimal], as_of: date) -> Decimal:
    eligible = [fx_date for fx_date in fx_by_date if fx_date <= as_of]
    if not eligible:
        raise MissingFxRateError(f"missing EUR/USD FX rate for {as_of.isoformat()}")
    return fx_by_date[max(eligible)]


def _bucket_key(d: date, aggregation: str) -> object:
    """Return the bucket key a date falls in for a given aggregation.

    Single source of truth shared by ``aggregate_points`` (which uses it to
    collapse daily points into bucket-anchor points) and ``_snap_marker_dates``
    (which uses it to align marker dates onto those same anchors).
    """
    if aggregation == "weekly":
        iso = d.isocalendar()
        return ("week", iso.year, iso.week)
    if aggregation == "monthly":
        return ("month", d.year, d.month)
    return ("day", d.toordinal())


def _snap_marker_dates(
    markers: list[NetWorthMarker],
    anchors_by_key: dict[object, date],
    aggregation: str,
) -> list[NetWorthMarker]:
    """Rewrite each marker's date to its bucket-anchor date.

    Daily aggregation is a no-op (anchor == date). Markers whose bucket has
    no anchor in the map (shouldn't happen — the daily replay spans the full
    range — but defensive belt-and-braces) keep their original date.
    """
    if aggregation == "daily":
        return markers
    snapped: list[NetWorthMarker] = []
    for marker in markers:
        anchor = anchors_by_key.get(_bucket_key(marker.date, aggregation))
        if anchor is None or anchor == marker.date:
            snapped.append(marker)
        else:
            snapped.append(replace(marker, date=anchor))
    return snapped


def _aggregation_for_range(timeframe: str, days: int | None) -> str:
    if timeframe in {"1m", "3m"}:
        return "daily"
    if timeframe == "1y":
        return "weekly"
    if timeframe == "all":
        return "monthly"
    if days is not None and days <= 90:
        return "daily"
    if days is not None and days <= 365:
        return "weekly"
    return "monthly"


def _txn_instrument(txn: Transaction) -> Instrument | None:
    attached = getattr(txn, "_networth_instrument", None)
    if attached is not None:
        return attached
    return getattr(txn, "instrument", None)


def _quantize_value(value: Decimal) -> Decimal:
    return value.quantize(UNIT_SCALE)
