"""Pricing client unit tests.

Mocks HTTP responses via `httpx.MockTransport` (no pytest-httpx dep needed).
Live integration tests are gated on env keys + `pytest.mark.live`.
"""
from __future__ import annotations

import os
from decimal import Decimal

import httpx
import pytest

from app.services import key_store
from app.services.pricing.alpha_vantage import fetch_alpha_vantage_quote
from app.services.pricing.coingecko import fetch_coingecko_quote
from app.services.pricing.finnhub import fetch_finnhub_quote


def _client_with(handler) -> httpx.AsyncClient:
    """Build an AsyncClient backed by a MockTransport that calls `handler`."""
    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(transport=transport)


# ---------------------------------------------------------------------------
# Finnhub
# ---------------------------------------------------------------------------


async def test_finnhub_happy_path(monkeypatch):
    monkeypatch.setitem(key_store._CACHE, "finnhub", "test-finnhub-key")

    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"c": 191.45, "h": 192.0, "l": 190.0})

    async with _client_with(handler) as client:
        price = await fetch_finnhub_quote(client, "AAPL")

    assert price == Decimal("191.45")
    assert isinstance(price, Decimal)
    # Sanity-check that the symbol is on the URL but the test key is what was sent
    assert "symbol=AAPL" in captured["url"]
    assert "token=test-finnhub-key" in captured["url"]


async def test_finnhub_rate_limit_raises(monkeypatch):
    monkeypatch.setitem(key_store._CACHE, "finnhub", "test-finnhub-key")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "rate limited"})

    async with _client_with(handler) as client:
        with pytest.raises(ValueError, match="rate limited"):
            await fetch_finnhub_quote(client, "AAPL")


async def test_finnhub_non_finite_raises(monkeypatch):
    monkeypatch.setitem(key_store._CACHE, "finnhub", "test-finnhub-key")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"c": 0})

    async with _client_with(handler) as client:
        with pytest.raises(ValueError, match="non-positive"):
            await fetch_finnhub_quote(client, "AAPL")


async def test_finnhub_missing_key_raises(monkeypatch):
    monkeypatch.setitem(key_store._CACHE, "finnhub", None)

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("client should not be called when key is missing")

    async with _client_with(handler) as client:
        with pytest.raises(ValueError, match="not configured"):
            await fetch_finnhub_quote(client, "AAPL")


async def test_finnhub_network_error_strips_url(monkeypatch):
    """Ensure raised ValueError does not leak the API key (URL)."""
    monkeypatch.setitem(key_store._CACHE, "finnhub", "test-finnhub-key")

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("simulated", request=request)

    async with _client_with(handler) as client:
        with pytest.raises(ValueError) as exc_info:
            await fetch_finnhub_quote(client, "AAPL")

    assert "test-finnhub-key" not in str(exc_info.value)
    assert "finnhub.io" not in str(exc_info.value)


# ---------------------------------------------------------------------------
# Alpha Vantage
# ---------------------------------------------------------------------------


async def test_alpha_vantage_happy_path(monkeypatch):
    monkeypatch.setitem(key_store._CACHE, "alpha_vantage", "AV-TEST-KEY")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "Meta Data": {"2. Symbol": "AAPL"},
                "Time Series (Daily)": {
                    "2026-04-29": {"1. open": "190.00", "4. close": "191.45"},
                    "2026-04-28": {"1. open": "189.00", "4. close": "190.00"},
                },
            },
        )

    async with _client_with(handler) as client:
        price = await fetch_alpha_vantage_quote(client, "AAPL")

    assert price == Decimal("191.45")


async def test_alpha_vantage_note_is_rate_limit(monkeypatch):
    """Alpha Vantage's quirky rate-limit response: HTTP 200 + 'Note' field."""
    monkeypatch.setitem(key_store._CACHE, "alpha_vantage", "AV-TEST-KEY")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"Note": "Thank you for using Alpha Vantage! Our standard API ..."},
        )

    async with _client_with(handler) as client:
        with pytest.raises(ValueError, match="rate limited"):
            await fetch_alpha_vantage_quote(client, "AAPL")


async def test_alpha_vantage_missing_series_raises(monkeypatch):
    monkeypatch.setitem(key_store._CACHE, "alpha_vantage", "AV-TEST-KEY")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"Meta Data": {}})

    async with _client_with(handler) as client:
        with pytest.raises(ValueError, match="missing daily series"):
            await fetch_alpha_vantage_quote(client, "AAPL")


# ---------------------------------------------------------------------------
# CoinGecko
# ---------------------------------------------------------------------------


async def test_coingecko_happy_path(monkeypatch):
    monkeypatch.setitem(key_store._CACHE, "coingecko", "CG-TEST-KEY")

    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"bitcoin": {"eur": 56789.12}})

    async with _client_with(handler) as client:
        price = await fetch_coingecko_quote(client, "bitcoin", "eur")

    assert price == Decimal("56789.12")
    assert "ids=bitcoin" in captured["url"]
    assert "vs_currencies=eur" in captured["url"]


async def test_coingecko_rate_limit_raises(monkeypatch):
    monkeypatch.setitem(key_store._CACHE, "coingecko", "CG-TEST-KEY")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={})

    async with _client_with(handler) as client:
        with pytest.raises(ValueError, match="rate limited"):
            await fetch_coingecko_quote(client, "bitcoin", "eur")


async def test_coingecko_missing_coin_raises(monkeypatch):
    monkeypatch.setitem(key_store._CACHE, "coingecko", "CG-TEST-KEY")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    async with _client_with(handler) as client:
        with pytest.raises(ValueError, match="missing coin_id"):
            await fetch_coingecko_quote(client, "bitcoin", "eur")


# ---------------------------------------------------------------------------
# Live integration smokes (skipped without env keys + PYTEST_LIVE=1)
# ---------------------------------------------------------------------------


@pytest.mark.live
@pytest.mark.skipif(
    os.getenv("PYTEST_LIVE") != "1" or not os.getenv("FINNHUB_API_KEY"),
    reason="set PYTEST_LIVE=1 and FINNHUB_API_KEY to run",
)
async def test_finnhub_live_aapl():
    async with httpx.AsyncClient() as client:
        price = await fetch_finnhub_quote(client, "AAPL")
    assert isinstance(price, Decimal)
    assert price > 0


@pytest.mark.live
@pytest.mark.skipif(
    os.getenv("PYTEST_LIVE") != "1" or not os.getenv("COINGECKO_API_KEY"),
    reason="set PYTEST_LIVE=1 and COINGECKO_API_KEY to run",
)
async def test_coingecko_live_btc():
    async with httpx.AsyncClient() as client:
        price = await fetch_coingecko_quote(client, "bitcoin", "eur")
    assert isinstance(price, Decimal)
    assert price > 0
