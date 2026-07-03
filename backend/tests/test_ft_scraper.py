"""FT.com tear-sheet scraper tests.

Mocks HTTP via `httpx.MockTransport`. Live integration test gated on
PYTEST_LIVE=1.
"""
from __future__ import annotations

import os
from decimal import Decimal

import httpx
import pytest

from app.models.instrument import Instrument
from app.services.pricing.ft_scraper import _build_ft_url, fetch_ft_quote


def _client_with(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _fund_instrument(symbol: str = "IE00BYX5NX33") -> Instrument:
    return Instrument(
        id="00000000-0000-0000-0000-000000000001",
        symbol=symbol,
        name="Fidelity MSCI World Index Fund EUR P Acc",
        instrument_type="fund",
        base_currency="EUR",
        price_source="ft",
    )


def _etf_instrument(ticker_override: str | None = "EGLN:LSE") -> Instrument:
    return Instrument(
        id="00000000-0000-0000-0000-000000000002",
        symbol="EGLN",
        name="iShares Physical Gold ETC",
        instrument_type="etf",
        base_currency="EUR",
        price_source="ft",
        ticker_override=ticker_override,
    )


# ---------------------------------------------------------------------------
# URL composition
# ---------------------------------------------------------------------------


def test_ft_funds_url_for_isin():
    inst = _fund_instrument(symbol="IE00BYX5NX33")
    url = _build_ft_url(inst)
    assert url == (
        "https://markets.ft.com/data/funds/tearsheet/summary?s=IE00BYX5NX33:EUR"
    )


def test_ft_etfs_url_requires_ticker_override():
    inst = _etf_instrument(ticker_override=None)
    with pytest.raises(ValueError, match="missing ticker_override"):
        _build_ft_url(inst)


def test_ft_etfs_url_uses_ticker_override():
    inst = _etf_instrument(ticker_override="EGLN:LSE")
    url = _build_ft_url(inst)
    assert url == (
        "https://markets.ft.com/data/etfs/tearsheet/summary?s=EGLN:LSE:EUR"
    )


# ---------------------------------------------------------------------------
# Scrape behaviour
# ---------------------------------------------------------------------------


_HTML_VALID = """
<html><body>
  <div class="mod-tearsheet-overview">
    <ul class="mod-ui-data-list">
      <li>
        <span class="mod-ui-data-list__label">Price (EUR)</span>
        <span class="mod-ui-data-list__value">13.00</span>
      </li>
    </ul>
  </div>
</body></html>
"""

_HTML_NO_SPAN = "<html><body><div>nothing here</div></body></html>"

_HTML_NON_NUMERIC = """
<html><body>
  <span class="mod-ui-data-list__value">—</span>
</body></html>
"""

_HTML_THOUSANDS = """
<html><body>
  <span class="mod-ui-data-list__value">1,234.56</span>
</body></html>
"""


async def test_ft_happy_path():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_HTML_VALID)

    async with _client_with(handler) as client:
        price = await fetch_ft_quote(client, _fund_instrument())
    assert price == Decimal("13.00")


async def test_ft_xpath_empty_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_HTML_NO_SPAN)

    async with _client_with(handler) as client:
        with pytest.raises(ValueError, match="xpath empty"):
            await fetch_ft_quote(client, _fund_instrument())


async def test_ft_non_numeric_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_HTML_NON_NUMERIC)

    async with _client_with(handler) as client:
        with pytest.raises(ValueError, match="non-numeric"):
            await fetch_ft_quote(client, _fund_instrument())


async def test_ft_thousands_separator_stripped():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_HTML_THOUSANDS)

    async with _client_with(handler) as client:
        price = await fetch_ft_quote(client, _fund_instrument())
    assert price == Decimal("1234.56")


async def test_ft_redirect_to_disallowed_host_rejected():
    """A 302 to evil.example.com must be rejected before parsing."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "markets.ft.com":
            return httpx.Response(
                302,
                headers={"Location": "https://evil.example.com/funds/IE00BYX5NX33"},
            )
        # Final hop on an attacker host — body would be parseable HTML but
        # must never be reached / parsed.
        return httpx.Response(200, text=_HTML_VALID)

    async with _client_with(handler) as client:
        with pytest.raises(ValueError, match="disallowed host"):
            await fetch_ft_quote(client, _fund_instrument())


async def test_ft_http_500_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="<html>error</html>")

    async with _client_with(handler) as client:
        with pytest.raises(ValueError, match="ft http 500"):
            await fetch_ft_quote(client, _fund_instrument())


async def test_ft_unsupported_instrument_type_raises():
    inst = Instrument(
        id="x",
        symbol="BTC",
        name="Bitcoin",
        instrument_type="crypto",
        base_currency="EUR",
        price_source="coingecko",
    )

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("client should never be called")

    async with _client_with(handler) as client:
        with pytest.raises(ValueError, match="only supports fund/etf"):
            await fetch_ft_quote(client, inst)


# ---------------------------------------------------------------------------
# Live integration smoke
# ---------------------------------------------------------------------------


@pytest.mark.live
@pytest.mark.skipif(
    os.getenv("PYTEST_LIVE") != "1",
    reason="set PYTEST_LIVE=1 to run live FT scrape",
)
async def test_ft_live_ie00byx5nx33():
    async with httpx.AsyncClient() as client:
        price = await fetch_ft_quote(client, _fund_instrument("IE00BYX5NX33"))
    assert isinstance(price, Decimal)
    assert price > 0
