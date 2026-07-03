"""Dispatcher unit tests.

Mocks the four per-source clients via `monkeypatch` so the dispatcher logic
is tested in isolation. Uses the same in-memory SQLite + attach_sqlite_pragmas
pattern as the earlier tests.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base, attach_sqlite_pragmas
from app.models import Instrument, PriceQuote
from app.services.pricing import StaleQuoteError, fetch_price
from app.services.pricing import dispatcher as dispatcher_mod


@pytest_asyncio.fixture
async def session():
    """In-memory async SQLite session with foreign_keys=ON pragma."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    attach_sqlite_pragmas(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


@pytest.fixture
def http_client():
    """A real-but-unused AsyncClient handed to fetch_price.

    Backed by a MockTransport that always raises — proves the dispatcher
    never makes real network calls because each test patches the per-source
    client functions.
    """
    def handler(request):  # pragma: no cover
        raise AssertionError(
            f"dispatcher should never call HTTP directly in tests: {request.url}"
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def _seed_instrument(
    session: AsyncSession,
    *,
    symbol: str,
    instrument_type: str,
    price_source: str,
    base_currency: str = "EUR",
    ticker_override: str | None = None,
) -> Instrument:
    inst = Instrument(
        symbol=symbol,
        name=f"Test {symbol}",
        instrument_type=instrument_type,
        base_currency=base_currency,
        price_source=price_source,
        ticker_override=ticker_override,
    )
    session.add(inst)
    await session.flush()
    return inst


# ---------------------------------------------------------------------------
# Stock branch — finnhub primary, alpha_vantage fallback
# ---------------------------------------------------------------------------


async def test_stock_uses_finnhub_first(session, http_client, monkeypatch):
    inst = await _seed_instrument(
        session, symbol="AAPL", instrument_type="stock", price_source="finnhub"
    )

    finnhub_calls: list[str] = []
    av_calls: list[str] = []

    async def fake_finnhub(client, symbol):
        finnhub_calls.append(symbol)
        return Decimal("191.45")

    async def fake_av(client, symbol):  # pragma: no cover
        av_calls.append(symbol)
        return Decimal("0")

    monkeypatch.setattr(dispatcher_mod, "fetch_finnhub_quote", fake_finnhub)
    monkeypatch.setattr(dispatcher_mod, "fetch_alpha_vantage_quote", fake_av)

    quote = await fetch_price(session, http_client, inst, today=date(2026, 4, 30))

    assert quote.source == "finnhub"
    assert quote.price == Decimal("191.45")
    assert quote.currency == "EUR"
    assert finnhub_calls == ["AAPL"]
    assert av_calls == []  # fallback NOT triggered


async def test_stock_falls_back_to_alpha_vantage(session, http_client, monkeypatch):
    inst = await _seed_instrument(
        session, symbol="AAPL", instrument_type="stock", price_source="finnhub"
    )

    async def fake_finnhub(client, symbol):
        raise ValueError("finnhub rate limited")

    async def fake_av(client, symbol):
        return Decimal("190.00")

    monkeypatch.setattr(dispatcher_mod, "fetch_finnhub_quote", fake_finnhub)
    monkeypatch.setattr(dispatcher_mod, "fetch_alpha_vantage_quote", fake_av)

    quote = await fetch_price(session, http_client, inst, today=date(2026, 4, 30))

    assert quote.source == "alpha_vantage"
    assert quote.price == Decimal("190.00")


async def test_stock_both_fail_raises_stale(session, http_client, monkeypatch):
    inst = await _seed_instrument(
        session, symbol="AAPL", instrument_type="stock", price_source="finnhub"
    )
    # Pre-seed a cached quote so StaleQuoteError.last_quote is populated.
    cached = PriceQuote(
        instrument_id=inst.id,
        date=date(2026, 4, 28),
        price=Decimal("188.00"),
        currency="EUR",
        source="finnhub",
        fetched_at=datetime(2026, 4, 28, 22, 0, 0),
    )
    session.add(cached)
    await session.commit()

    async def fake_finnhub(client, symbol):
        raise ValueError("finnhub rate limited")

    async def fake_av(client, symbol):
        raise ValueError("alpha_vantage rate limited")

    monkeypatch.setattr(dispatcher_mod, "fetch_finnhub_quote", fake_finnhub)
    monkeypatch.setattr(dispatcher_mod, "fetch_alpha_vantage_quote", fake_av)

    with pytest.raises(StaleQuoteError) as exc_info:
        await fetch_price(session, http_client, inst, today=date(2026, 4, 30))

    err = exc_info.value
    assert err.last_quote is not None
    assert err.last_quote.price == Decimal("188.00")
    assert "AAPL" in str(err)


# ---------------------------------------------------------------------------
# Crypto branch — coingecko only, no fallback
# ---------------------------------------------------------------------------


async def test_crypto_uses_coingecko_only(session, http_client, monkeypatch):
    inst = await _seed_instrument(
        session,
        symbol="bitcoin",
        instrument_type="crypto",
        price_source="coingecko",
        base_currency="EUR",
    )

    cg_calls: list[tuple[str, str]] = []
    finnhub_calls: list[str] = []

    async def fake_cg(client, coin_id, vs_currency):
        cg_calls.append((coin_id, vs_currency))
        return Decimal("56789.12")

    async def fake_finnhub(client, symbol):  # pragma: no cover
        finnhub_calls.append(symbol)
        return Decimal("0")

    monkeypatch.setattr(dispatcher_mod, "fetch_coingecko_quote", fake_cg)
    monkeypatch.setattr(dispatcher_mod, "fetch_finnhub_quote", fake_finnhub)

    quote = await fetch_price(session, http_client, inst, today=date(2026, 4, 30))

    assert quote.source == "coingecko"
    assert quote.price == Decimal("56789.12")
    assert cg_calls == [("bitcoin", "eur")]
    assert finnhub_calls == []


# ---------------------------------------------------------------------------
# FT branch — fund / etf, no fallback
# ---------------------------------------------------------------------------


async def test_fund_uses_ft_only(session, http_client, monkeypatch):
    inst = await _seed_instrument(
        session,
        symbol="IE00BYX5NX33",
        instrument_type="fund",
        price_source="ft",
        base_currency="EUR",
    )

    ft_calls: list[str] = []

    async def fake_ft(client, instrument):
        ft_calls.append(instrument.symbol)
        return Decimal("13.00")

    monkeypatch.setattr(dispatcher_mod, "fetch_ft_quote", fake_ft)

    quote = await fetch_price(session, http_client, inst, today=date(2026, 4, 30))

    assert quote.source == "ft"
    assert quote.price == Decimal("13.00")
    assert ft_calls == ["IE00BYX5NX33"]


# ---------------------------------------------------------------------------
# Manual override wins
# ---------------------------------------------------------------------------


async def test_manual_override_wins_over_api(session, http_client, monkeypatch):
    inst = await _seed_instrument(
        session, symbol="AAPL", instrument_type="stock", price_source="finnhub"
    )
    today = date(2026, 4, 30)
    manual = PriceQuote(
        instrument_id=inst.id,
        date=today,
        price=Decimal("100.00"),
        currency="EUR",
        source="manual",
    )
    session.add(manual)
    await session.commit()

    async def fake_finnhub(client, symbol):  # pragma: no cover
        raise AssertionError("manual override must short-circuit before API call")

    monkeypatch.setattr(dispatcher_mod, "fetch_finnhub_quote", fake_finnhub)

    quote = await fetch_price(session, http_client, inst, today=today)

    assert quote.source == "manual"
    assert quote.price == Decimal("100.00")


# ---------------------------------------------------------------------------
# Caller-commits contract
# ---------------------------------------------------------------------------


async def test_caller_commits_contract(session, http_client, monkeypatch):
    """The dispatcher must add the new quote but NOT commit it."""
    inst = await _seed_instrument(
        session, symbol="AAPL", instrument_type="stock", price_source="finnhub"
    )

    async def fake_finnhub(client, symbol):
        return Decimal("191.45")

    monkeypatch.setattr(dispatcher_mod, "fetch_finnhub_quote", fake_finnhub)

    quote = await fetch_price(session, http_client, inst, today=date(2026, 4, 30))

    # The new quote is in session.new (pending), not yet committed.
    assert quote in session.new


# ---------------------------------------------------------------------------
# Unknown source
# ---------------------------------------------------------------------------


async def test_unknown_source_raises_stale(session, http_client):
    """An unknown price_source surfaces as StaleQuoteError (the dispatcher's
    outer try/except wraps the inner ValueError).

    The user sees the holding go stale rather than the cron crashing.
    """
    inst = await _seed_instrument(
        session, symbol="???", instrument_type="stock", price_source="bogus"
    )

    with pytest.raises(StaleQuoteError, match="unknown price_source"):
        await fetch_price(session, http_client, inst, today=date(2026, 4, 30))


# ---------------------------------------------------------------------------
# Manual-only instrument with no cache
# ---------------------------------------------------------------------------


async def test_manual_only_instrument_without_cache_raises_stale(
    session, http_client
):
    inst = await _seed_instrument(
        session,
        symbol="OPAQUE-FUND",
        instrument_type="fund",
        price_source="manual",
    )

    with pytest.raises(StaleQuoteError, match="no cached price"):
        await fetch_price(session, http_client, inst, today=date(2026, 4, 30))


async def test_manual_only_instrument_with_cache_returns_latest(
    session, http_client
):
    inst = await _seed_instrument(
        session,
        symbol="OPAQUE-FUND",
        instrument_type="fund",
        price_source="manual",
    )
    cached = PriceQuote(
        instrument_id=inst.id,
        date=date(2026, 4, 25),
        price=Decimal("9.99"),
        currency="EUR",
        source="manual",
    )
    session.add(cached)
    await session.commit()

    quote = await fetch_price(session, http_client, inst, today=date(2026, 4, 30))
    assert quote.price == Decimal("9.99")
    assert quote.source == "manual"
