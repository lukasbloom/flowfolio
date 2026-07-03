"""Per-provider live API-key test calls.

Each function takes a not-yet-saved candidate key plus a shared httpx client and
hits ONLY that provider's hardcoded official host (SSRF-safe, no request-derived
URL). The outcome maps to a pass (returns None) or a sanitized failure
(ValueError, provider-prefixed). An HTTP 401/403 is an invalid key; a network
error, timeout, or any other non-2xx is an unreachable provider. The
candidate key is NEVER placed in the raised message (mirrors finnhub.py:51-54).
Only the provider id and a category are surfaced. The router
resolves the right function via TEST_DISPATCH keyed by provider id.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable

import httpx

from app.core.config import settings
from app.services.pricing.alpha_vantage import ALPHA_VANTAGE_URL
from app.services.pricing.coingecko import COINGECKO_URL
from app.services.pricing.finnhub import FINNHUB_QUOTE_URL
from app.services.pricing.twelve_data import TWELVE_DATA_URL

# Reuse each provider's hardcoded host. CoinGecko's ping path is derived from the
# pricing client's base URL so the host can never drift; GitHub has no pricing
# client, so its official host is pinned here.
COINGECKO_PING_URL = str(httpx.URL(COINGECKO_URL).copy_with(path="/api/v3/ping"))
GITHUB_RATE_LIMIT_URL = "https://api.github.com/rate_limit"

# A canonical symbol used purely to make the test request well-formed. The test
# only inspects the HTTP status, so any valid ticker works.
_CANONICAL_SYMBOL = "AAPL"


def _raise_for_status(provider: str, status_code: int) -> None:
    """Map a response status to a sanitized failure. 2xx passes silently."""
    if 200 <= status_code < 300:
        return
    if status_code in (401, 403):
        raise ValueError(f"{provider}: invalid API key")
    raise ValueError(f"{provider}: provider unreachable (HTTP {status_code})")


def _raise_unreachable(provider: str, exc: httpx.HTTPError) -> None:
    """Rewrap a network error WITHOUT the URL/key in the message."""
    raise ValueError(f"{provider}: provider unreachable ({type(exc).__name__})") from None


async def _test_finnhub(client: httpx.AsyncClient, candidate_key: str) -> None:
    try:
        resp = await client.get(
            FINNHUB_QUOTE_URL,
            params={"symbol": _CANONICAL_SYMBOL, "token": candidate_key},
            timeout=settings.pricing_timeout_seconds,
        )
    except httpx.HTTPError as exc:
        _raise_unreachable("finnhub", exc)
    _raise_for_status("finnhub", resp.status_code)


async def _test_coingecko(client: httpx.AsyncClient, candidate_key: str) -> None:
    try:
        resp = await client.get(
            COINGECKO_PING_URL,
            headers={"accept": "application/json", "x-cg-demo-api-key": candidate_key},
            timeout=settings.pricing_timeout_seconds,
        )
    except httpx.HTTPError as exc:
        _raise_unreachable("coingecko", exc)
    _raise_for_status("coingecko", resp.status_code)


async def _test_alpha_vantage(client: httpx.AsyncClient, candidate_key: str) -> None:
    try:
        resp = await client.get(
            ALPHA_VANTAGE_URL,
            params={
                "function": "GLOBAL_QUOTE",
                "symbol": _CANONICAL_SYMBOL,
                "apikey": candidate_key,
            },
            timeout=settings.pricing_timeout_seconds,
        )
    except httpx.HTTPError as exc:
        _raise_unreachable("alpha_vantage", exc)
    _raise_for_status("alpha_vantage", resp.status_code)


async def _test_twelve_data(client: httpx.AsyncClient, candidate_key: str) -> None:
    try:
        resp = await client.get(
            TWELVE_DATA_URL,
            params={
                "symbol": _CANONICAL_SYMBOL,
                "interval": "1day",
                "outputsize": "1",
                "apikey": candidate_key,
            },
            timeout=settings.pricing_timeout_seconds,
        )
    except httpx.HTTPError as exc:
        _raise_unreachable("twelve_data", exc)
    _raise_for_status("twelve_data", resp.status_code)


async def _test_github(client: httpx.AsyncClient, candidate_key: str) -> None:
    try:
        resp = await client.get(
            GITHUB_RATE_LIMIT_URL,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {candidate_key}",
            },
            timeout=settings.pricing_timeout_seconds,
        )
    except httpx.HTTPError as exc:
        _raise_unreachable("github", exc)
    _raise_for_status("github", resp.status_code)


# provider id -> test function. The router resolves by id (exactly the 5 ids).
TestFn = Callable[[httpx.AsyncClient, str], Awaitable[None]]

TEST_DISPATCH: dict[str, TestFn] = {
    "finnhub": _test_finnhub,
    "coingecko": _test_coingecko,
    "alpha_vantage": _test_alpha_vantage,
    "twelve_data": _test_twelve_data,
    "github": _test_github,
}
