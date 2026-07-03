"""Per-instrument price dispatcher.

Caller-commits convention (matches `app.services.fifo.match_lots_for_sell`):
the dispatcher adds one PriceQuote row per successful fetch; the caller
(cron / manual API) is responsible for `await db.commit()`.

Fallback chain (single-shot, no retries within a source):
    - stock/etf via finnhub: finnhub -> alpha_vantage -> StaleQuoteError
    - crypto/stablecoin via coingecko: coingecko -> StaleQuoteError
    - fund/etf via ft: ft -> StaleQuoteError
    - manual: never auto-fetched; returns the most recent cached quote

If a manual price_quote already exists for the
(instrument_id, today) pair, return it without making any API call.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Optional

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.models.instrument import Instrument
from app.models.price_quote import PriceQuote
from app.services.pricing.alpha_vantage import fetch_alpha_vantage_quote
from app.services.pricing.coingecko import fetch_coingecko_quote
from app.services.pricing.finnhub import fetch_finnhub_quote
from app.services.pricing.ft_scraper import fetch_ft_quote

logger = logging.getLogger(__name__)


class StaleQuoteError(Exception):
    """All API sources failed.

    The caller is responsible for marking the holding stale in the UI.
    `last_quote` carries the most recent cached PriceQuote (or None) so the
    UI can render last-known-good while displaying the stale badge.
    """

    def __init__(self, message: str, last_quote: Optional[PriceQuote] = None):
        super().__init__(message)
        self.last_quote = last_quote


async def _last_cached_quote(
    session: AsyncSession, instrument_id: str
) -> Optional[PriceQuote]:
    """Return the most recent PriceQuote for `instrument_id`, or None."""
    stmt = (
        select(PriceQuote)
        .where(PriceQuote.instrument_id == instrument_id)
        .order_by(PriceQuote.date.desc(), PriceQuote.fetched_at.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def _today_manual_override(
    session: AsyncSession, instrument_id: str, today: date
) -> Optional[PriceQuote]:
    """Return today's manual NAV override for `instrument_id`, or None."""
    stmt = select(PriceQuote).where(
        PriceQuote.instrument_id == instrument_id,
        PriceQuote.date == today,
        PriceQuote.source == "manual",
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def fetch_price(
    session: AsyncSession,
    client: httpx.AsyncClient,
    instrument: Instrument,
    today: Optional[date] = None,
) -> PriceQuote:
    """Fetch and persist a fresh PriceQuote for `instrument`.

    The function MUST be called inside an open DB transaction; the returned
    PriceQuote has been added to the session but NOT committed.

    Raises:
        StaleQuoteError: All API sources for this instrument failed.
            `err.last_quote` carries the most recent cached PriceQuote
            (None if the instrument has no history yet).
        ValueError: `instrument.price_source` is unknown.
    """
    today = today or clock.today()

    # Manual override wins over any API for the same date.
    manual = await _today_manual_override(session, instrument.id, today)
    if manual is not None:
        return manual

    source = instrument.price_source
    currency = instrument.base_currency

    try:
        if source == "finnhub":
            # price_source="finnhub" enum-label vs. actual provider call path:
            #   - DAILY-REFRESH (here): Finnhub primary -> Alpha Vantage fallback.
            #     Honest naming — the primary really is Finnhub for live quotes.
            #   - BULK-BACKFILL (services/backfill.py): Twelve Data primary ->
            #     Alpha Vantage fallback. Misleading naming, historical:
            #     Twelve Data displaced
            #     Finnhub on the history path (800/day vs 25/day budget +
            #     deeper outputsize). Twelve Data free tier does NOT cover
            #     EU-listed originals (.AS / .DE / XETR-quoted UCITS ETFs)
            #     without a paid plan — those instruments should be
            #     reconfigured to `price_source="manual"` until a paid tier
            #     or alternate provider lands.
            symbol = instrument.ticker_override or instrument.symbol
            try:
                price = await fetch_finnhub_quote(client, symbol)
                tag = "finnhub"
            except ValueError as e:
                logger.warning(
                    "finnhub_fallback_to_av",
                    extra={"symbol": symbol, "err": str(e)},
                )
                price = await fetch_alpha_vantage_quote(client, symbol)
                tag = "alpha_vantage"
        elif source == "coingecko":
            # CoinGecko expects its canonical coin id (e.g. "usd-coin"
            # for USDC, "bitcoin" for BTC). Users typically store the trading
            # ticker on `symbol` and put the coin id in `ticker_override` —
            # mirror what `backfill_instrument_history` already does so the
            # daily quote refresh and the backfill agree.
            coin_id = instrument.ticker_override or instrument.symbol
            price = await fetch_coingecko_quote(client, coin_id, currency.lower())
            tag = "coingecko"
        elif source == "ft":
            price = await fetch_ft_quote(client, instrument)
            tag = "ft"
        elif source == "manual":
            # No automatic fetch — manual-only instrument. Return the most
            # recent cached row; if none exist, raise stale.
            cached = await _last_cached_quote(session, instrument.id)
            if cached is None:
                raise StaleQuoteError(
                    f"manual instrument {instrument.id} has no cached price",
                    None,
                )
            return cached
        else:
            raise ValueError(
                f"unknown price_source {source!r} for instrument {instrument.id}"
            )
    except ValueError as e:
        last = await _last_cached_quote(session, instrument.id)
        raise StaleQuoteError(
            f"all sources failed for {instrument.symbol}: {e}", last
        ) from None

    quote = PriceQuote(
        instrument_id=instrument.id,
        date=today,
        price=price,
        currency=currency,
        source=tag,
    )
    session.add(quote)
    return quote
