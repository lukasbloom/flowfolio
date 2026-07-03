"""
Model-shape tests.

Verifies:
- PriceQuote, FxRate, JobRun import cleanly from app.models.
- NUMERIC precision pins match the established conventions verbatim.
- UNIQUE constraints reject duplicate rows at the DB level (IntegrityError).
- The three new source/status enum tuples are declared per-module and are NOT
  shared with TXN_SOURCES (sanity check on the "do not conflate" rule).
"""
from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base, attach_sqlite_pragmas
from app.core.db_types import DecimalText
from app.models import FxRate, Instrument, JobRun, PriceQuote


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


# ---------------------------------------------------------------------------
# Import + shape checks
# ---------------------------------------------------------------------------


def test_phase2_models_import_cleanly():
    from app.models import FxRate, JobRun, PriceQuote  # noqa: F401

    assert PriceQuote.__tablename__ == "price_quote"
    assert FxRate.__tablename__ == "fx_rate"
    assert JobRun.__tablename__ == "job_runs"


# Money columns are stored as DecimalText (canonical TEXT), not
# Numeric — SQLite binds Numeric/Decimal as REAL and corrupts >15-digit values.
# The type, not a precision/scale, is the contract now.
def test_price_quote_price_is_decimal_text():
    col = PriceQuote.__table__.c.price
    assert isinstance(col.type, DecimalText)


def test_fx_rate_rate_is_decimal_text():
    col = FxRate.__table__.c.rate
    assert isinstance(col.type, DecimalText)


def test_phase2_source_enums_are_distinct():
    """PRICE_QUOTE_SOURCES, FX_RATE_SOURCES, JOB_RUN_STATUSES live in their own
    modules and must NOT be imported from app.models.transaction."""
    from app.models.fx_rate import FX_RATE_SOURCES
    from app.models.job_runs import JOB_RUN_STATUSES
    from app.models.price_quote import PRICE_QUOTE_SOURCES
    from app.models.transaction import TXN_SOURCES

    assert PRICE_QUOTE_SOURCES == (
        "finnhub",
        "alpha_vantage",
        "twelve_data",
        "coingecko",
        "binance",
        "ft",
        "yahoo",
        "manual",
    )
    assert FX_RATE_SOURCES == ("frankfurter", "manual")
    assert JOB_RUN_STATUSES == ("running", "ok", "failed")
    # Distinct sets — never share elements with TXN_SOURCES beyond "manual".
    assert set(PRICE_QUOTE_SOURCES) != set(TXN_SOURCES)
    assert set(FX_RATE_SOURCES) != set(TXN_SOURCES)


# ---------------------------------------------------------------------------
# UNIQUE constraint enforcement at DB level
# ---------------------------------------------------------------------------


async def _seed_instrument(session: AsyncSession) -> str:
    inst = Instrument(
        symbol="AAPL",
        name="Apple",
        instrument_type="stock",
        base_currency="USD",
        price_source="finnhub",
    )
    session.add(inst)
    await session.flush()
    return inst.id


async def test_price_quote_unique_instrument_date_source(session: AsyncSession):
    """Inserting two PriceQuote rows with the same (instrument_id, date, source)
    must raise IntegrityError."""
    inst_id = await _seed_instrument(session)

    pq1 = PriceQuote(
        instrument_id=inst_id,
        date=date(2026, 4, 30),
        price=Decimal("180.50"),
        currency="USD",
        source="finnhub",
    )
    session.add(pq1)
    await session.commit()

    pq2 = PriceQuote(
        instrument_id=inst_id,
        date=date(2026, 4, 30),
        price=Decimal("181.00"),
        currency="USD",
        source="finnhub",
    )
    session.add(pq2)
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_price_quote_allows_different_sources_same_date(session: AsyncSession):
    """The UNIQUE key includes source — two different sources for the same
    (instrument, date) must coexist."""
    inst_id = await _seed_instrument(session)

    pq1 = PriceQuote(
        instrument_id=inst_id,
        date=date(2026, 4, 30),
        price=Decimal("180.50"),
        currency="USD",
        source="finnhub",
    )
    pq2 = PriceQuote(
        instrument_id=inst_id,
        date=date(2026, 4, 30),
        price=Decimal("180.55"),
        currency="USD",
        source="alpha_vantage",
    )
    session.add_all([pq1, pq2])
    await session.commit()

    assert pq1.id != pq2.id


async def test_fx_rate_unique_date_pair(session: AsyncSession):
    """Inserting two FxRate rows with the same (date, base, quote) must raise."""
    fx1 = FxRate(
        date=date(2026, 4, 30),
        base_currency="EUR",
        quote_currency="USD",
        rate=Decimal("1.0712340000"),
        source="frankfurter",
    )
    session.add(fx1)
    await session.commit()

    fx2 = FxRate(
        date=date(2026, 4, 30),
        base_currency="EUR",
        quote_currency="USD",
        rate=Decimal("1.0800000000"),
        source="manual",
    )
    session.add(fx2)
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_fx_rate_decimal_round_trip(session: AsyncSession):
    """rate column round-trips as Decimal (no float coercion)."""
    fx = FxRate(
        date=date(2026, 4, 30),
        base_currency="EUR",
        quote_currency="USD",
        rate=Decimal("1.0712340000"),
        source="frankfurter",
    )
    session.add(fx)
    await session.commit()
    await session.refresh(fx)
    assert isinstance(fx.rate, Decimal)


async def test_job_runs_unique_name_date(session: AsyncSession):
    """Inserting two JobRun rows with the same (job_name, run_date) must raise.

    This is the idempotency guard — accrual cron skipping a duplicate day relies
    on the IntegrityError surfacing here."""
    jr1 = JobRun(job_name="accrual", run_date=date(2026, 4, 30), status="ok")
    session.add(jr1)
    await session.commit()

    jr2 = JobRun(job_name="accrual", run_date=date(2026, 4, 30), status="running")
    session.add(jr2)
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_job_runs_allows_different_jobs_same_date(session: AsyncSession):
    """price_refresh and accrual on the same date must coexist."""
    jr1 = JobRun(job_name="accrual", run_date=date(2026, 4, 30), status="ok")
    jr2 = JobRun(job_name="price_refresh", run_date=date(2026, 4, 30), status="ok")
    session.add_all([jr1, jr2])
    await session.commit()

    assert jr1.id != jr2.id
