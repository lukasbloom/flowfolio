"""Request-scoped market-data snapshot (quotes + FX).

Collapses the per-holding ``quotes.latest_quote`` / per-day ``quotes.convert_currency``
DB round-trips in the dashboard read-path services into TWO bulk loads per request
(latest-quote-per-instrument + EUR/USD FX-by-date), then serves every lookup from
memory with pure accessors. Mirrors networth.py's ``_load_quotes``/``_load_fx`` pattern.

BYTE-IDENTITY CONTRACT: the in-memory accessors must return the SAME row / rate /
arithmetic the per-call helpers in ``app.services.quotes`` return — see the
per-accessor docstrings. Two distinct FX resolution modes coexist on purpose:

  - Single-``as_of`` consumers (perf / closed*-NOTE / allocation / concentration)
    convert everything at one date, so ``eur_usd_rate()`` / ``convert()`` resolve
    against the snapshot's ``as_of``.
  - Per-date consumers (contributions' daily cost-basis loop; closed's per-row
    ``last_close_date``) need the rate AT A SPECIFIC DATE, so ``convert(...,
    as_of=...)`` resolves the at-or-before-date rate from the preloaded
    ``{date: rate}`` map — identical to networth's ``_fx_on_or_before`` semantics.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import FxRate, PriceQuote
from app.services.quotes import MissingFxRateError, convert


@dataclass
class MarketDataSnapshot:
    """Immutable-by-convention bundle of latest-quote-per-instrument + FX-by-date.

    Built once per request via :func:`load_market_data`. ``as_of`` is the default
    conversion date for the single-rate consumers; per-date consumers pass an
    explicit ``as_of`` to :meth:`convert`.
    """

    as_of: date
    # instrument_id -> the PriceQuote that quotes.latest_quote(session, id, as_of)
    # would return (or absent if that helper would return None).
    _latest_quote_by_instrument: dict[str, PriceQuote] = field(default_factory=dict)
    # EUR/USD rate-by-date, ordered ascending keys — for at-or-before resolution.
    _fx_by_date: dict[date, Decimal] = field(default_factory=dict)

    # ---- quote accessor -------------------------------------------------
    def latest_quote(self, instrument_id: str) -> PriceQuote | None:
        """Return the same row ``quotes.latest_quote(session, instrument_id, as_of)``
        would return: manual same-date override wins; else newest date, then
        manual-source precedence, then newest fetched_at. See
        :func:`load_market_data` for how the winner is selected at load time.
        """
        return self._latest_quote_by_instrument.get(instrument_id)

    # ---- FX accessors ---------------------------------------------------
    def _rate_on_or_before(self, as_of: date) -> Decimal:
        """At-or-before-date EUR/USD rate. Mirrors networth._fx_on_or_before:
        the rate of the latest date <= as_of. Raises MissingFxRateError when none.
        """
        eligible = [d for d in self._fx_by_date if d <= as_of]
        if not eligible:
            raise MissingFxRateError(
                f"missing EUR/USD FX rate for {as_of.isoformat()}"
            )
        return self._fx_by_date[max(eligible)]

    def eur_usd_rate(self) -> Decimal:
        """EUR/USD rate at the snapshot's ``as_of`` (single-rate consumers).

        Identical to ``quotes.latest_eur_usd_rate(session, as_of)``: raises
        MissingFxRateError when no rate at-or-before ``as_of`` exists.
        """
        return self._rate_on_or_before(self.as_of)

    def sorted_fx_dates(self) -> list[date]:
        """Ascending FX dates, for a forward-cursor / bisect at-or-before lookup
        by per-date consumers that resolve many dates against the same map."""
        return sorted(self._fx_by_date)

    def rate_at(self, d: date) -> Decimal:
        """The EUR/USD rate stored for the EXACT date ``d`` (a key of the map).
        Pair with :meth:`sorted_fx_dates` for O(1)/O(log n) at-or-before lookups
        without the per-call linear scan in :meth:`_rate_on_or_before`."""
        return self._fx_by_date[d]

    def convert(
        self,
        amount: Decimal,
        from_currency: str,
        to_currency: str,
        as_of: date | None = None,
    ) -> Decimal:
        """EUR<->USD conversion matching ``quotes.convert_currency`` arithmetic.

        ``as_of`` defaults to the snapshot date (single-rate consumers). Per-date
        consumers (contributions' daily loop, closed's per-row last_close_date)
        pass an explicit ``as_of`` so the at-or-before-date rate is used — exactly
        as networth's per-day ``_convert_amount`` does.
        """
        if from_currency == to_currency:
            return amount
        rate = self._rate_on_or_before(self.as_of if as_of is None else as_of)
        return convert(amount, from_currency, to_currency, rate)


async def load_market_data(
    session: AsyncSession,
    *,
    as_of: date,
    instrument_ids: set[str] | None = None,
) -> MarketDataSnapshot:
    """Build a :class:`MarketDataSnapshot` with two bulk queries.

    1. All PriceQuotes with ``date <= as_of`` (optionally narrowed to
       ``instrument_ids``), reduced to one winner per instrument applying the
       EXACT precedence ``quotes.latest_quote`` uses.
    2. All EUR/USD FxRate rows with ``date <= as_of`` → ``{date: rate}`` map.
    """
    # ---- 1. Latest quote per instrument --------------------------------
    quote_stmt = select(PriceQuote).where(PriceQuote.date <= as_of)
    if instrument_ids is not None:
        quote_stmt = quote_stmt.where(PriceQuote.instrument_id.in_(instrument_ids))
    quote_result = await session.execute(quote_stmt)

    # Group all eligible quotes per instrument, then reduce to the single winner
    # that quotes.latest_quote would pick. That helper's precedence is:
    #   (a) a manual quote dated EXACTLY as_of wins outright (newest fetched_at
    #       among such manual-today rows);
    #   (b) otherwise: newest date, then manual-source precedence
    #       (case(source=="manual",0) else 1 ASC → manual wins), then newest
    #       fetched_at.
    # We replicate (a)+(b) exactly. Sort key below mirrors the ORDER BY clauses.
    grouped: dict[str, list[PriceQuote]] = defaultdict(list)
    for q in quote_result.scalars():
        grouped[q.instrument_id].append(q)

    latest_by_instrument: dict[str, PriceQuote] = {}
    for instrument_id, quotes in grouped.items():
        # (a) manual same-date override: a manual quote dated exactly as_of,
        #     newest fetched_at. latest_quote runs this as a separate LIMIT-1
        #     query that short-circuits before the general ordering.
        manual_today = [
            q for q in quotes if q.date == as_of and q.source == "manual"
        ]
        if manual_today:
            latest_by_instrument[instrument_id] = max(
                manual_today, key=lambda q: q.fetched_at
            )
            continue
        # (b) general winner. Replicate ORDER BY:
        #     date DESC, (manual→0 else 1) ASC, fetched_at DESC → take first.
        #     As a max() key (all DESC except manual-precedence which is ASC):
        #     larger date wins; among equal dates manual (0) wins over non-manual
        #     (1) → encode as 1 for manual / 0 for non-manual so larger wins;
        #     then larger fetched_at wins.
        latest_by_instrument[instrument_id] = max(
            quotes,
            key=lambda q: (
                q.date,
                1 if q.source == "manual" else 0,
                q.fetched_at,
            ),
        )

    # ---- 2. EUR/USD FX-by-date -----------------------------------------
    fx_stmt = (
        select(FxRate)
        .where(
            FxRate.base_currency == "EUR",
            FxRate.quote_currency == "USD",
            FxRate.date <= as_of,
        )
        .order_by(FxRate.date.asc(), FxRate.fetched_at.asc())
    )
    fx_result = await session.execute(fx_stmt)
    # Last write per date wins — matches networth._load_fx ({rate.date: rate.rate}
    # built from a date-asc, fetched_at-asc ordering, so the newest fetched_at on
    # a given date is the final value retained).
    fx_by_date: dict[date, Decimal] = {}
    for rate in fx_result.scalars():
        fx_by_date[rate.date] = rate.rate

    return MarketDataSnapshot(
        as_of=as_of,
        _latest_quote_by_instrument=latest_by_instrument,
        _fx_by_date=fx_by_date,
    )
