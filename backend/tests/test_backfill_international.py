"""Regression test pinning the fix for the international
ticker backfill gap.

Empirical findings (probed 2026-05-26 against live Twelve Data + Finnhub
free tiers):
    - ASML.AS / SAP.DE: Twelve Data rejects the `.AS` / `.DE` suffix
      ("api error"); the exchange-qualified XETR/AMS form requires a paid
      Grow/Venture plan; the bare ticker (`ASML`, `SAP`) resolves to the
      US ADR in USD — wrong currency and price for the EU-listed original.
    - VWCE / SXR8: not covered at all on Twelve Data free tier (any
      symbol form returns the same paid-plan gate); Finnhub returns 0.
    - TSLA: Twelve Data returns 4001 daily rows cleanly — current code
      is correct for US-listed names; sparse prod data is most likely a
      one-off 8/min rate-limit casualty that retrying resolves.

Track shipped (D, composite):
    1. WARN log on `no_history_available` carries enough context
       (provider_symbol, price_source, window, rows_received) to debug
       a sparse backfill without grepping API logs.
    2. Dispatcher comment documents the price_source="finnhub"
       enum-label / Twelve-Data-implementation asymmetry on the
       backfill path.
    3. Fixture (`scripts/fixtures/golden_portfolio.py`) reclassifies
       ASML.AS / SAP.DE / VWCE / SXR8 from price_source="finnhub" to
       "manual" — honest configuration that stops bulk backfill from
       trying-and-failing them.

This test asserts behaviors (1) and (3) — the dispatcher comment is a
docs-only change with no observable behavior to pin.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base, attach_sqlite_pragmas
from app.models import Instrument, PriceQuote
from app.services import backfill as backfill_mod
from app.services.backfill import backfill_instrument_history


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


async def _eu_instrument(
    session: AsyncSession,
    *,
    symbol: str,
    instrument_type: str = "stock",
) -> Instrument:
    # Pre-fix configuration: EU instrument with price_source="finnhub"
    # (i.e., what bulk backfill would see if the fixture migration to
    # "manual" hadn't shipped yet, or what a user would create through
    # the UI for an EU ticker).
    instrument = Instrument(
        symbol=symbol,
        name=symbol,
        instrument_type=instrument_type,
        base_currency="EUR",
        price_source="finnhub",
    )
    session.add(instrument)
    await session.flush()
    return instrument


# ---------------------------------------------------------------------------
# Behavior 1: WARN log on no_history_available carries provider_symbol +
# price_source + window so the user can debug a sparse bulk-backfill row
# without grepping API call logs.
# ---------------------------------------------------------------------------


async def test_no_history_available_logs_warning_with_provider_context(
    session: AsyncSession, monkeypatch, caplog: pytest.LogCaptureFixture
):
    """When both Twelve Data and Alpha Vantage return empty/no usable
    rows, `backfill_instrument_history` returns `no_history_available`
    cleanly AND emits a WARN with enough context to debug.

    This is the exact path triggered for VWCE/SXR8 on free tier (TD
    rejects, AV rate-limited or empty) before the fixture-reclassify fix
    landed. The WARN message is what now tells the user "go set
    price_source=manual or supply a ticker_override".
    """
    instrument = await _eu_instrument(session, symbol="VWCE", instrument_type="etf")

    async def td_empty(client: httpx.AsyncClient, symbol: str):
        # Twelve Data raises ValueError on empty `values` — kicks off
        # the AV fallback.
        raise ValueError(f"twelve_data missing values for {symbol}")

    async def av_empty(client: httpx.AsyncClient, symbol: str):
        # AV returns zero usable rows (e.g., out-of-window history).
        return []

    monkeypatch.setattr(backfill_mod, "fetch_twelve_data_history", td_empty)
    monkeypatch.setattr(backfill_mod, "fetch_alpha_vantage_history", av_empty)

    caplog.set_level(logging.WARNING, logger="app.services.backfill")
    async with httpx.AsyncClient() as client:
        result = await backfill_instrument_history(
            session, client, instrument, date(2026, 1, 1), date(2026, 4, 30)
        )

    assert result.status == "no_history_available"
    assert result.inserted_prices == 0
    assert await session.scalar(select(func.count()).select_from(PriceQuote)) == 0

    # The WARN must surface enough context for the user to act.
    no_history_records = [
        r for r in caplog.records if r.message == "backfill_no_history_available"
    ]
    assert len(no_history_records) == 1, (
        f"expected exactly one no-history WARN, got {len(no_history_records)}: "
        f"{[r.message for r in caplog.records]}"
    )
    rec = no_history_records[0]
    assert rec.levelno == logging.WARNING
    assert rec.symbol == "VWCE"
    assert rec.provider_symbol == "VWCE"  # no ticker_override on this fixture row
    assert rec.price_source == "finnhub"
    assert rec.start == "2026-01-01"
    assert rec.end == "2026-04-30"
    assert rec.rows_received == 0


async def test_no_history_available_warn_includes_ticker_override(
    session: AsyncSession, monkeypatch, caplog: pytest.LogCaptureFixture
):
    """If the user supplies a `ticker_override` to work around an EU
    coverage gap (e.g., ASML.AS -> ASML for the US ADR), the WARN must
    reflect the override they actually configured — not the raw fixture
    symbol — so they can verify the override reached the provider call.
    """
    instrument = Instrument(
        symbol="ASML.AS",
        ticker_override="ASML",
        name="ASML Holding N.V.",
        instrument_type="stock",
        base_currency="EUR",
        price_source="finnhub",
    )
    session.add(instrument)
    await session.flush()

    async def td_empty(client: httpx.AsyncClient, symbol: str):
        # Verify the override actually flowed to the provider call.
        assert symbol == "ASML"
        raise ValueError(f"twelve_data missing values for {symbol}")

    async def av_empty(client: httpx.AsyncClient, symbol: str):
        assert symbol == "ASML"
        return []

    monkeypatch.setattr(backfill_mod, "fetch_twelve_data_history", td_empty)
    monkeypatch.setattr(backfill_mod, "fetch_alpha_vantage_history", av_empty)

    caplog.set_level(logging.WARNING, logger="app.services.backfill")
    async with httpx.AsyncClient() as client:
        result = await backfill_instrument_history(
            session, client, instrument, date(2026, 4, 1), date(2026, 4, 30)
        )

    assert result.status == "no_history_available"
    rec = next(
        r for r in caplog.records if r.message == "backfill_no_history_available"
    )
    assert rec.symbol == "ASML.AS"
    assert rec.provider_symbol == "ASML"


# ---------------------------------------------------------------------------
# Behavior 2: Fixture reclassify — ASML.AS, SAP.DE, VWCE, SXR8 are now
# price_source="manual", so `backfill_instrument_history` short-circuits
# to `manual_history_required` and never burns a provider call on them.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "symbol,instrument_type",
    [
        ("ASML.AS", "stock"),
        ("SAP.DE", "stock"),
        ("VWCE", "etf"),
        ("SXR8", "etf"),
    ],
)
async def test_eu_fixture_instruments_short_circuit_to_manual(
    session: AsyncSession, symbol: str, instrument_type: str
):
    """With the fixture reclassify, these four EU instruments never
    call Twelve Data or Alpha Vantage — they return
    `manual_history_required` immediately, the same path that MSCI-W
    and MM-EUR already use. No provider quota burned for a known-empty
    response.
    """
    instrument = Instrument(
        symbol=symbol,
        name=symbol,
        instrument_type=instrument_type,
        base_currency="EUR",
        price_source="manual",  # post-fix fixture configuration
    )
    session.add(instrument)
    await session.flush()

    async with httpx.AsyncClient() as client:
        result = await backfill_instrument_history(
            session, client, instrument, date(2026, 1, 1), date(2026, 4, 30)
        )

    assert result.status == "manual_history_required"
    assert result.inserted_prices == 0
    assert result.skipped_existing == 0


# ---------------------------------------------------------------------------
# Behavior 3: TSLA (and other US-listed price_source="finnhub" tickers)
# continue to backfill correctly through the Twelve Data primary path.
# This pins the regression test against any future symbol-mapping change
# that might accidentally re-route US tickers through a different code
# path.
# ---------------------------------------------------------------------------


async def test_us_listed_finnhub_instrument_uses_twelve_data(
    session: AsyncSession, monkeypatch
):
    """TSLA / AAPL / MSFT — the symbol passes through verbatim to Twelve
    Data and rows insert with source='twelve_data'. The empirical probe
    confirmed TD returns 4001 daily rows for TSLA.
    """
    instrument = Instrument(
        symbol="TSLA",
        name="Tesla, Inc.",
        instrument_type="stock",
        base_currency="USD",
        price_source="finnhub",
    )
    session.add(instrument)
    await session.flush()

    captured: dict[str, str] = {}

    async def fake_td(client: httpx.AsyncClient, symbol: str):
        captured["symbol"] = symbol
        return [
            _Point(date(2026, 4, 28), Decimal("280.50")),
            _Point(date(2026, 4, 29), Decimal("285.10")),
        ]

    monkeypatch.setattr(backfill_mod, "fetch_twelve_data_history", fake_td)

    async with httpx.AsyncClient() as client:
        result = await backfill_instrument_history(
            session, client, instrument, date(2026, 4, 28), date(2026, 4, 29)
        )

    assert result.status == "ok"
    assert result.inserted_prices == 2
    assert captured["symbol"] == "TSLA"  # raw symbol, no mapping shim
    row = (
        await session.execute(
            select(PriceQuote).where(PriceQuote.instrument_id == instrument.id)
        )
    ).first()
    assert row is not None
    assert row[0].source == "twelve_data"
    assert row[0].currency == "USD"
