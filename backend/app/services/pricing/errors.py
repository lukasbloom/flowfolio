"""Typed pricing errors that callers can pattern-match on, plus the shared
decimal-parse and HTTP-triage helpers used by every pricing/FX provider.

Subclassing ValueError keeps backward compatibility: every existing
`except ValueError` block continues to catch these.

The `parse_positive_decimal` / `raise_for_provider_response` helpers below
were extracted verbatim from per-provider copies (binance, alpha_vantage,
coingecko, twelve_data, finnhub, ft, fx). They are parameterised so each
provider's error-message strings remain byte-identical to the pre-refactor
copies — see the per-provider call sites for the exact (provider, noun)
combination each used.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

import httpx


class PriceProviderError(ValueError):
    """Generic catch-all for upstream-provider failures."""


class PriceProviderRateLimited(PriceProviderError):
    """The upstream price provider returned a rate-limit signal (HTTP 429
    or a JSON `Note`/`Information` body)."""


def parse_positive_decimal(
    raw: object,
    *,
    provider: str,
    context: str,
    noun: str = "close",
    raw_in_nonnumeric: bool = False,
) -> Decimal:
    """Parse `raw` into a finite, strictly-positive Decimal or raise ValueError.

    The error-message wording is parameterised so each provider reproduces its
    historical strings byte-for-byte:

    - `provider`: the provider tag prefix, e.g. "binance", "coingecko".
    - `noun`: the value noun in the message — "close" (binance/alpha_vantage/
      twelve_data), "price" (coingecko/finnhub), or "rate" (frankfurter/fx).
    - `raw_in_nonnumeric`: when True, append `: {raw}` to the non-numeric
      message as well (only fx's `_parse_positive_fx_decimal` did this; every
      provider's non-numeric message omitted the raw value).

    The non-positive message always includes `: {raw}`, matching every prior
    copy.
    """
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        if raw_in_nonnumeric:
            raise ValueError(
                f"{provider} non-numeric {noun} for {context}: {raw}"
            ) from None
        raise ValueError(f"{provider} non-numeric {noun} for {context}") from None
    if not value.is_finite() or value <= 0:
        raise ValueError(f"{provider} non-positive {noun} for {context}: {raw}")
    return value


def raise_for_provider_response(
    resp: httpx.Response,
    *,
    provider: str,
    rate_limited_exc: type[ValueError] = PriceProviderRateLimited,
) -> None:
    """Apply the standard HTTP status triage shared by the pricing providers.

    Reproduces the per-provider triplet exactly:
        - 429 → `rate_limited_exc("<provider> rate limited")`
        - any non-200 status → `ValueError("<provider> http {code}")`

    `rate_limited_exc` defaults to `PriceProviderRateLimited`; finnhub historically
    raised a plain `ValueError` on 429, so it passes `rate_limited_exc=ValueError`
    to keep that exact (non-PriceProviderRateLimited) behavior. The httpx network
    error → "<provider> network error: {type}" wrap stays at each call site because
    its `from None` re-raise and surrounding try/except are provider-specific.
    """
    if resp.status_code == 429:
        raise rate_limited_exc(f"{provider} rate limited")
    if resp.status_code != 200:
        raise ValueError(f"{provider} http {resp.status_code}")
