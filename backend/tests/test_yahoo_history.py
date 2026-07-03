"""Yahoo history provider (history-only backfill source for FT instruments)."""
from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import httpx
import pytest

from app.services.pricing.errors import PriceProviderRateLimited
from app.services.pricing.yahoo import (
    fetch_yahoo_history,
    ft_ticker_to_yahoo,
    resolve_yahoo_symbol,
)


def _client_with(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _epoch(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp())


def test_ft_ticker_to_yahoo_maps_exchange_qualifiers():
    assert ft_ticker_to_yahoo("VUSA:GER") == "VUSA.DE"
    assert ft_ticker_to_yahoo("EGLN:LSE") == "EGLN.L"
    # bare ISIN (open-end fund) has no exchange qualifier → None
    assert ft_ticker_to_yahoo("IE00BYX5MX67") is None
    assert ft_ticker_to_yahoo(None) is None
    assert ft_ticker_to_yahoo("XXX:UNKNOWN") is None


async def test_fetch_yahoo_history_parses_eur_closes_and_skips_nulls():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"chart": {"result": [{
            "meta": {"currency": "EUR"},
            "timestamp": [_epoch(date(2026, 6, 15)), _epoch(date(2026, 6, 16)), _epoch(date(2026, 6, 17))],
            "indicators": {"quote": [{"close": [123.45, None, 124.5]}]},
        }], "error": None}})

    async with _client_with(handler) as client:
        pts = await fetch_yahoo_history(client, "VUSA.DE", date(2026, 6, 15), date(2026, 6, 17))

    assert [(p.date, p.price) for p in pts] == [
        (date(2026, 6, 15), Decimal("123.4500")),
        (date(2026, 6, 17), Decimal("124.5000")),
    ]


async def test_fetch_yahoo_history_rejects_non_eur_currency():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"chart": {"result": [{
            "meta": {"currency": "USD"},
            "timestamp": [_epoch(date(2026, 6, 17))],
            "indicators": {"quote": [{"close": [84.45]}]},
        }], "error": None}})

    async with _client_with(handler) as client:
        with pytest.raises(ValueError, match="expected EUR"):
            await fetch_yahoo_history(client, "IGLN.L", date(2026, 6, 1), date(2026, 6, 17))


async def test_fetch_yahoo_history_raises_rate_limited_on_429():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="Too Many Requests")

    async with _client_with(handler) as client:
        with pytest.raises(PriceProviderRateLimited):
            await fetch_yahoo_history(client, "VUSA.DE", date(2026, 6, 1), date(2026, 6, 17))


async def test_resolve_yahoo_symbol_exchange_ticker_is_direct():
    # No HTTP needed for exchange tickers.
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("search should not be called for exchange tickers")

    async with _client_with(handler) as client:
        assert await resolve_yahoo_symbol(client, "VUSA:GER") == "VUSA.DE"


async def test_resolve_yahoo_symbol_isin_prefers_morningstar_nav():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"quotes": [
            {"symbol": "IE00BYX5MX67.SG", "quoteType": "MUTUALFUND"},
            {"symbol": "0P0001CLDM.F", "quoteType": "MUTUALFUND"},
            {"symbol": "SOMESTOCK", "quoteType": "EQUITY"},
        ]})

    async with _client_with(handler) as client:
        assert await resolve_yahoo_symbol(client, "IE00BYX5MX67") == "0P0001CLDM.F"
