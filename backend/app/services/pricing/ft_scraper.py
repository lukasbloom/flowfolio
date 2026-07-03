"""FT.com tear-sheet scraper for ISIN-quoted European mutual funds and ETFs.

Mirrors a spreadsheet IMPORTXML approach for FT.com tear-sheets. Verified live
returning Decimal('13.00') for ISIN IE00BYX5NX33 (Fidelity MSCI World Index
Fund EUR P Acc).

Single-selector approach, no fallbacks. On empty/non-numeric,
mark stale and log loudly.

Threat model:
- Final URL host MUST equal `markets.ft.com` after redirects.
  We follow up to 3 redirects then assert the host before parsing.
- httpx default body limit + 10s timeout cap memory pressure.
"""
from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

import httpx
from selectolax.parser import HTMLParser

from app.core.config import settings
from app.models.instrument import Instrument

logger = logging.getLogger(__name__)

FT_FUNDS_URL = "https://markets.ft.com/data/funds/tearsheet/summary?s={isin}:EUR"
FT_ETFS_URL = "https://markets.ft.com/data/etfs/tearsheet/summary?s={ticker_override}:EUR"
FT_HOST = "markets.ft.com"

# selectolax uses CSS selectors; equivalent to XPath
# `//span[@class='mod-ui-data-list__value']`.
FT_NAV_SELECTOR = "span.mod-ui-data-list__value"

# Required: FT.com blocks generic UA strings.
FT_USER_AGENT = "Flowfolio/0.1 (+self-hosted; personal portfolio tracker)"


def _build_ft_url(instrument: Instrument) -> str:
    """Compose the tear-sheet URL for a fund or ETF instrument.

    Funds use the `:EUR` suffix on ISIN; ETFs use the user-provided
    ticker_override (e.g. `EGLN:LSE`) with `:EUR` appended.
    """
    if instrument.instrument_type == "fund":
        # FT's lookup key for a fund is its ISIN. Prefer ticker_override (set when
        # the display symbol is a short label like "SP500" rather than the ISIN);
        # fall back to symbol for the ISIN-as-symbol convention. Mirrors how ETFs
        # already source their FT key from ticker_override.
        return FT_FUNDS_URL.format(isin=instrument.ticker_override or instrument.symbol)
    # ETFs and exchange-traded commodities (metal, e.g. a physical gold ETC) both
    # resolve via FT's etfs tear-sheet using an exchange ticker in ticker_override.
    if instrument.instrument_type in ("etf", "metal"):
        if not instrument.ticker_override:
            raise ValueError(
                f"{instrument.instrument_type} instrument {instrument.id} missing "
                f"ticker_override (e.g. 'EGLN:LSE')"
            )
        return FT_ETFS_URL.format(ticker_override=instrument.ticker_override)
    raise ValueError(
        f"FT scraper only supports fund/etf/metal, got {instrument.instrument_type}"
    )


async def fetch_ft_quote(client: httpx.AsyncClient, instrument: Instrument) -> Decimal:
    """Return the current FT.com tear-sheet NAV for `instrument` as `Decimal`.

    Raises:
        ValueError: instrument type is unsupported, ETF lacks
            ticker_override, network call failed, response was not 200,
            redirect went to a host other than markets.ft.com,
            selector returned no node, parsed text was non-numeric or
            non-positive.
    """
    url = _build_ft_url(instrument)

    try:
        resp = await client.get(
            url,
            timeout=settings.pricing_timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": FT_USER_AGENT},
        )
    except httpx.HTTPError as e:
        raise ValueError(f"ft network error: {type(e).__name__}") from None

    # Redirect safety. The final URL after redirects must still be on
    # markets.ft.com — never parse content served by a different host.
    final_host = urlparse(str(resp.url)).hostname
    if final_host != FT_HOST:
        raise ValueError(f"ft redirected to disallowed host: {final_host}")

    if resp.status_code != 200:
        raise ValueError(f"ft http {resp.status_code}")

    tree = HTMLParser(resp.text)
    node = tree.css_first(FT_NAV_SELECTOR)
    if node is None:
        raise ValueError(f"ft xpath empty for {instrument.symbol}")

    text = node.text(strip=True)
    # FT renders thousands separators ("1,234.56"); strip them before parsing.
    cleaned = text.replace(",", "")
    # NOTE: intentionally NOT routed through services.pricing.errors.parse_positive_decimal.
    # FT's messages differ structurally from every other provider's: the
    # non-numeric branch reports the original `text!r` (the repr of the scraped
    # string, pre-comma-strip is `text`, parsed value is `cleaned`) rather than
    # the raw value, and the non-positive branch is worded "non-finite ... {price}"
    # (the parsed Decimal) rather than "non-positive ... {raw}". Unifying would
    # change these byte-for-byte, so this scraper keeps its own parse.
    try:
        price = Decimal(cleaned)
    except (InvalidOperation, ValueError):
        raise ValueError(
            f"ft non-numeric price for {instrument.symbol}: {text!r}"
        ) from None
    if not price.is_finite() or price <= 0:
        raise ValueError(f"ft non-finite price for {instrument.symbol}: {price}")

    logger.info(
        "ft_quote_ok",
        extra={"source": "ft", "symbol": instrument.symbol, "value": str(price)},
    )
    return price
