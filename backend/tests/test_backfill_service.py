from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import httpx
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core import config as cfg_module
from app.core.database import Base, attach_sqlite_pragmas, get_db
from app.main import app
from app.models import Account, FxRate, Instrument, PriceQuote, Transaction
from app.routers import instruments as instruments_router
from app.services import backfill as backfill_mod
from app.services.backfill import BackfillResult, backfill_fx_history, backfill_instrument_history
from tests.conftest import seed_admin_password


@dataclass(frozen=True)
class _Point:
    date: date
    price: Decimal


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


@pytest_asyncio.fixture
async def api_client():
    original_password = cfg_module.settings.app_password
    cfg_module.settings.app_password = "test-password-123"

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    attach_sqlite_pragmas(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_db():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_db] = override_db
    await seed_admin_password(maker, "test-password-123")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        login = await client.post(
            "/api/auth/login", json={"password": "test-password-123"}
        )
        assert login.status_code == 200
        yield client, maker

    app.dependency_overrides.clear()
    await engine.dispose()
    cfg_module.settings.app_password = original_password


async def _instrument(
    session: AsyncSession,
    *,
    symbol: str = "AAPL",
    price_source: str = "finnhub",
    base_currency: str = "USD",
) -> Instrument:
    instrument = Instrument(
        symbol=symbol,
        name=symbol,
        instrument_type="stock",
        base_currency=base_currency,
        price_source=price_source,
    )
    session.add(instrument)
    await session.flush()
    return instrument


async def test_backfill_is_idempotent(session: AsyncSession, monkeypatch):
    instrument = await _instrument(session)

    async def fake_history(client: httpx.AsyncClient, symbol: str):
        assert symbol == "AAPL"
        return [
            _Point(date(2026, 4, 28), Decimal("190.00")),
            _Point(date(2026, 4, 29), Decimal("191.45")),
        ]

    monkeypatch.setattr(backfill_mod, "fetch_twelve_data_history", fake_history)

    async with httpx.AsyncClient() as client:
        first = await backfill_instrument_history(
            session, client, instrument, date(2026, 4, 28), date(2026, 4, 29)
        )
        await session.flush()
        second = await backfill_instrument_history(
            session, client, instrument, date(2026, 4, 28), date(2026, 4, 29)
        )

    assert first.status == "ok"
    assert first.inserted_prices == 2
    assert first.skipped_existing == 0
    assert second.inserted_prices == 0
    assert second.skipped_existing == 2

    count = await session.scalar(select(func.count()).select_from(PriceQuote))
    assert count == 2
    sources = await session.execute(select(PriceQuote.source).distinct())
    assert set(sources.scalars()) == {"twelve_data"}


async def test_backfill_manual_history_required_skips_prices(
    session: AsyncSession,
):
    instrument = await _instrument(session, price_source="manual")

    async with httpx.AsyncClient() as client:
        result = await backfill_instrument_history(
            session, client, instrument, date(2026, 4, 28), date(2026, 4, 29)
        )

    assert result.status == "manual_history_required"
    assert result.inserted_prices == 0
    assert result.skipped_existing == 0
    assert await session.scalar(select(func.count()).select_from(PriceQuote)) == 0


async def test_backfill_crypto_primary_is_binance(
    session: AsyncSession, monkeypatch
):
    """Binance is the primary crypto-history source. Prices land
    with source="binance" and currency="USD" (USDT pair) regardless of
    the instrument's base_currency — the replay layer converts to EUR
    via FX at chart time."""
    instrument = await _instrument(
        session,
        symbol="BTC",
        price_source="coingecko",
        base_currency="EUR",
    )

    captured: dict[str, str] = {}

    async def fake_binance_history(client: httpx.AsyncClient, symbol_pair: str):
        captured["pair"] = symbol_pair
        return [_Point(date(2026, 4, 29), Decimal("57000.00"))]

    monkeypatch.setattr(backfill_mod, "fetch_binance_history", fake_binance_history)

    async with httpx.AsyncClient() as client:
        result = await backfill_instrument_history(
            session, client, instrument, date(2026, 4, 28), date(2026, 4, 29)
        )

    assert result.inserted_prices == 1
    assert captured["pair"] == "BTCUSDT"
    row = (
        await session.execute(
            select(PriceQuote).where(PriceQuote.instrument_id == instrument.id)
        )
    ).scalar_one()
    assert row.source == "binance"
    assert row.currency == "USD"


async def test_backfill_falls_back_to_coingecko_when_binance_fails(
    session: AsyncSession, monkeypatch
):
    """If Binance can't supply the pair (e.g. a small alt that isn't
    listed), fall back to CoinGecko. CoinGecko fallback honours
    `ticker_override` so the canonical coin id is used."""
    instrument = Instrument(
        symbol="BTC",
        ticker_override="bitcoin",
        name="Bitcoin",
        instrument_type="crypto",
        base_currency="EUR",
        price_source="coingecko",
    )
    session.add(instrument)
    await session.flush()

    captured: dict[str, str] = {}

    async def binance_fails(client: httpx.AsyncClient, symbol_pair: str):
        raise ValueError("binance http 400")

    async def fake_coingecko(
        client: httpx.AsyncClient, coin_id: str, vs_currency: str = "eur"
    ):
        captured["coin_id"] = coin_id
        captured["vs"] = vs_currency
        return [_Point(date(2026, 4, 29), Decimal("57000.00"))]

    monkeypatch.setattr(backfill_mod, "fetch_binance_history", binance_fails)
    monkeypatch.setattr(backfill_mod, "fetch_coingecko_history", fake_coingecko)

    async with httpx.AsyncClient() as client:
        await backfill_instrument_history(
            session, client, instrument, date(2026, 4, 28), date(2026, 4, 29)
        )

    assert captured["coin_id"] == "bitcoin"  # ticker_override resolved
    assert captured["vs"] == "eur"
    row = (
        await session.execute(
            select(PriceQuote).where(PriceQuote.instrument_id == instrument.id)
        )
    ).scalar_one()
    assert row.source == "coingecko"
    assert row.currency == "EUR"


async def test_backfill_fx_history_is_idempotent(
    session: AsyncSession, monkeypatch
):
    async def fake_fx_range(
        client: httpx.AsyncClient,
        start: date,
        end: date,
        base: str = "EUR",
        quote: str = "USD",
    ):
        assert start == date(2026, 4, 28)
        assert end == date(2026, 4, 29)
        assert base == "EUR"
        assert quote == "USD"
        return [
            (date(2026, 4, 28), Decimal("1.13")),
            (date(2026, 4, 29), Decimal("1.14")),
        ]

    monkeypatch.setattr(backfill_mod, "fetch_fx_range", fake_fx_range)

    async with httpx.AsyncClient() as client:
        first = await backfill_fx_history(
            session, client, date(2026, 4, 28), date(2026, 4, 29)
        )
        await session.flush()
        second = await backfill_fx_history(
            session, client, date(2026, 4, 28), date(2026, 4, 29)
        )

    assert first == 2
    assert second == 0
    assert await session.scalar(select(func.count()).select_from(FxRate)) == 2


async def test_backfill_services_do_not_commit(session: AsyncSession, monkeypatch):
    instrument = await _instrument(session)

    async def fake_history(client: httpx.AsyncClient, symbol: str):
        return [_Point(date(2026, 4, 29), Decimal("191.45"))]

    monkeypatch.setattr(backfill_mod, "fetch_twelve_data_history", fake_history)

    async with httpx.AsyncClient() as client:
        await backfill_instrument_history(
            session, client, instrument, date(2026, 4, 29), date(2026, 4, 29)
        )

    await session.rollback()
    assert await session.scalar(select(func.count()).select_from(PriceQuote)) == 0


async def test_backfill_falls_back_to_alpha_vantage_on_non_rate_limit_error(
    session: AsyncSession, monkeypatch
):
    """Twelve Data raising a non-rate-limit ValueError (network glitch,
    unknown symbol, etc.) must still trigger Alpha Vantage fallback."""
    instrument = await _instrument(session)

    async def td_fails(client: httpx.AsyncClient, symbol: str):
        raise ValueError("twelve_data network error: ConnectError")

    async def av_ok(client: httpx.AsyncClient, symbol: str):
        return [_Point(date(2026, 4, 29), Decimal("191.45"))]

    monkeypatch.setattr(backfill_mod, "fetch_twelve_data_history", td_fails)
    monkeypatch.setattr(backfill_mod, "fetch_alpha_vantage_history", av_ok)

    async with httpx.AsyncClient() as client:
        result = await backfill_instrument_history(
            session, client, instrument, date(2026, 4, 29), date(2026, 4, 29)
        )

    assert result.status == "ok"
    assert result.inserted_prices == 1
    sources = await session.execute(select(PriceQuote.source).distinct())
    assert set(sources.scalars()) == {"alpha_vantage"}


async def test_backfill_does_not_fall_back_on_twelve_data_rate_limit(
    session: AsyncSession, monkeypatch
):
    """A Twelve Data rate-limit must NOT burn Alpha Vantage's scarcer
    25/day quota. The 429 propagates so the user retries later."""
    from app.services.pricing.errors import PriceProviderRateLimited

    instrument = await _instrument(session)

    async def td_rate_limited(client: httpx.AsyncClient, symbol: str):
        raise PriceProviderRateLimited("twelve_data rate limited")

    av_called = {"count": 0}

    async def av_should_not_run(client: httpx.AsyncClient, symbol: str):
        av_called["count"] += 1
        return []

    monkeypatch.setattr(backfill_mod, "fetch_twelve_data_history", td_rate_limited)
    monkeypatch.setattr(backfill_mod, "fetch_alpha_vantage_history", av_should_not_run)

    import pytest
    async with httpx.AsyncClient() as client:
        with pytest.raises(PriceProviderRateLimited):
            await backfill_instrument_history(
                session, client, instrument, date(2026, 4, 29), date(2026, 4, 29)
            )
    assert av_called["count"] == 0, "AV must not be called when TD is rate-limited"


async def test_backfill_propagates_error_when_both_providers_fail(
    session: AsyncSession, monkeypatch
):
    """If TD fails for a non-rate-limit reason and AV also fails, the AV
    error propagates so the router can return 429/502 with a useful message."""
    from app.services.pricing.errors import PriceProviderRateLimited

    instrument = await _instrument(session)

    async def td_generic_fail(client: httpx.AsyncClient, symbol: str):
        raise ValueError("twelve_data unknown symbol")

    async def av_rate_limited(client: httpx.AsyncClient, symbol: str):
        raise PriceProviderRateLimited("av rate limited")

    monkeypatch.setattr(backfill_mod, "fetch_twelve_data_history", td_generic_fail)
    monkeypatch.setattr(backfill_mod, "fetch_alpha_vantage_history", av_rate_limited)

    import pytest
    async with httpx.AsyncClient() as client:
        with pytest.raises(PriceProviderRateLimited):
            await backfill_instrument_history(
                session, client, instrument, date(2026, 4, 29), date(2026, 4, 29)
            )


async def test_trigger_instrument_backfill_commits_once(api_client, monkeypatch):
    client, maker = api_client
    async with maker() as session:
        account = Account(name="Revolut", account_type="broker", currency="EUR")
        instrument = Instrument(
            symbol="AAPL",
            name="Apple",
            instrument_type="stock",
            base_currency="USD",
            price_source="finnhub",
        )
        session.add_all([account, instrument])
        await session.flush()
        session.add(
            Transaction(
                account_id=account.id,
                instrument_id=instrument.id,
                txn_type="buy",
                date=date(2026, 4, 28),
                quantity=Decimal("1"),
                unit_price=Decimal("190"),
                price_currency="USD",
                fx_rate_to_eur=Decimal("1.1"),
                cost_basis_eur=Decimal("172.72727273"),
            )
        )
        await session.commit()
        instrument_id = instrument.id

    async def fake_price_backfill(
        session: AsyncSession,
        client: httpx.AsyncClient,
        instrument: Instrument,
        start: date,
        end: date,
    ) -> BackfillResult:
        assert start == date(2026, 4, 28)
        assert end >= start
        session.add(
            PriceQuote(
                instrument_id=instrument.id,
                date=start,
                price=Decimal("191.45"),
                currency=instrument.base_currency,
                source="alpha_vantage",
            )
        )
        return BackfillResult(
            instrument_id=instrument.id,
            status="ok",
            inserted_prices=1,
            skipped_existing=0,
            start=start,
            end=end,
        )

    async def fake_fx_backfill(
        session: AsyncSession,
        client: httpx.AsyncClient,
        start: date,
        end: date,
    ) -> int:
        session.add(
            FxRate(
                date=start,
                base_currency="EUR",
                quote_currency="USD",
                rate=Decimal("1.13"),
                source="frankfurter",
            )
        )
        return 1

    monkeypatch.setattr(
        instruments_router, "backfill_instrument_history", fake_price_backfill
    )
    monkeypatch.setattr(instruments_router, "backfill_fx_history", fake_fx_backfill)

    response = await client.post(f"/api/instruments/{instrument_id}/backfill")

    assert response.status_code == 202
    assert response.json() == {
        "instrument_id": instrument_id,
        "status": "ok",
        "inserted_prices": 1,
        "skipped_existing": 0,
        "inserted_fx_rates": 1,
    }
    async with maker() as session:
        assert await session.scalar(select(func.count()).select_from(PriceQuote)) == 1
        assert await session.scalar(select(func.count()).select_from(FxRate)) == 1


async def test_trigger_instrument_backfill_no_transactions(api_client):
    client, maker = api_client
    async with maker() as session:
        instrument = await _instrument(session)
        await session.commit()
        instrument_id = instrument.id

    response = await client.post(f"/api/instruments/{instrument_id}/backfill")

    assert response.status_code == 202
    assert response.json() == {
        "instrument_id": instrument_id,
        "status": "no_transactions",
        "inserted_prices": 0,
        "skipped_existing": 0,
    }
