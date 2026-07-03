"""Pricing service package.

Per-source async clients (finnhub / alpha_vantage / coingecko / ft) and the
per-instrument dispatcher that branches on `instrument.price_source` and
applies the fallback chain.

Caller-commits convention (matches `app.services.fifo.match_lots_for_sell`):
the dispatcher calls `session.add(quote)`; the caller is responsible for
committing the surrounding transaction.
"""
from app.services.pricing.dispatcher import StaleQuoteError, fetch_price

__all__ = ["fetch_price", "StaleQuoteError"]
