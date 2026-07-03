"""httpx outbound guard for hermetic network enforcement on the test stack.

When settings.network_hermetic is True, install_http_guard() monkey-patches
httpx.AsyncClient so that every request is inspected and any host matching
FORBIDDEN_HOSTS raises an AssertionError before the request leaves the process.

Mechanism (chosen over alternatives):
- Docker network drop (`network_mode: none`): would break the api↔caddy↔web
  internal docker network. Rejected.
- Bogus API keys alone: clients still resolve DNS + reach the wire. Insufficient
  for hermetic claim.
- httpx event hook + monkey-patched __init__: catches every AsyncClient
  regardless of construction site. Chosen.

The forbidden host list reflects every external API surface in backend/app/services/
that costs free-tier quota (Finnhub/CoinGecko/Frankfurter/Binance/Alpha Vantage/FT).
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import httpx

# Hosts forbidden when running with the hermetic flag set. Substring match —
# both api.coingecko.com and coingecko.com are caught by "coingecko.com".
FORBIDDEN_HOSTS: tuple[str, ...] = (
    "finnhub.io",
    "coingecko.com",
    "frankfurter.app",
    "frankfurter.dev",
    "binance.com",
    "alphavantage.co",
    "ft.com",
)


class HermeticNetworkViolation(AssertionError):
    """Raised when a hermetic-mode httpx client attempts to reach a forbidden host."""


async def _hermetic_request_hook(request: httpx.Request) -> None:
    host = (request.url.host or "").lower()
    for forbidden in FORBIDDEN_HOSTS:
        if forbidden in host:
            raise HermeticNetworkViolation(
                f"Hermetic network mode active (FLOWFOLIO_NETWORK_HERMETIC=true): "
                f"httpx call to {request.url} blocked. Host '{host}' matched forbidden "
                f"substring '{forbidden}'. If this fires in a non-test environment, the "
                f"compose.test.yml flag has leaked — check FLOWFOLIO_NETWORK_HERMETIC."
            )


_INSTALLED = False
_ORIGINAL_INIT: Any = None


def install_http_guard() -> None:
    """Idempotent install. Call once at startup when settings.network_hermetic is True."""
    global _INSTALLED, _ORIGINAL_INIT
    if _INSTALLED:
        return
    _ORIGINAL_INIT = httpx.AsyncClient.__init__

    def patched_init(self: httpx.AsyncClient, *args: Any, **kwargs: Any) -> None:
        existing: Iterable[Any] = kwargs.get("event_hooks", {}).get("request", []) or []
        kwargs["event_hooks"] = {
            **kwargs.get("event_hooks", {}),
            "request": [*existing, _hermetic_request_hook],
        }
        _ORIGINAL_INIT(self, *args, **kwargs)

    httpx.AsyncClient.__init__ = patched_init  # type: ignore[method-assign]
    _INSTALLED = True


def uninstall_http_guard() -> None:
    """Test-only — restore the original __init__ between tests."""
    global _INSTALLED, _ORIGINAL_INIT
    if not _INSTALLED:
        return
    httpx.AsyncClient.__init__ = _ORIGINAL_INIT  # type: ignore[method-assign]
    _ORIGINAL_INIT = None
    _INSTALLED = False
