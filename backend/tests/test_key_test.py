"""Per-provider key-test unit tests.

Drives each `key_test` function through an `httpx.MockTransport` so nothing
reaches the wire (the hermetic guard blocks these hosts anyway). Asserts:
- a 200 passes (returns None),
- a 401/403 raises an "invalid API key" failure,
- a network error raises an "unreachable" failure,
- the candidate key NEVER appears in any raised message,
- each call targets only its provider's hardcoded host,
- the dispatch map has exactly the 5 provider ids.
"""
from __future__ import annotations

import httpx
import pytest

from app.services import key_test
from app.services.key_test import TEST_DISPATCH

# A recognizable secret so a leak into any error string is unmistakable.
CANDIDATE = "SUPERSECRETKEY-0123456789"

# provider id -> expected outbound host (the only host that call may reach).
EXPECTED_HOSTS = {
    "finnhub": "finnhub.io",
    "coingecko": "api.coingecko.com",
    "alpha_vantage": "www.alphavantage.co",
    "twelve_data": "api.twelvedata.com",
    "github": "api.github.com",
}

ALL_PROVIDERS = list(EXPECTED_HOSTS)


def _client_with(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_dispatch_has_exactly_five_providers():
    assert set(TEST_DISPATCH) == set(ALL_PROVIDERS)
    assert len(TEST_DISPATCH) == 5


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ALL_PROVIDERS)
async def test_200_passes_and_hits_only_provider_host(provider):
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["host"] = request.url.host
        return httpx.Response(200, json={"ok": True})

    async with _client_with(handler) as client:
        result = await TEST_DISPATCH[provider](client, CANDIDATE)

    assert result is None
    assert captured["host"] == EXPECTED_HOSTS[provider]


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ALL_PROVIDERS)
@pytest.mark.parametrize("status", [401, 403])
async def test_401_403_raises_invalid_without_leaking_key(provider, status):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": "nope"})

    async with _client_with(handler) as client:
        with pytest.raises(ValueError) as excinfo:
            await TEST_DISPATCH[provider](client, CANDIDATE)

    message = str(excinfo.value)
    assert "invalid API key" in message
    assert provider in message
    assert CANDIDATE not in message


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ALL_PROVIDERS)
async def test_network_error_raises_unreachable_without_leaking_key(provider):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"boom for {CANDIDATE}")

    async with _client_with(handler) as client:
        with pytest.raises(ValueError) as excinfo:
            await TEST_DISPATCH[provider](client, CANDIDATE)

    message = str(excinfo.value)
    assert "unreachable" in message
    assert provider in message
    assert CANDIDATE not in message


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ALL_PROVIDERS)
async def test_500_raises_unreachable(provider):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "server"})

    async with _client_with(handler) as client:
        with pytest.raises(ValueError, match="unreachable"):
            await TEST_DISPATCH[provider](client, CANDIDATE)


def test_derived_coingecko_ping_host_is_official():
    assert httpx.URL(key_test.COINGECKO_PING_URL).host == "api.coingecko.com"
    assert key_test.COINGECKO_PING_URL.endswith("/api/v3/ping")
