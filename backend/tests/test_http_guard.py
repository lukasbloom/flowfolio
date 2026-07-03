"""Regression tests for backend/app/core/http_guard.py."""
from __future__ import annotations

import httpx
import pytest

from app.core.http_guard import (
    FORBIDDEN_HOSTS,
    HermeticNetworkViolation,
    install_http_guard,
    uninstall_http_guard,
)


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    uninstall_http_guard()


@pytest.mark.asyncio
async def test_forbidden_host_raises_when_guard_installed():
    install_http_guard()
    async with httpx.AsyncClient() as client:
        with pytest.raises(HermeticNetworkViolation, match=r"finnhub\.io"):
            await client.get("https://finnhub.io/api/v1/quote?symbol=AAPL")


@pytest.mark.asyncio
async def test_localhost_allowed_when_guard_installed():
    """Internal calls (caddy -> api -> web) must continue to work. Forbidden list is host-substring."""
    install_http_guard()
    async with httpx.AsyncClient() as client:
        # We don't expect a response (nothing listens on 127.0.0.1:1 in test env);
        # we expect the guard hook NOT to fire (ConnectError instead).
        with pytest.raises((httpx.ConnectError, httpx.ConnectTimeout)):
            await client.get("http://127.0.0.1:1/should-not-be-blocked", timeout=0.5)


def test_forbidden_hosts_cover_known_external_apis():
    """Belt-and-braces: every external pricing/FX domain the project uses is in the list."""
    expected = {"finnhub.io", "coingecko.com", "frankfurter", "binance.com", "alphavantage.co", "ft.com"}
    haystack = " ".join(FORBIDDEN_HOSTS)
    for needle in expected:
        assert needle in haystack, f"Missing forbidden host: {needle}"
