"""Shared quote / FX helpers used across the analytics services.

Extracted to break the cross-module private-import pattern where perf,
networth, realized, closed, contributions, and reconciliation each reached
into a sibling service for `_latest_quote`, `_convert_currency`,
`MissingFxRateError`, etc.

Behavior-preservation notes:
- `MissingFxRateError` was previously defined as two *separate* (but
  behaviorally identical, plain `LookupError`-subclass) classes in perf.py and
  networth.py. They are unified here; both modules now re-export this one.
- `convert` is the pure, rate-injected EUR<->USD conversion arithmetic shared
  by networth's `_convert_amount` and perf's `_convert_currency`. Each caller
  still owns how it *loads* the rate (networth from an in-memory fx_by_date
  map; perf via a per-call DB query) — only the arithmetic is shared.
- `latest_quote` / `latest_eur_usd_rate` / `convert_currency` / `first_buy_date`
  are perf's implementations, moved here verbatim so realized.py and closed.py
  can import them from a neutral module. perf.py keeps thin re-export aliases.
- `quote_on_or_before` is the networth/contributions list-scan helper (return
  the last quote whose date <= as_of, or None). The list ORDER is the caller's
  responsibility — see the per-module `_load_quotes` notes for the intentional
  manual-source tiebreak drift between networth and contributions.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Account, FxRate, HoldingTag, Instrument, PriceQuote, Tag, Transaction


class MissingFxRateError(LookupError):
    """Raised when no EUR/USD FX rate is available at or before the as-of date.

    Callers catch this per-row/per-day and surface it as a graceful warning
    (`twrr_reason="missing_fx"` in perf, `missing_fx:{date}` in networth)
    instead of letting it 500 the request.
    """


def convert(
    amount: Decimal,
    from_currency: str,
    to_currency: str,
    rate: Decimal,
) -> Decimal:
    """Pure EUR<->USD conversion given an already-resolved EUR/USD `rate`.

    `rate` is the EUR-base rate (USD per 1 EUR). Same identity / direction /
    unsupported-pair semantics as the former per-module copies.
    """
    if from_currency == to_currency:
        return amount
    if from_currency == "EUR" and to_currency == "USD":
        return amount * rate
    if from_currency == "USD" and to_currency == "EUR":
        return amount / rate
    raise ValueError(f"unsupported currency conversion: {from_currency}->{to_currency}")


def quote_on_or_before(quotes: list[PriceQuote], as_of: date) -> PriceQuote | None:
    """Return the last quote whose date <= as_of, or None.

    Assumes `quotes` is pre-sorted date-ascending by the caller (the per-module
    `_load_quotes` queries do this). The tail element is therefore the most
    recent eligible quote.
    """
    eligible = [quote for quote in quotes if quote.date <= as_of]
    if not eligible:
        return None
    return eligible[-1]


async def latest_quote(
    session: AsyncSession, instrument_id: str, as_of: date
) -> PriceQuote | None:
    """Most-relevant quote for an instrument at `as_of` (perf semantics).

    Manual same-date overrides win; otherwise the newest date wins, with a
    manual-source precedence tiebreak then newest fetched_at.
    """
    manual_today_stmt = (
        select(PriceQuote)
        .where(
            PriceQuote.instrument_id == instrument_id,
            PriceQuote.date == as_of,
            PriceQuote.source == "manual",
        )
        .order_by(PriceQuote.fetched_at.desc())
        .limit(1)
    )
    manual_result = await session.execute(manual_today_stmt)
    manual_today = manual_result.scalar_one_or_none()
    if manual_today is not None:
        return manual_today

    stmt = (
        select(PriceQuote)
        .where(PriceQuote.instrument_id == instrument_id, PriceQuote.date <= as_of)
        .order_by(
            PriceQuote.date.desc(),
            case((PriceQuote.source == "manual", 0), else_=1).asc(),
            PriceQuote.fetched_at.desc(),
        )
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def latest_eur_usd_rate(session: AsyncSession, as_of: date) -> Decimal:
    stmt = (
        select(FxRate)
        .where(
            FxRate.base_currency == "EUR",
            FxRate.quote_currency == "USD",
            FxRate.date <= as_of,
        )
        .order_by(FxRate.date.desc(), FxRate.fetched_at.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    fx = result.scalar_one_or_none()
    if fx is None:
        raise MissingFxRateError("missing EUR/USD FX rate")
    return fx.rate


async def convert_currency(
    session: AsyncSession,
    amount: Decimal,
    from_currency: str,
    to_currency: str,
    as_of: date,
) -> Decimal:
    """DB-backed EUR<->USD conversion: loads the rate itself, then converts."""
    if from_currency == to_currency:
        return amount
    rate = await latest_eur_usd_rate(session, as_of)
    return convert(amount, from_currency, to_currency, rate)


async def load_holdings(
    session: AsyncSession, tag_filter: str | None = None
) -> tuple[
    list[tuple[str, str]],
    dict[str, Account],
    dict[str, Instrument],
]:
    """Return distinct (account_id, instrument_id) holdings plus batch-loaded
    Account/Instrument maps, optionally narrowed to a tag.

    Extracted verbatim from the identical blocks in perf.get_performance_rows,
    closed.get_closed_positions, and allocation.get_allocation_slices. Returns:
        (holdings, accounts_by_id, instruments_by_id)
    where `holdings` preserves the DB group-by row order. The N+1-avoiding
    batch load (two IN() queries) is preserved exactly.
    """
    holding_stmt = (
        select(Transaction.account_id, Transaction.instrument_id)
        .where(Transaction.deleted_at.is_(None))
        .group_by(Transaction.account_id, Transaction.instrument_id)
    )
    holding_result = await session.execute(holding_stmt)
    holdings = list(holding_result.all())

    if tag_filter is not None:
        tag_stmt = (
            select(HoldingTag.account_id, HoldingTag.instrument_id)
            .join(Tag, Tag.id == HoldingTag.tag_id)
            .where(Tag.name == tag_filter)
        )
        tag_result = await session.execute(tag_stmt)
        tagged_holdings = {
            (account_id, instrument_id) for account_id, instrument_id in tag_result.all()
        }
        holdings = [holding for holding in holdings if holding in tagged_holdings]

    account_ids = {account_id for account_id, _ in holdings}
    instrument_ids = {instrument_id for _, instrument_id in holdings}
    accounts_by_id: dict[str, Account] = {}
    instruments_by_id: dict[str, Instrument] = {}
    if account_ids:
        result = await session.execute(select(Account).where(Account.id.in_(account_ids)))
        accounts_by_id = {a.id: a for a in result.scalars()}
    if instrument_ids:
        result = await session.execute(
            select(Instrument).where(Instrument.id.in_(instrument_ids))
        )
        instruments_by_id = {i.id: i for i in result.scalars()}

    return holdings, accounts_by_id, instruments_by_id


async def first_buy_date(
    session: AsyncSession, account_id: str, instrument_id: str
) -> date | None:
    # quantity is TEXT-backed (DecimalText): the qty>0 sign filter moves to
    # Python (a SQL comparison would type-juggle the text against a Decimal).
    # min(date) is computed in Python over the buy/adjustment rows that survive
    # the sign filter.
    stmt = select(Transaction.date, Transaction.quantity).where(
        Transaction.account_id == account_id,
        Transaction.instrument_id == instrument_id,
        Transaction.txn_type.in_(("buy", "adjustment")),
        Transaction.deleted_at.is_(None),
    )
    result = await session.execute(stmt)
    buy_dates = [d for d, qty in result if qty > Decimal("0")]
    return min(buy_dates) if buy_dates else None
