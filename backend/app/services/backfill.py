"""Historical price and FX cache backfill services."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fx_rate import FxRate
from app.models.instrument import Instrument
from app.models.price_quote import PriceQuote
from app.services.fx import fetch_fx_range
from app.services.pricing.alpha_vantage import fetch_alpha_vantage_history
from app.services.pricing.binance import fetch_binance_history
from app.services.pricing.coingecko import fetch_coingecko_history
from app.services.pricing.errors import PriceProviderRateLimited
from app.services.pricing.twelve_data import fetch_twelve_data_history
from app.services.pricing.yahoo import fetch_yahoo_history, resolve_yahoo_symbol

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BackfillResult:
    instrument_id: str
    status: str
    inserted_prices: int
    skipped_existing: int
    start: date
    end: date


async def backfill_instrument_history(
    session: AsyncSession,
    client: httpx.AsyncClient,
    instrument: Instrument,
    start: date,
    end: date,
) -> BackfillResult:
    """Populate historical `price_quote` rows for one instrument.

    The caller owns transaction boundaries. This function stages rows with
    `session.add(...)` and intentionally does not commit.
    """
    if start > end:
        raise ValueError(f"start date {start} is after end date {end}")

    if instrument.price_source == "manual":
        return BackfillResult(
            instrument_id=instrument.id,
            status="manual_history_required",
            inserted_prices=0,
            skipped_existing=0,
            start=start,
            end=end,
        )

    if instrument.price_source == "ft":
        # History/live split, mirroring finnhub→twelve_data: FT.com is the LIVE
        # source (ft_scraper), but it serves no history, so backfill uses Yahoo
        # (history-only — never the daily scheduler; see services/pricing/yahoo.py).
        # ETFs/metals resolve by exchange ticker, funds by ISIN→Morningstar NAV.
        yahoo_symbol = await resolve_yahoo_symbol(client, instrument.ticker_override)
        if yahoo_symbol is None:
            return BackfillResult(
                instrument_id=instrument.id,
                status="manual_history_required",
                inserted_prices=0,
                skipped_existing=0,
                start=start,
                end=end,
            )
        history = await fetch_yahoo_history(client, yahoo_symbol, start, end)
        source = "yahoo"
        price_currency = "EUR"  # fetch_yahoo_history asserts the chart currency is EUR
    elif instrument.price_source == "finnhub":
        # Twelve Data is the primary stock-history source (free tier:
        # 800/day, 8/min — 32x Alpha Vantage's headroom + EU-listing coverage).
        # Alpha Vantage stays as a fallback for any ticker Twelve Data misses
        # or when Twelve Data itself errors. Either way, the per-row `source`
        # tag matches the provider that actually supplied the data.
        symbol = instrument.ticker_override or instrument.symbol
        try:
            history = await fetch_twelve_data_history(client, symbol)
            source = "twelve_data"
        except PriceProviderRateLimited:
            # Don't fall back on rate-limit — Alpha Vantage's 25/day quota
            # is more precious than Twelve Data's 800/day, and the rate
            # limit will reset in seconds. Surface the 429 so the user
            # retries instead of silently burning the AV budget.
            raise
        except ValueError as td_err:
            logger.warning(
                "backfill_twelve_data_failed_falling_back",
                extra={"symbol": symbol, "err": str(td_err)},
            )
            history = await fetch_alpha_vantage_history(client, symbol)
            source = "alpha_vantage"
        price_currency = instrument.base_currency
    elif instrument.price_source == "coingecko":
        # Binance public API is the primary crypto-history source.
        # CoinGecko Demo tier was progressively restricted (2026: only
        # days=1 returns 200 on /market_chart) so it can't backfill past
        # the last 24h. Binance has no auth, no per-IP daily cap, and
        # covers every coin the user actually holds (BTC/ETH/USDC/etc).
        # We always fetch the USDT pair and store currency="USD" — the
        # replay's existing FX path converts to EUR for display.
        # CoinGecko stays as a final fallback for any coin not on Binance,
        # though it's effectively a no-op given the Demo cap.
        binance_pair = f"{instrument.symbol.upper()}USDT"
        try:
            history = await fetch_binance_history(client, binance_pair)
            source = "binance"
            price_currency = "USD"
        except PriceProviderRateLimited:
            # Same rationale as the Twelve Data branch — surface 429s so
            # the user retries instead of falling through to a CoinGecko
            # call that's almost certain to be either rate-limited too or
            # capped at days=1 on Demo tier.
            raise
        except ValueError as bn_err:
            logger.warning(
                "backfill_binance_failed_falling_back",
                extra={"symbol": binance_pair, "err": str(bn_err)},
            )
            coin_id = instrument.ticker_override or instrument.symbol
            history = await fetch_coingecko_history(
                client, coin_id, instrument.base_currency.lower()
            )
            source = "coingecko"
            price_currency = instrument.base_currency
    else:
        raise ValueError(
            f"unknown historical price_source {instrument.price_source!r} "
            f"for instrument {instrument.id}"
        )

    history_by_date = {
        point.date: point.price for point in history if start <= point.date <= end
    }
    if not history_by_date:
        # Surface a WARN with enough context (provider
        # symbol + price_source + window) so the user can debug a sparse
        # backfill without grepping API call logs by hand. Common causes
        # we've seen on free tiers:
        #   - EU-listed shares behind a paid Twelve Data tier (ASML.AS,
        #     SAP.DE on XETR/AMS qualifier)
        #   - UCITS ETFs (VWCE, SXR8) not covered at all on the free tier
        #   - Provider returned rows but ALL fell outside the requested
        #     window (e.g., free-tier history cap on a deep-history symbol)
        # Action for the user: switch the instrument's `price_source` to
        # `manual` and supply NAV anchors, OR set `ticker_override` to a
        # symbol the provider does cover.
        provider_symbol = (
            instrument.ticker_override or instrument.symbol
            if instrument.price_source != "coingecko"
            else (instrument.ticker_override or f"{instrument.symbol.upper()}USDT")
        )
        logger.warning(
            "backfill_no_history_available",
            extra={
                "instrument_id": instrument.id,
                "symbol": instrument.symbol,
                "provider_symbol": provider_symbol,
                "price_source": instrument.price_source,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "rows_received": len(history),
            },
        )
        return BackfillResult(
            instrument_id=instrument.id,
            status="no_history_available",
            inserted_prices=0,
            skipped_existing=0,
            start=start,
            end=end,
        )

    stmt = select(PriceQuote.date).where(
        PriceQuote.instrument_id == instrument.id,
        PriceQuote.source == source,
        PriceQuote.date.in_(history_by_date.keys()),
    )
    result = await session.execute(stmt)
    existing_dates = set(result.scalars().all())

    inserted = 0
    skipped = 0
    for quote_date, price in sorted(history_by_date.items()):
        if quote_date in existing_dates:
            skipped += 1
            continue
        session.add(PriceQuote(
            instrument_id=instrument.id,
            date=quote_date,
            price=price,
            currency=price_currency,
            source=source,
        ))
        inserted += 1

    return BackfillResult(
        instrument_id=instrument.id,
        status="ok",
        inserted_prices=inserted,
        skipped_existing=skipped,
        start=start,
        end=end,
    )


async def backfill_fx_history(
    session: AsyncSession,
    client: httpx.AsyncClient,
    start: date,
    end: date,
) -> int:
    """Populate EUR/USD `fx_rate` rows for a date range and return insert count."""
    if start > end:
        raise ValueError(f"start date {start} is after end date {end}")

    history = await fetch_fx_range(client, start, end)
    if not history:
        return 0

    dates = [rate_date for rate_date, _rate in history]
    stmt = select(FxRate.date).where(
        FxRate.base_currency == "EUR",
        FxRate.quote_currency == "USD",
        FxRate.date.in_(dates),
    )
    result = await session.execute(stmt)
    existing_dates = set(result.scalars().all())

    inserted = 0
    for rate_date, rate in history:
        if rate_date in existing_dates:
            continue
        session.add(FxRate(
            date=rate_date,
            base_currency="EUR",
            quote_currency="USD",
            rate=rate,
            source="frankfurter",
        ))
        inserted += 1
    return inserted
