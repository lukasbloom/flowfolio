"""CoinGecko Demo /simple/price client.

Free Demo tier: ~30 req/min, 10k/month
(CLAUDE.md §"Price & FX Data Sources").

Endpoint:
    https://api.coingecko.com/api/v3/simple/price?ids={ID}&vs_currencies={CUR}&x_cg_demo_api_key={KEY}

Response shape (success):
    {"bitcoin": {"eur": 56789.12}}

Security: same as finnhub.py — key resolved from the DB-backed key store,
never logged.

Timezone: market_chart timestamps are Unix-ms; we convert them to the
user's local calendar date in Europe/Madrid before storing as a `date`.
Trade dates are recorded as Spain-local calendar days, so anchoring CoinGecko
points to the same locale prevents off-by-one mismatches on volatile
crypto sleeves (a midnight UTC close maps to 01:00–02:00 Madrid local).
"""
from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from decimal import Decimal

import httpx

from app.core.clock import LOCAL_TZ
from app.core.config import settings
from app.services.key_store import get_api_key
from app.services.pricing.errors import (
    parse_positive_decimal,
    raise_for_provider_response,
)
from app.services.pricing.types import HistoricalPrice

# CoinGecko timestamps are normalised to the app's LOCAL_TZ
# (Europe/Madrid), matching the date-fns `es` locale used across the frontend
# and the user's calendar. Single-sourced in app.core.clock — change there.

logger = logging.getLogger(__name__)

COINGECKO_URL = "https://api.coingecko.com/api/v3/simple/price"
COINGECKO_MARKET_CHART_URL = "https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart"


def _coingecko_auth_headers() -> dict[str, str]:
    """Demo-tier auth header. CoinGecko deprecated the `x_cg_demo_api_key`
    query-param form (it now returns 401 "API Key Missing"); the active
    method is the `x-cg-demo-api-key` HTTP header. Omit the header
    entirely when no key is configured rather than sending an empty
    string — defensive against any quirk in CoinGecko's ratelimiter
    that might treat empty differently from absent.
    """
    headers = {"accept": "application/json"}
    key = get_api_key("coingecko")
    if key:
        headers["x-cg-demo-api-key"] = key
    return headers


async def fetch_coingecko_quote(
    client: httpx.AsyncClient, coin_id: str, vs_currency: str = "eur"
) -> Decimal:
    """Return the current CoinGecko price for `coin_id` in `vs_currency`.

    Args:
        coin_id: CoinGecko canonical coin id, e.g. "bitcoin", "ethereum".
        vs_currency: lowercase ISO currency, e.g. "eur" or "usd".

    Raises:
        ValueError: if the API key is not configured, the response is
            rate-limited (HTTP 429), the network call fails, the response
            is missing the coin_id/vs_currency keys, or the price is
            non-finite, non-numeric, or non-positive.
    """
    if not get_api_key("coingecko"):
        raise ValueError("COINGECKO_API_KEY not configured")

    params = {
        "ids": coin_id,
        "vs_currencies": vs_currency,
    }
    try:
        resp = await client.get(
            COINGECKO_URL,
            params=params,
            headers=_coingecko_auth_headers(),
            timeout=settings.pricing_timeout_seconds,
        )
    except httpx.HTTPError as e:
        raise ValueError(f"coingecko network error: {type(e).__name__}") from None

    raise_for_provider_response(resp, provider="coingecko")

    payload = resp.json()
    coin_block = payload.get(coin_id)
    if not coin_block:
        raise ValueError(f"coingecko missing coin_id {coin_id}")
    raw = coin_block.get(vs_currency)
    if raw is None:
        raise ValueError(f"coingecko missing {vs_currency} for {coin_id}")
    price = parse_positive_decimal(
        raw, provider="coingecko", noun="price", context=f"{coin_id}/{vs_currency}"
    )

    logger.info(
        "coingecko_quote_ok",
        extra={"source": "coingecko", "symbol": coin_id, "vs": vs_currency},
    )
    return price


async def fetch_coingecko_history(
    client: httpx.AsyncClient, coin_id: str, vs_currency: str = "eur"
) -> list[HistoricalPrice]:
    """Return daily CoinGecko market-chart history for `coin_id`."""
    if not get_api_key("coingecko"):
        raise ValueError("COINGECKO_API_KEY not configured")

    url = COINGECKO_MARKET_CHART_URL.format(coin_id=coin_id)
    params = {
        "vs_currency": vs_currency,
        "days": "max",
        "interval": "daily",
    }
    try:
        resp = await client.get(
            url,
            params=params,
            headers=_coingecko_auth_headers(),
            timeout=settings.pricing_timeout_seconds,
        )
    except httpx.HTTPError as e:
        raise ValueError(f"coingecko network error: {type(e).__name__}") from None

    raise_for_provider_response(resp, provider="coingecko")

    payload = resp.json()
    prices = payload.get("prices")
    if not prices:
        raise ValueError(f"coingecko missing prices for {coin_id}")

    history: list[HistoricalPrice] = []
    seen_dates: set[date] = set()
    for item in prices:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            raise ValueError(f"coingecko malformed price point for {coin_id}")
        timestamp_ms, raw = item[0], item[1]
        point_date = (
            datetime.fromtimestamp(int(timestamp_ms) / 1000, tz=UTC)
            .astimezone(LOCAL_TZ)
            .date()
        )
        if point_date in seen_dates:
            continue
        seen_dates.add(point_date)
        history.append(
            HistoricalPrice(
                date=point_date,
                price=parse_positive_decimal(
                    raw,
                    provider="coingecko",
                    noun="price",
                    context=f"{coin_id}/{vs_currency}",
                ),
            )
        )
    return history
