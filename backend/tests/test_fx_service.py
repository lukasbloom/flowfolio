"""Frankfurter FX client + cache layer tests.

Mocks HTTP via `httpx.MockTransport` (matches the test_pricing_clients.py
pattern, no pytest-httpx dep needed).
"""
from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base, attach_sqlite_pragmas
from app.models.fx_rate import FxRate
from app.services.fx import fetch_fx_rate, get_or_fetch_fx_rate


def _client_with(handler) -> httpx.AsyncClient:
    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(transport=transport)


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    attach_sqlite_pragmas(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


# ---------------------------------------------------------------------------
# fetch_fx_rate — direct Frankfurter call
# ---------------------------------------------------------------------------


async def test_fetch_happy_path_eur_usd():
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(
            200,
            json={
                "amount": 1,
                "base": "EUR",
                "date": "2025-01-15",
                "rates": {"USD": 1.0234},
            },
        )

    async with _client_with(handler) as client:
        rate, actual = await fetch_fx_rate(client, date(2025, 1, 15), "EUR", "USD")

    assert rate == Decimal("1.0234")
    assert isinstance(rate, Decimal)
    assert actual == date(2025, 1, 15)
    assert "api.frankfurter.dev" in captured["url"]
    assert "2025-01-15" in captured["url"]
    assert "base=EUR" in captured["url"]
    assert "symbols=USD" in captured["url"]


async def test_fetch_walkback_logged_when_actual_differs(caplog):
    """Saturday request → Frankfurter returns Friday's rate; log captures walkback."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "amount": 1,
                "base": "EUR",
                "date": "2025-01-17",  # Friday
                "rates": {"USD": 1.0289},
            },
        )

    async with _client_with(handler) as client:
        with caplog.at_level(logging.INFO, logger="app.services.fx"):
            rate, actual = await fetch_fx_rate(
                client, date(2025, 1, 18), "EUR", "USD"  # Saturday
            )

    assert actual == date(2025, 1, 17)
    assert rate == Decimal("1.0289")
    assert any("fx_walkback_used" in r.message for r in caplog.records)


async def test_fetch_404_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "not found"})

    async with _client_with(handler) as client:
        with pytest.raises(ValueError, match="no data"):
            await fetch_fx_rate(client, date(1980, 1, 1), "EUR", "USD")


async def test_fetch_non_numeric_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "amount": 1,
                "base": "EUR",
                "date": "2025-01-15",
                "rates": {"USD": "abc"},
            },
        )

    async with _client_with(handler) as client:
        with pytest.raises(ValueError, match="non-numeric"):
            await fetch_fx_rate(client, date(2025, 1, 15), "EUR", "USD")


async def test_fetch_invalid_currency_rejected():
    """Only EUR and USD are supported."""
    called: dict[str, bool] = {"hit": False}

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        called["hit"] = True
        raise AssertionError("must reject before HTTP call")

    async with _client_with(handler) as client:
        with pytest.raises(ValueError, match="only"):
            await fetch_fx_rate(client, date(2025, 1, 15), "GBP", "EUR")

    assert called["hit"] is False


# ---------------------------------------------------------------------------
# get_or_fetch_fx_rate — cache layer
# ---------------------------------------------------------------------------


async def test_get_or_fetch_cache_hit_no_http(session: AsyncSession):
    """Pre-existing FxRate row → cache hit, mock NOT called."""
    cached = FxRate(
        date=date(2025, 1, 15),
        base_currency="EUR",
        quote_currency="USD",
        rate=Decimal("1.0234"),
        source="frankfurter",
    )
    session.add(cached)
    await session.commit()

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("HTTP must not be called on cache hit")

    async with _client_with(handler) as client:
        row = await get_or_fetch_fx_rate(
            session, client, date(2025, 1, 15), "EUR", "USD"
        )

    assert row.rate == Decimal("1.0234")
    assert row.id == cached.id


async def test_get_or_fetch_cache_miss_writes_row(session: AsyncSession):
    """Empty cache → fetch + session.add(); row appears in session.new."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "amount": 1,
                "base": "EUR",
                "date": "2025-02-10",
                "rates": {"USD": 1.0876},
            },
        )

    async with _client_with(handler) as client:
        row = await get_or_fetch_fx_rate(
            session, client, date(2025, 2, 10), "EUR", "USD"
        )

    assert row.rate == Decimal("1.0876")
    assert row.source == "frankfurter"
    # Service must NOT commit — row is staged in session.new
    assert row in session.new
    await session.commit()  # caller commits

    result = await session.execute(
        select(FxRate).where(FxRate.date == date(2025, 2, 10))
    )
    fetched = result.scalar_one()
    assert fetched.rate == Decimal("1.0876")


async def test_get_or_fetch_walkback_dedupe(session: AsyncSession):
    """Cache has Friday row; Saturday lookup walks back and returns existing row."""
    friday = FxRate(
        date=date(2025, 1, 17),
        base_currency="EUR",
        quote_currency="USD",
        rate=Decimal("1.0289"),
        source="frankfurter",
    )
    session.add(friday)
    await session.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "amount": 1,
                "base": "EUR",
                "date": "2025-01-17",  # walked back
                "rates": {"USD": 1.0289},
            },
        )

    async with _client_with(handler) as client:
        row = await get_or_fetch_fx_rate(
            session, client, date(2025, 1, 18), "EUR", "USD"  # Saturday
        )

    assert row.id == friday.id
    # No new row staged
    result = await session.execute(
        select(FxRate).where(FxRate.date == date(2025, 1, 17))
    )
    rows = result.scalars().all()
    assert len(rows) == 1


async def test_get_or_fetch_walkback_caches_requested_date(session: AsyncSession):
    """Regression: a walked-back lookup must also cache the requested date.

    Without this, every Saturday/Sunday lookup misses on date==Saturday, calls
    Frankfurter, walks back to Friday (already cached or not), and is silently
    deduped — defeating the cache for every non-business-day txn save.
    """
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(
            200,
            json={
                "amount": 1,
                "base": "EUR",
                "date": "2025-01-17",  # Friday
                "rates": {"USD": 1.0289},
            },
        )

    async with _client_with(handler) as client:
        # First Saturday lookup: cache empty, calls Frankfurter, gets walked
        # back to Friday. Should stage rows for BOTH Saturday and Friday.
        first = await get_or_fetch_fx_rate(
            session, client, date(2025, 1, 18), "EUR", "USD"
        )
        await session.commit()
        # Second Saturday lookup MUST hit cache (no second HTTP call).
        second = await get_or_fetch_fx_rate(
            session, client, date(2025, 1, 18), "EUR", "USD"
        )

    assert call_count["n"] == 1, "weekend re-lookup should not re-hit Frankfurter"
    assert first.rate == Decimal("1.0289")
    assert second.rate == Decimal("1.0289")
    # Both dates persisted.
    saturday = await session.execute(
        select(FxRate).where(FxRate.date == date(2025, 1, 18))
    )
    friday = await session.execute(
        select(FxRate).where(FxRate.date == date(2025, 1, 17))
    )
    assert saturday.scalar_one().rate == Decimal("1.0289")
    assert friday.scalar_one().rate == Decimal("1.0289")


async def test_get_or_fetch_caller_commits(session: AsyncSession):
    """Service must NOT commit — caller is responsible per fifo.py convention."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "amount": 1,
                "base": "EUR",
                "date": "2025-03-03",
                "rates": {"USD": 1.0501},
            },
        )

    async with _client_with(handler) as client:
        row = await get_or_fetch_fx_rate(
            session, client, date(2025, 3, 3), "EUR", "USD"
        )

    # Row is in pending state; if we rollback (instead of commit), it disappears
    await session.rollback()
    assert row not in session.new
    result = await session.execute(
        select(FxRate).where(FxRate.date == date(2025, 3, 3))
    )
    assert result.scalar_one_or_none() is None
