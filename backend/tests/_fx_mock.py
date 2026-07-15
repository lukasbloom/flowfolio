"""Shared Frankfurter mock helpers for FX-locking tests.

patch_frankfurter rebinds app.services.fx.httpx.AsyncClient to a
MockTransport-backed factory. REAL_ASYNC_CLIENT is captured once at import
time, before any test patches, so patching twice in one test REPLACES the
mock transport instead of wrapping the previous factory around it.
"""
from __future__ import annotations

import httpx

REAL_ASYNC_CLIENT = httpx.AsyncClient


def patch_frankfurter(monkeypatch, handler) -> None:
    """Route the `async with httpx.AsyncClient()` block inside
    app.services.fx.resolve_locked_fx_rate through `handler`."""
    transport = httpx.MockTransport(handler)

    def factory(*args, **kwargs):
        kwargs["transport"] = transport
        return REAL_ASYNC_CLIENT(*args, **kwargs)

    monkeypatch.setattr("app.services.fx.httpx.AsyncClient", factory)


def frankfurter_ok(rate: str, date_str: str):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "amount": 1,
                "base": "EUR",
                "date": date_str,
                "rates": {"USD": float(rate)},
            },
        )

    return handler


def frankfurter_500():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "upstream"})

    return handler


def frankfurter_must_not_be_called():
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("must NOT call Frankfurter")

    return handler
