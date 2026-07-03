"""Finnhub /quote client.

Free tier: 60 req/min (CLAUDE.md §"Price & FX Data Sources").

Endpoint: https://finnhub.io/api/v1/quote?symbol={SYMBOL}&token={KEY}
Response shape: {"c": <current>, "h": <high>, "l": <low>, ...}

Security:
- API key is resolved from the DB-backed key store via get_api_key at call
  time. Never hardcoded.
- API key is passed via httpx params (URL-encoded), never logged. On httpx
  exceptions we strip the URL from the message before re-raising — httpx
  stringifies the full URL (including the token) by default.
"""
from __future__ import annotations

import logging
from decimal import Decimal

import httpx

from app.core.config import settings
from app.services.key_store import get_api_key
from app.services.pricing.errors import (
    parse_positive_decimal,
    raise_for_provider_response,
)

logger = logging.getLogger(__name__)

FINNHUB_QUOTE_URL = "https://finnhub.io/api/v1/quote"


async def fetch_finnhub_quote(client: httpx.AsyncClient, symbol: str) -> Decimal:
    """Return the current Finnhub quote for `symbol` as `Decimal`.

    Raises:
        ValueError: if the API key is not configured, the response is
            rate-limited (HTTP 429), the network call fails, the response
            body is missing the "c" field, or the price is non-finite,
            non-numeric, or non-positive.
    """
    key = get_api_key("finnhub")
    if not key:
        raise ValueError("FINNHUB_API_KEY not configured")

    params = {"symbol": symbol, "token": key}
    try:
        resp = await client.get(
            FINNHUB_QUOTE_URL,
            params=params,
            timeout=settings.pricing_timeout_seconds,
        )
    except httpx.HTTPError as e:
        # httpx exceptions stringify the URL (which contains the token).
        # Rewrap with sanitized message before re-raising.
        raise ValueError(f"finnhub network error: {type(e).__name__}") from None

    # finnhub historically raised a plain ValueError("finnhub rate limited") on
    # 429 (NOT PriceProviderRateLimited like the other providers); preserve that
    # by passing rate_limited_exc=ValueError.
    raise_for_provider_response(resp, provider="finnhub", rate_limited_exc=ValueError)

    payload = resp.json()
    raw = payload.get("c")
    if raw is None:
        raise ValueError("finnhub response missing 'c'")
    price = parse_positive_decimal(
        raw, provider="finnhub", noun="price", context=symbol
    )

    # Log only sanitized fields — never the URL (contains the API key).
    logger.info("finnhub_quote_ok", extra={"source": "finnhub", "symbol": symbol})
    return price
