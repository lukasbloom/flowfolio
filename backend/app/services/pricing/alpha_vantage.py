"""Alpha Vantage TIME_SERIES_DAILY client (Finnhub fallback).

Free tier: 25 calls/day, 5 calls/minute (CLAUDE.md §"Price & FX Data Sources").
Used only as a fallback when Finnhub returns an error.

Endpoint:
    https://www.alphavantage.co/query?function=TIME_SERIES_DAILY&symbol={SYMBOL}&apikey={KEY}

Response shape (success):
    {
        "Meta Data": {...},
        "Time Series (Daily)": {
            "2026-04-29": {"1. open": "...", "4. close": "191.45", ...},
            ...
        },
    }

Rate-limit response (HTTP 200 + "Note" body — Alpha Vantage's quirk):
    {"Note": "Thank you for using Alpha Vantage! Our standard API ..."}

Security: same as finnhub.py — key resolved from the DB-backed key store,
never logged.
"""
from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal

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

ALPHA_VANTAGE_URL = "https://www.alphavantage.co/query"


async def fetch_alpha_vantage_quote(client: httpx.AsyncClient, symbol: str) -> Decimal:
    """Return the most-recent close from Alpha Vantage's daily time series.

    Raises:
        ValueError: if the API key is not configured, the response is
            rate-limited (HTTP 429 OR HTTP 200 with "Note" body), the
            network call fails, the response is missing the daily series,
            or the close is non-finite, non-numeric, or non-positive.
    """
    key = get_api_key("alpha_vantage")
    if not key:
        raise ValueError("ALPHA_VANTAGE_API_KEY not configured")

    params = {
        "function": "TIME_SERIES_DAILY",
        "symbol": symbol,
        "apikey": key,
    }
    try:
        resp = await client.get(
            ALPHA_VANTAGE_URL,
            params=params,
            timeout=settings.pricing_timeout_seconds,
        )
    except httpx.HTTPError as e:
        raise ValueError(f"alpha_vantage network error: {type(e).__name__}") from None

    raise_for_provider_response(resp, provider="alpha_vantage")

    payload = resp.json()

    # Alpha Vantage returns rate-limit messages as HTTP 200 + a "Note" key.
    if "Note" in payload or "Information" in payload:
        raise PriceProviderRateLimited("alpha_vantage rate limited")
    if "Error Message" in payload:
        raise ValueError(f"alpha_vantage api error for {symbol}")

    series = payload.get("Time Series (Daily)")
    if not series:
        raise ValueError(f"alpha_vantage missing daily series for {symbol}")

    # Most recent date — keys are ISO-formatted "YYYY-MM-DD" so lexicographic max works.
    latest_date = max(series.keys())
    row = series[latest_date]
    raw = row.get("4. close")
    if raw is None:
        raise ValueError(f"alpha_vantage missing close for {symbol} {latest_date}")
    price = parse_positive_decimal(
        raw, provider="alpha_vantage", context=f"{symbol} {latest_date}"
    )

    logger.info(
        "alpha_vantage_quote_ok",
        extra={"source": "alpha_vantage", "symbol": symbol, "as_of": latest_date},
    )
    return price


async def fetch_alpha_vantage_history(
    client: httpx.AsyncClient, symbol: str
) -> list[HistoricalPrice]:
    """Return full Alpha Vantage daily close history for `symbol`."""
    key = get_api_key("alpha_vantage")
    if not key:
        raise ValueError("ALPHA_VANTAGE_API_KEY not configured")

    params = {
        "function": "TIME_SERIES_DAILY",
        "symbol": symbol,
        "outputsize": "full",
        "apikey": key,
    }
    try:
        resp = await client.get(
            ALPHA_VANTAGE_URL,
            params=params,
            timeout=settings.pricing_timeout_seconds,
        )
    except httpx.HTTPError as e:
        raise ValueError(f"alpha_vantage network error: {type(e).__name__}") from None

    raise_for_provider_response(resp, provider="alpha_vantage")

    payload = resp.json()
    if "Note" in payload or "Information" in payload:
        raise PriceProviderRateLimited("alpha_vantage rate limited")
    if "Error Message" in payload:
        raise ValueError(f"alpha_vantage api error for {symbol}")

    series = payload.get("Time Series (Daily)")
    if not series:
        raise ValueError(f"alpha_vantage missing daily series for {symbol}")

    history: list[HistoricalPrice] = []
    for day, row in sorted(series.items()):
        raw = row.get("4. close")
        if raw is None:
            raise ValueError(f"alpha_vantage missing close for {symbol} {day}")
        history.append(
            HistoricalPrice(
                date=date.fromisoformat(day),
                price=parse_positive_decimal(
                    raw, provider="alpha_vantage", context=f"{symbol} {day}"
                ),
            )
        )
    return history
