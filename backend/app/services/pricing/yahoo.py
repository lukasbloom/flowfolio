"""Yahoo Finance v8 chart — HISTORY-ONLY provider for FT-priced instruments.

Why this exists (and why it's history-only): CLAUDE.md blacklists Yahoo for
*scheduled live* polling — its unofficial endpoints rate-limit aggressively and
IP-ban hosts that poll them every day. A one-shot historical backfill of a
handful of symbols does not trip that, and Yahoo is the only free source with
daily history for European UCITS funds/ETFs. So this mirrors the existing
live/history split (finnhub live → twelve_data history; ft live → yahoo history):
the FT scraper stays the LIVE price source; Yahoo only fills `price_quote`
history during backfill. Do NOT wire this into the daily scheduler.

Resolution:
- ETF / metal: the FT `ticker_override` carries an exchange qualifier
  (`VUSA:GER`, `EGLN:LSE`) → mapped to a Yahoo symbol (`VUSA.DE`, `EGLN.L`).
- Fund: the FT `ticker_override` is a bare ISIN → resolved via Yahoo search to
  its Morningstar NAV symbol (`0P……F`).
All targets are EUR-quoted; `fetch_yahoo_history` asserts the chart currency is
EUR so a mis-resolved non-EUR symbol can never be stored as if it were EUR.
"""
from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import httpx

from app.core.config import settings
from app.services.pricing.errors import PriceProviderRateLimited
from app.services.pricing.types import HistoricalPrice

logger = logging.getLogger(__name__)

YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
YAHOO_SEARCH_URL = "https://query1.finance.yahoo.com/v1/finance/search"
# Yahoo's WAF 429s the full desktop-Chrome UA string (flagged as a scraper
# pattern) but lets a bare "Mozilla/5.0" through — verified empirically: the
# long UA gets 429 on every request while this one returns 200. Keep it minimal.
YAHOO_USER_AGENT = "Mozilla/5.0"
# FT exchange qualifier (in ticker_override) -> Yahoo exchange suffix.
_FT_TO_YAHOO_SUFFIX = {"GER": ".DE", "LSE": ".L"}


def ft_ticker_to_yahoo(ticker_override: str | None) -> str | None:
    """Map an FT exchange ticker (`VUSA:GER`, `EGLN:LSE`) to a Yahoo symbol
    (`VUSA.DE`, `EGLN.L`). Returns None for tickers without a known exchange
    qualifier (e.g. an open-end fund's bare ISIN — resolve those via search)."""
    if not ticker_override or ":" not in ticker_override:
        return None
    base, _, exch = ticker_override.partition(":")
    suffix = _FT_TO_YAHOO_SUFFIX.get(exch)
    return f"{base}{suffix}" if suffix else None


async def resolve_yahoo_symbol(
    client: httpx.AsyncClient, ticker_override: str | None
) -> str | None:
    """Resolve an FT `ticker_override` to a Yahoo chart symbol.

    Exchange tickers map directly; a bare ISIN is looked up via Yahoo search,
    preferring the Morningstar NAV feed (`0P……`) then any mutual-fund match.
    Returns None when nothing usable resolves (caller → manual_history_required).
    """
    direct = ft_ticker_to_yahoo(ticker_override)
    if direct is not None:
        return direct
    if not ticker_override:
        return None
    try:
        resp = await client.get(
            YAHOO_SEARCH_URL,
            params={"q": ticker_override, "quotesCount": 6, "newsCount": 0},
            headers={"User-Agent": YAHOO_USER_AGENT},
            timeout=settings.pricing_timeout_seconds,
            follow_redirects=True,
        )
    except httpx.HTTPError as e:
        raise ValueError(f"yahoo search network error: {type(e).__name__}") from None
    if resp.status_code == 429:
        raise PriceProviderRateLimited("yahoo search rate limited")
    if resp.status_code != 200:
        raise ValueError(f"yahoo search http {resp.status_code}")
    quotes = resp.json().get("quotes", []) or []
    funds = [q for q in quotes if q.get("quoteType") == "MUTUALFUND" and q.get("symbol")]
    morningstar = [q for q in funds if str(q["symbol"]).startswith("0P")]
    chosen = (morningstar or funds)
    return chosen[0]["symbol"] if chosen else None


async def fetch_yahoo_history(
    client: httpx.AsyncClient, symbol: str, start: date, end: date
) -> list[HistoricalPrice]:
    """Return daily EUR close/NAV history for a Yahoo `symbol` in [start, end].

    Raises:
        PriceProviderRateLimited: HTTP 429.
        ValueError: network error, non-200, chart error, non-EUR currency, or
            no usable closes.
    """
    period1 = int(datetime(start.year, start.month, start.day, tzinfo=UTC).timestamp())
    period2 = int(
        (datetime(end.year, end.month, end.day, tzinfo=UTC) + timedelta(days=1)).timestamp()
    )
    try:
        resp = await client.get(
            YAHOO_CHART_URL.format(symbol=symbol),
            params={"period1": period1, "period2": period2, "interval": "1d"},
            headers={"User-Agent": YAHOO_USER_AGENT},
            timeout=settings.pricing_timeout_seconds,
            follow_redirects=True,
        )
    except httpx.HTTPError as e:
        raise ValueError(f"yahoo network error: {type(e).__name__}") from None
    if resp.status_code == 429:
        raise PriceProviderRateLimited("yahoo rate limited")
    if resp.status_code != 200:
        raise ValueError(f"yahoo http {resp.status_code}")

    chart = resp.json().get("chart", {})
    if chart.get("error"):
        raise ValueError(f"yahoo chart error for {symbol}: {chart['error']}")
    result = chart.get("result")
    if not result:
        raise ValueError(f"yahoo empty result for {symbol}")
    res = result[0]
    currency = res.get("meta", {}).get("currency")
    if currency != "EUR":
        # Defensive: we only ever resolve EUR-listed tickers / EUR NAV feeds.
        raise ValueError(f"yahoo {symbol} currency is {currency!r}, expected EUR")

    timestamps = res.get("timestamp") or []
    quote = (res.get("indicators", {}).get("quote") or [{}])[0]
    closes = quote.get("close") or []
    out: list[HistoricalPrice] = []
    seen: set[date] = set()
    for ts, close in zip(timestamps, closes):
        if close is None:
            continue
        d = datetime.fromtimestamp(ts, UTC).date()
        if d in seen:
            continue
        price = Decimal(str(close)).quantize(Decimal("0.0001"))
        if price <= 0:
            continue
        seen.add(d)
        out.append(HistoricalPrice(date=d, price=price))
    if not out:
        raise ValueError(f"yahoo no usable closes for {symbol}")
    return out
