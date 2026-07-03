"""Frankfurter EUR/USD client + fx_rate cache layer.

Frankfurter automatically walks back to the last published business day on
weekends/holidays — the response.date field tells us the actual date supplying
the rate. We log it for audit trail honesty.

One Frankfurter call per non-cached txn save. No batch import.
The fx_rate table is the time-series cache; transaction.fx_rate_to_eur is the
immutable per-txn copy. Once a fx_rate row exists for (date, base, quote), it is
the source of truth for that day.

Caller-commits contract (mirrors backend/app/services/fifo.py):
    Must be called INSIDE an open DB transaction (caller is responsible for
    commit). The service stages new rows via session.add(...) but never calls
    session.commit() / session.rollback().
"""
from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal, InvalidOperation

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.fx_rate import FxRate
from app.schemas.fx_rate import VALID_CURRENCIES
from app.services.pricing.errors import parse_positive_decimal, raise_for_provider_response

logger = logging.getLogger(__name__)
FRANKFURTER_BASE_URL = "https://api.frankfurter.dev/v1"


async def fetch_fx_rate(
    client: httpx.AsyncClient,
    on_date: date,
    base: str = "EUR",
    quote: str = "USD",
) -> tuple[Decimal, date]:
    """Fetch (rate, actual_date) from Frankfurter.

    Walk-back is implicit: when `on_date` is a weekend/holiday, Frankfurter
    returns the last published business day's rate and reports that date in
    response.date. We surface this as the second tuple element.
    """
    if base not in VALID_CURRENCIES or quote not in VALID_CURRENCIES:
        raise ValueError(
            f"only {VALID_CURRENCIES} supported; got base={base}, quote={quote}"
        )
    if base == quote:
        raise ValueError(
            f"base==quote ({base}); identity rate is 1.0 — caller should skip Frankfurter"
        )

    url = f"{FRANKFURTER_BASE_URL}/{on_date.isoformat()}"
    params = {"base": base, "symbols": quote}
    try:
        resp = await client.get(
            url, params=params, timeout=settings.pricing_timeout_seconds
        )
    except httpx.HTTPError as e:
        # Strip URL/params from the message to avoid leaking host/key in logs
        raise ValueError(f"frankfurter network error: {type(e).__name__}") from None

    if resp.status_code == 404:
        raise ValueError(
            f"frankfurter has no data for {on_date} (pre-1999 or future)"
        )
    # Classify rate-limit (429) so the scheduler/caller can back off
    # rather than seeing an opaque "frankfurter http 429". frankfurter has
    # always raised a plain ValueError (not PriceProviderRateLimited) on 429,
    # so pass rate_limited_exc=ValueError to keep that behavior byte-identical.
    raise_for_provider_response(resp, provider="frankfurter", rate_limited_exc=ValueError)

    payload = resp.json()
    rates = payload.get("rates", {})
    raw = rates.get(quote)
    if raw is None:
        raise ValueError(f"frankfurter response missing rates.{quote}")
    # NOTE: intentionally NOT routed through parse_positive_decimal. This
    # single-date path's messages have no "for {context}" segment
    # ("frankfurter non-numeric rate: {raw}"), unlike the range path's
    # _parse_positive_fx_decimal (and the shared helper), which include it.
    # Unifying would change these strings byte-for-byte.
    try:
        rate = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        raise ValueError(f"frankfurter non-numeric rate: {raw}") from None
    if not rate.is_finite() or rate <= 0:
        raise ValueError(f"frankfurter non-positive rate: {raw}")

    actual_date = date.fromisoformat(payload["date"])
    if actual_date != on_date:
        logger.info(
            "fx_walkback_used requested=%s actual=%s base=%s quote=%s",
            on_date.isoformat(),
            actual_date.isoformat(),
            base,
            quote,
        )
    return rate, actual_date


async def fetch_fx_range(
    client: httpx.AsyncClient,
    start: date,
    end: date,
    base: str = "EUR",
    quote: str = "USD",
) -> list[tuple[date, Decimal]]:
    """Fetch daily FX rates from Frankfurter's range endpoint."""
    if base not in VALID_CURRENCIES or quote not in VALID_CURRENCIES:
        raise ValueError(
            f"only {VALID_CURRENCIES} supported; got base={base}, quote={quote}"
        )
    if base == quote:
        raise ValueError(
            f"base==quote ({base}); identity rate is 1.0 — caller should skip Frankfurter"
        )
    if start > end:
        raise ValueError(f"start date {start} is after end date {end}")

    url = f"{FRANKFURTER_BASE_URL}/{start.isoformat()}..{end.isoformat()}"
    params = {"base": base, "symbols": quote}
    try:
        resp = await client.get(
            url, params=params, timeout=settings.pricing_timeout_seconds
        )
    except httpx.HTTPError as e:
        raise ValueError(f"frankfurter network error: {type(e).__name__}") from None

    if resp.status_code == 404:
        raise ValueError(
            f"frankfurter has no data for {start}..{end} (pre-1999 or future)"
        )
    # Classify rate-limit (429) so backfill/scheduler callers can
    # distinguish "back off and retry" from a permanent error. As in
    # fetch_fx_rate, frankfurter raises a plain ValueError on 429.
    raise_for_provider_response(resp, provider="frankfurter", rate_limited_exc=ValueError)

    payload = resp.json()
    rates = payload.get("rates")
    if not rates:
        raise ValueError("frankfurter range response missing rates")

    history: list[tuple[date, Decimal]] = []
    for day, quote_rates in sorted(rates.items()):
        raw = quote_rates.get(quote)
        if raw is None:
            raise ValueError(f"frankfurter response missing rates.{day}.{quote}")
        history.append(
            (
                date.fromisoformat(day),
                parse_positive_decimal(
                    raw,
                    provider="frankfurter",
                    noun="rate",
                    context=f"{day} {base}/{quote}",
                    raw_in_nonnumeric=True,
                ),
            )
        )
    return history


async def get_or_fetch_fx_rate(
    session: AsyncSession,
    client: httpx.AsyncClient,
    on_date: date,
    base: str = "EUR",
    quote: str = "USD",
) -> FxRate:
    """Cache-first FX rate retrieval.

    1. SELECT from fx_rate by (on_date, base, quote). Hit → return cached row,
       no HTTP call.
    2. Miss → call fetch_fx_rate (walk-back implicit). If the actual_date that
       supplied the rate already has a cached row (e.g. weekend lookup hits an
       already-cached Friday), return that row instead of inserting a duplicate.
    3. Otherwise session.add(FxRate(...)) and return the new row. The caller
       MUST commit.
    """
    if base == quote:
        raise ValueError(
            "base==quote — caller should use Decimal('1') without DB lookup"
        )

    # Cache hit: exact requested date
    stmt = select(FxRate).where(
        FxRate.date == on_date,
        FxRate.base_currency == base,
        FxRate.quote_currency == quote,
    )
    result = await session.execute(stmt)
    cached = result.scalar_one_or_none()
    if cached is not None:
        return cached

    # Cache miss: fetch from Frankfurter (walk-back implicit)
    rate, actual_date = await fetch_fx_rate(client, on_date, base, quote)

    # Walk-back dedupe: actual_date may already be cached
    if actual_date != on_date:
        stmt2 = select(FxRate).where(
            FxRate.date == actual_date,
            FxRate.base_currency == base,
            FxRate.quote_currency == quote,
        )
        result2 = await session.execute(stmt2)
        existing = result2.scalar_one_or_none()
        # Cache the rate under the requested (weekend/holiday) date too,
        # mirroring the actual_date row. Otherwise every Saturday/Sunday lookup
        # re-hits Frankfurter even though we already know which Friday rate
        # applies, defeating the cache.
        stub_row = FxRate(
            date=on_date,
            base_currency=base,
            quote_currency=quote,
            rate=existing.rate if existing is not None else rate,
            source="frankfurter",
        )
        session.add(stub_row)
        if existing is not None:
            return existing
        # No row at actual_date yet — also stage one so subsequent lookups
        # by either the actual date or any other walked-back date hit the
        # cache directly.
        actual_row = FxRate(
            date=actual_date,
            base_currency=base,
            quote_currency=quote,
            rate=rate,
            source="frankfurter",
        )
        session.add(actual_row)
        return actual_row

    new_row = FxRate(
        date=actual_date,
        base_currency=base,
        quote_currency=quote,
        rate=rate,
        source="frankfurter",
    )
    session.add(new_row)
    return new_row
