"""Binance public spot API — primary crypto-history source.

No auth required. Weight-based rate limit of 1200/min on the public
endpoints; daily klines is `weight=1`, so 1200 calls/min is effectively
unlimited for personal-tracker backfills.

Endpoint:
    https://api.binance.com/api/v3/klines?symbol={SYMBOL}&interval=1d&startTime={MS}&endTime={MS}&limit=1000

Returns up to 1000 daily candles per call (≈3 years of history). Each
candle is an array — index 0 is open_time (ms), index 4 is close (str).

Symbol format: `<COIN><QUOTE>`, e.g. `BTCUSDT`, `USDCUSDT`. We always
fetch USDT pairs (USDT≈USD) and store `currency="USD"` on the resulting
`PriceQuote` rows; the replay layer already converts to EUR via FX.

Used as the primary backfill source for `price_source="coingecko"`
instruments — CoinGecko's Demo tier has been progressively restricted
(2026: only days=1 returns 200 on /market_chart) so it's no longer
viable for history. CoinGecko's /simple/price endpoint still works for
the daily quote refresh; that path is unchanged.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx

from app.core.config import settings
from app.services.pricing.errors import (
    parse_positive_decimal,
    raise_for_provider_response,
)
from app.services.pricing.types import HistoricalPrice

logger = logging.getLogger(__name__)

BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"


async def fetch_binance_history(
    client: httpx.AsyncClient, symbol_pair: str
) -> list[HistoricalPrice]:
    """Return daily Binance close history for a trading pair (e.g. BTCUSDT).

    Fetches up to 1000 candles ending today. For longer ranges, callers
    can paginate by passing custom startTime — but a personal tracker's
    backfill range fits in one call.

    Raises:
        PriceProviderRateLimited: HTTP 429 (Binance's IP-banlist trigger).
        ValueError: network error, malformed response, or a Binance JSON
            error (e.g. invalid symbol).
    """
    end_ms = int(datetime.now(UTC).timestamp() * 1000)
    # 1000 daily candles back from now (~2.74 years). For instruments
    # held longer than that the older history won't be backfilled — the
    # synthetic-quote fallback in get_networth_series picks up the slack
    # at the trade price. Pagination is a future-work item.
    start_ms = end_ms - 1000 * 24 * 60 * 60 * 1000
    params = {
        "symbol": symbol_pair.upper(),
        "interval": "1d",
        "startTime": start_ms,
        "endTime": end_ms,
        "limit": 1000,
    }
    try:
        resp = await client.get(
            BINANCE_KLINES_URL,
            params=params,
            timeout=settings.pricing_timeout_seconds,
        )
    except httpx.HTTPError as e:
        raise ValueError(f"binance network error: {type(e).__name__}") from None

    # 429 → PriceProviderRateLimited("binance rate limited"); any other non-200
    # → ValueError("binance http {code}"). Binance returns 400 with a JSON body
    # for invalid symbols, etc.; we never leak the symbol in the error message
    # (the router masks it but defense-in-depth is cheap).
    raise_for_provider_response(resp, provider="binance")

    payload = resp.json()
    if not isinstance(payload, list):
        raise ValueError(f"binance unexpected response shape for {symbol_pair}")
    if not payload:
        raise ValueError(f"binance no candles returned for {symbol_pair}")

    history: list[HistoricalPrice] = []
    for candle in payload:
        if not isinstance(candle, list) or len(candle) < 5:
            raise ValueError(f"binance malformed candle for {symbol_pair}")
        open_time_ms = candle[0]
        raw_close = candle[4]
        try:
            point_date = (
                datetime.fromtimestamp(int(open_time_ms) / 1000, tz=UTC).date()
            )
        except (TypeError, ValueError, OverflowError):
            raise ValueError(f"binance bad timestamp for {symbol_pair}") from None
        history.append(
            HistoricalPrice(
                date=point_date,
                price=parse_positive_decimal(
                    raw_close,
                    provider="binance",
                    context=f"{symbol_pair} {point_date}",
                ),
            )
        )
    return history
