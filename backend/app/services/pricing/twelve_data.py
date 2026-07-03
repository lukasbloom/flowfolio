"""Twelve Data /time_series client — primary stock-history source.

Free tier: 800 calls/day, 8 calls/minute. Used as the primary historical
backfill source for `price_source="finnhub"` instruments (Alpha Vantage
remains as a fallback when Twelve Data fails).

Endpoint:
    https://api.twelvedata.com/time_series?symbol={SYMBOL}&interval=1day&outputsize=5000&apikey={KEY}

Response shape (success):
    {
        "meta": {...},
        "values": [
            {"datetime": "2026-05-08", "open": "...", "close": "210.00", ...},
            ...
        ],
        "status": "ok"
    }

Rate-limit / error response:
    {"code": 429, "message": "...", "status": "error"}

Twelve Data flags rate-limit either via HTTP 429 OR a JSON body with
`status="error"` and `code=429`. We map both to PriceProviderRateLimited.

Security: same as finnhub.py — key resolved from the DB-backed key store,
never logged.
"""
from __future__ import annotations

import logging
from datetime import date

import httpx

from app.core.config import settings
from app.services.key_store import get_api_key
from app.services.pricing.errors import (
    PriceProviderRateLimited,
    parse_positive_decimal,
    raise_for_provider_response,
)
from app.services.pricing.types import HistoricalPrice

logger = logging.getLogger(__name__)

TWELVE_DATA_URL = "https://api.twelvedata.com/time_series"


async def fetch_twelve_data_history(
    client: httpx.AsyncClient, symbol: str
) -> list[HistoricalPrice]:
    """Return daily Twelve Data close history for `symbol`.

    Raises:
        PriceProviderRateLimited: HTTP 429 or JSON `{status: "error", code: 429}`.
        ValueError: missing API key, network error, malformed response,
            non-positive prices, or other API errors.
    """
    key = get_api_key("twelve_data")
    if not key:
        raise ValueError("TWELVE_DATA_API_KEY not configured")

    params = {
        "symbol": symbol,
        "interval": "1day",
        "outputsize": "5000",  # Twelve Data caps at 5000 points; ample for daily.
        "format": "JSON",
        "apikey": key,
    }
    try:
        resp = await client.get(
            TWELVE_DATA_URL,
            params=params,
            timeout=settings.pricing_timeout_seconds,
        )
    except httpx.HTTPError as e:
        raise ValueError(f"twelve_data network error: {type(e).__name__}") from None

    raise_for_provider_response(resp, provider="twelve_data")

    payload = resp.json()

    # Twelve Data also signals errors via HTTP-200 with status="error".
    if payload.get("status") == "error":
        if payload.get("code") == 429:
            raise PriceProviderRateLimited("twelve_data rate limited")
        raise ValueError(f"twelve_data api error for {symbol}")

    values = payload.get("values")
    if not values:
        raise ValueError(f"twelve_data missing values for {symbol}")

    history: list[HistoricalPrice] = []
    for row in values:
        datetime_str = row.get("datetime")
        raw_close = row.get("close")
        if datetime_str is None or raw_close is None:
            raise ValueError(f"twelve_data malformed row for {symbol}")
        try:
            point_date = date.fromisoformat(datetime_str[:10])
        except ValueError:
            raise ValueError(f"twelve_data bad date for {symbol}: {datetime_str}") from None
        history.append(
            HistoricalPrice(
                date=point_date,
                price=parse_positive_decimal(
                    raw_close,
                    provider="twelve_data",
                    context=f"{symbol} {point_date}",
                ),
            )
        )
    # Twelve Data returns newest-first; backfill expects ascending.
    history.sort(key=lambda h: h.date)
    return history
