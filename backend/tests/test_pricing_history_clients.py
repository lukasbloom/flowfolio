"""Historical pricing and FX client tests."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import httpx
import pytest

from app.services import key_store
from app.services.fx import fetch_fx_range
from app.services.pricing.alpha_vantage import fetch_alpha_vantage_history
from app.services.pricing.coingecko import fetch_coingecko_history


def _client_with(handler) -> httpx.AsyncClient:
    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(transport=transport)


async def test_alpha_vantage_history_uses_full_output_and_parses_daily_closes(
    monkeypatch,
):
    monkeypatch.setitem(key_store._CACHE, "alpha_vantage", "AV-TEST-KEY")
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(
            200,
            json={
                "Meta Data": {"2. Symbol": "AAPL"},
                "Time Series (Daily)": {
                    "2026-04-29": {"4. close": "191.45"},
                    "2026-04-28": {"4. close": "190.00"},
                },
            },
        )

    async with _client_with(handler) as client:
        history = await fetch_alpha_vantage_history(client, "AAPL")

    assert "function=TIME_SERIES_DAILY" in captured["url"]
    assert "outputsize=full" in captured["url"]
    assert [(price.date, price.price) for price in history] == [
        (date(2026, 4, 28), Decimal("190.00")),
        (date(2026, 4, 29), Decimal("191.45")),
    ]


async def test_alpha_vantage_history_sanitizes_network_errors(monkeypatch):
    monkeypatch.setitem(key_store._CACHE, "alpha_vantage", "AV-TEST-KEY")

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("leaky https://example.test?apikey=AV-TEST-KEY")

    async with _client_with(handler) as client:
        with pytest.raises(ValueError) as exc_info:
            await fetch_alpha_vantage_history(client, "AAPL")

    assert "AV-TEST-KEY" not in str(exc_info.value)
    assert "example.test" not in str(exc_info.value)


async def test_alpha_vantage_history_rejects_non_positive_values(monkeypatch):
    monkeypatch.setitem(key_store._CACHE, "alpha_vantage", "AV-TEST-KEY")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"Time Series (Daily)": {"2026-04-29": {"4. close": "0"}}},
        )

    async with _client_with(handler) as client:
        with pytest.raises(ValueError, match="non-positive"):
            await fetch_alpha_vantage_history(client, "AAPL")


async def test_coingecko_history_uses_market_chart_daily(monkeypatch):
    monkeypatch.setitem(key_store._CACHE, "coingecko", "CG-TEST-KEY")
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(
            200,
            json={
                "prices": [
                    [1777334400000, 56789.12],
                    [1777420800000, "57000.00"],
                ]
            },
        )

    async with _client_with(handler) as client:
        history = await fetch_coingecko_history(client, "bitcoin", "eur")

    assert "/coins/bitcoin/market_chart" in captured["url"]
    assert "days=max" in captured["url"]
    assert "interval=daily" in captured["url"]
    assert [(price.date, price.price) for price in history] == [
        (date(2026, 4, 28), Decimal("56789.12")),
        (date(2026, 4, 29), Decimal("57000.00")),
    ]


async def test_coingecko_history_rejects_non_finite_values(monkeypatch):
    monkeypatch.setitem(key_store._CACHE, "coingecko", "CG-TEST-KEY")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"prices": [[1777334400000, "NaN"]]})

    async with _client_with(handler) as client:
        with pytest.raises(ValueError, match="non-positive"):
            await fetch_coingecko_history(client, "bitcoin", "eur")


async def test_fx_range_fetches_frankfurter_range():
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(
            200,
            json={
                "base": "EUR",
                "rates": {
                    "2026-04-28": {"USD": 1.13},
                    "2026-04-29": {"USD": "1.14"},
                },
            },
        )

    async with _client_with(handler) as client:
        history = await fetch_fx_range(
            client, date(2026, 4, 28), date(2026, 4, 29)
        )

    assert "/2026-04-28..2026-04-29" in captured["url"]
    assert "base=EUR" in captured["url"]
    assert "symbols=USD" in captured["url"]
    assert history == [
        (date(2026, 4, 28), Decimal("1.13")),
        (date(2026, 4, 29), Decimal("1.14")),
    ]


async def test_fx_range_sanitizes_network_errors():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("leaky https://frankfurter.dev/v1")

    async with _client_with(handler) as client:
        with pytest.raises(ValueError) as exc_info:
            await fetch_fx_range(client, date(2026, 4, 28), date(2026, 4, 29))

    assert "frankfurter.dev" not in str(exc_info.value)
