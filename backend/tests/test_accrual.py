"""Daily yield accrual service tests."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base, attach_sqlite_pragmas
from app.models import Account, ApyConfig, Instrument, JobRun, PriceQuote, Transaction
from app.services.accrual import (
    ACCRUAL_JOB_NAME,
    accrue_for_date,
    run_accrual_with_backfill,
)
from app.services.pricing.dispatcher import StaleQuoteError


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


async def _seed_pair(session: AsyncSession) -> tuple[Account, Instrument]:
    account = Account(name="Revolut", account_type="broker")
    instrument = Instrument(
        symbol="ETH",
        name="Ethereum",
        instrument_type="crypto",
        base_currency="EUR",
        price_source="coingecko",
    )
    session.add_all([account, instrument])
    await session.flush()
    return account, instrument


async def _seed_buy(
    session: AsyncSession,
    account_id: str,
    instrument_id: str,
    quantity: str = "100",
    on_date: date = date(2025, 1, 1),
) -> Transaction:
    txn = Transaction(
        account_id=account_id,
        instrument_id=instrument_id,
        txn_type="buy",
        date=on_date,
        quantity=Decimal(quantity),
        unit_price=Decimal("3000.00"),
        price_currency="EUR",
        fx_rate_to_eur=Decimal("1"),
        cost_basis_eur=Decimal(quantity) * Decimal("3000.00"),
        source="manual",
    )
    session.add(txn)
    await session.flush()
    return txn


async def _seed_config(
    session: AsyncSession,
    account_id: str,
    instrument_id: str,
    rate: str = "0.023700",
    effective_from: date = date(2025, 1, 1),
    effective_to: date | None = None,
) -> ApyConfig:
    cfg = ApyConfig(
        account_id=account_id,
        instrument_id=instrument_id,
        apy_rate=Decimal(rate),
        effective_from=effective_from,
        effective_to=effective_to,
        compounding="daily_simple",
    )
    session.add(cfg)
    await session.flush()
    return cfg


async def _seed_price(
    session: AsyncSession,
    instrument_id: str,
    on_date: date = date(2025, 1, 15),
    price: str = "3000.00",
    currency: str = "EUR",
) -> PriceQuote:
    quote = PriceQuote(
        instrument_id=instrument_id,
        date=on_date,
        price=Decimal(price),
        currency=currency,
        source="manual",
    )
    session.add(quote)
    await session.flush()
    return quote


async def _yield_txn_count(session: AsyncSession) -> int:
    return await session.scalar(
        select(func.count()).select_from(Transaction).where(Transaction.txn_type == "yield")
    )


async def test_accrue_no_active_configs(session: AsyncSession):
    created = await accrue_for_date(session, date(2025, 1, 15))
    await session.commit()

    assert created == []
    job = (
        await session.execute(
            select(JobRun).where(JobRun.job_name == "accrual", JobRun.run_date == date(2025, 1, 15))
        )
    ).scalar_one()
    assert job.status == "ok"


async def test_accrue_with_one_active_config_creates_one_yield_txn(session: AsyncSession):
    account, instrument = await _seed_pair(session)
    await _seed_buy(session, account.id, instrument.id)
    cfg = await _seed_config(session, account.id, instrument.id)
    await _seed_price(session, instrument.id)
    await session.commit()

    created = await accrue_for_date(session, date(2025, 1, 15))
    await session.commit()

    assert len(created) == 1
    txn = created[0]
    assert txn.quantity == Decimal("0.006493150684931507")
    assert txn.cost_basis_eur == Decimal("19.47945205")
    assert txn.apy_config_id == cfg.id
    assert txn.source == "accrual"


async def test_accrue_idempotent_second_call_returns_empty(session: AsyncSession):
    account, instrument = await _seed_pair(session)
    await _seed_buy(session, account.id, instrument.id)
    await _seed_config(session, account.id, instrument.id)
    await _seed_price(session, instrument.id)
    await session.commit()

    first = await accrue_for_date(session, date(2025, 1, 15))
    await session.commit()
    second = await accrue_for_date(session, date(2025, 1, 15))
    await session.commit()

    assert len(first) == 1
    assert second == []
    assert await _yield_txn_count(session) == 1
    assert await session.scalar(select(func.count()).select_from(JobRun)) == 1


async def test_accrue_zero_balance_position_skipped(session: AsyncSession):
    account, instrument = await _seed_pair(session)
    await _seed_buy(session, account.id, instrument.id)
    session.add(
        Transaction(
            account_id=account.id,
            instrument_id=instrument.id,
            txn_type="sell",
            date=date(2025, 1, 10),
            quantity=Decimal("-100"),
            unit_price=Decimal("3100.00"),
            price_currency="EUR",
            fx_rate_to_eur=Decimal("1"),
            cost_basis_eur=Decimal("310000.00"),
            source="manual",
        )
    )
    await _seed_config(session, account.id, instrument.id)
    await _seed_price(session, instrument.id)
    await session.commit()

    created = await accrue_for_date(session, date(2025, 1, 15))
    await session.commit()

    assert created == []
    assert await _yield_txn_count(session) == 0


async def test_accrue_first_day_after_effective_from(session: AsyncSession):
    account, instrument = await _seed_pair(session)
    await _seed_buy(session, account.id, instrument.id)
    await _seed_config(session, account.id, instrument.id, effective_from=date(2025, 1, 15))
    await _seed_price(session, instrument.id, date(2025, 1, 15))
    await _seed_price(session, instrument.id, date(2025, 1, 16))
    await session.commit()

    same_day = await accrue_for_date(session, date(2025, 1, 15))
    await session.commit()
    next_day = await accrue_for_date(session, date(2025, 1, 16))
    await session.commit()

    assert same_day == []
    assert len(next_day) == 1


async def test_accrue_uses_apy_rate_effective_on_accrual_date(session: AsyncSession):
    account, instrument = await _seed_pair(session)
    await _seed_buy(session, account.id, instrument.id)
    await _seed_config(
        session,
        account.id,
        instrument.id,
        rate="0.020000",
        effective_from=date(2025, 1, 1),
        effective_to=date(2025, 2, 15),
    )
    await _seed_config(
        session,
        account.id,
        instrument.id,
        rate="0.050000",
        effective_from=date(2025, 2, 16),
    )
    await _seed_price(session, instrument.id, date(2025, 2, 10))
    await _seed_price(session, instrument.id, date(2025, 2, 20))
    await session.commit()

    first = await accrue_for_date(session, date(2025, 2, 10))
    await session.commit()
    second = await accrue_for_date(session, date(2025, 2, 20))
    await session.commit()

    # Exact DecimalText storage means the compounded accrual is now the
    # exact Decimal value (last digit was a float-rounding artifact before).
    assert first[0].quantity == Decimal("0.005479452054794521")
    assert second[0].quantity == Decimal("0.013699380746856821")


async def test_accrue_compounds_prior_yields(session: AsyncSession):
    account, instrument = await _seed_pair(session)
    await _seed_buy(session, account.id, instrument.id)
    session.add(
        Transaction(
            account_id=account.id,
            instrument_id=instrument.id,
            txn_type="yield",
            date=date(2025, 1, 14),
            quantity=Decimal("1"),
            unit_price=Decimal("3000.00"),
            price_currency="EUR",
            fx_rate_to_eur=Decimal("1"),
            cost_basis_eur=Decimal("3000.00"),
            source="accrual",
        )
    )
    await _seed_config(session, account.id, instrument.id)
    await _seed_price(session, instrument.id)
    await session.commit()

    created = await accrue_for_date(session, date(2025, 1, 15))
    await session.commit()

    assert created[0].quantity == Decimal("0.006558082191780822")


async def test_accrue_yield_txn_has_cost_basis_locked(session: AsyncSession):
    account, instrument = await _seed_pair(session)
    await _seed_buy(session, account.id, instrument.id)
    await _seed_config(session, account.id, instrument.id)
    await _seed_price(session, instrument.id, price="3000.00")
    await session.commit()

    created = await accrue_for_date(session, date(2025, 1, 15))
    await session.commit()

    txn = created[0]
    expected = (txn.quantity * Decimal("3000.00") / Decimal("1")).quantize(
        Decimal("0.00000001")
    )
    assert txn.cost_basis_eur == expected


async def test_accrue_no_price_quote_raises_stale(session: AsyncSession):
    account, instrument = await _seed_pair(session)
    await _seed_buy(session, account.id, instrument.id)
    await _seed_config(session, account.id, instrument.id)
    await session.commit()

    with pytest.raises(StaleQuoteError):
        await accrue_for_date(session, date(2025, 1, 15))

    job = (
        await session.execute(
            select(JobRun).where(JobRun.job_name == "accrual", JobRun.run_date == date(2025, 1, 15))
        )
    ).scalar_one()
    assert job.status == "failed"


async def test_run_accrual_with_backfill_walks_90_days(session: AsyncSession):
    account, instrument = await _seed_pair(session)
    await _seed_buy(session, account.id, instrument.id, on_date=date(2024, 12, 1))
    await _seed_config(session, account.id, instrument.id, effective_from=date(2024, 12, 1))
    for offset in range(91):
        await _seed_price(session, instrument.id, date(2025, 3, 31) - timedelta(days=offset))
    session.add(JobRun(job_name="accrual", run_date=date(2025, 3, 15), status="ok"))
    await session.commit()

    summary = await run_accrual_with_backfill(session, today=date(2025, 3, 31))

    assert len(summary) == 91
    assert min(summary) == date(2024, 12, 31)
    assert summary[date(2025, 3, 15)] == 0


async def test_run_accrual_with_backfill_continues_after_per_day_failure(
    session: AsyncSession,
):
    account, instrument = await _seed_pair(session)
    await _seed_buy(session, account.id, instrument.id, on_date=date(2025, 1, 1))
    await _seed_config(session, account.id, instrument.id, effective_from=date(2025, 1, 1))
    await _seed_price(session, instrument.id, date(2025, 1, 3))
    await session.commit()

    summary = await run_accrual_with_backfill(
        session, today=date(2025, 1, 3), backfill_days=1
    )

    failed = (
        await session.execute(
            select(JobRun).where(JobRun.job_name == "accrual", JobRun.run_date == date(2025, 1, 2))
        )
    ).scalar_one()
    assert failed.status == "failed"
    assert summary[date(2025, 1, 2)] == 0
    assert summary[date(2025, 1, 3)] == 1


async def test_mark_failed_job_run_race_resolves_to_single_failed_row(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    """Prove (don't assume) the _mark_failed_job_run race is
    closed. A concurrent worker can insert the JobRun for this accrual date in
    the window between accrue_for_date's rollback and our failure-marker insert.
    When that happens _mark_failed_job_run's INSERT hits the
    UniqueConstraint(job_name, run_date) and raises IntegrityError; the fallback
    must rollback, UPDATE the existing row to 'failed', and NOT propagate the
    IntegrityError (it re-raises the ORIGINAL accrual error so the caller still
    learns the accrual failed). The end state must be exactly ONE JobRun row,
    status='failed', with notes + completed_at populated.

    The race is made deterministic by wrapping the module's
    _mark_failed_job_run: the wrapper simulates the concurrent worker by
    committing a competing JobRun for the same (job_name, run_date) *before*
    delegating to the real implementation, so the real INSERT collides exactly
    as it would under a true two-worker race.
    """
    account, instrument = await _seed_pair(session)
    await _seed_buy(session, account.id, instrument.id)
    await _seed_config(session, account.id, instrument.id)
    # No price quote seeded → the accrual body raises StaleQuoteError, driving
    # the except-Exception failure path that calls _mark_failed_job_run.
    await session.commit()

    accrual_date = date(2025, 1, 15)

    import app.services.accrual as accrual_mod

    real_mark_failed = accrual_mod._mark_failed_job_run
    competing_inserted = {"done": False}

    async def racing_mark_failed(
        sess: AsyncSession, run_date: date, error: Exception
    ) -> None:
        # Simulate the concurrent worker exactly once: it inserts + commits the
        # JobRun for the same (job_name, run_date) right before our marker insert.
        if not competing_inserted["done"]:
            competing_inserted["done"] = True
            sess.add(
                JobRun(
                    job_name=ACCRUAL_JOB_NAME,
                    run_date=run_date,
                    status="running",
                )
            )
            await sess.commit()
        # Now delegate to the real implementation; its INSERT will collide with
        # the row the "concurrent worker" just committed → IntegrityError →
        # accrue_for_date's except IntegrityError fallback UPDATEs to failed.
        await real_mark_failed(sess, run_date, error)

    monkeypatch.setattr(accrual_mod, "_mark_failed_job_run", racing_mark_failed)

    # The original accrual error (StaleQuoteError) must still propagate; the
    # IntegrityError from the marker collision must NOT leak out.
    with pytest.raises(StaleQuoteError):
        await accrue_for_date(session, accrual_date)

    assert competing_inserted["done"] is True

    # Exactly one JobRun row for (job_name, run_date), and it is 'failed'.
    count = await session.scalar(
        select(func.count())
        .select_from(JobRun)
        .where(JobRun.job_name == ACCRUAL_JOB_NAME, JobRun.run_date == accrual_date)
    )
    assert count == 1

    row = (
        await session.execute(
            select(JobRun).where(
                JobRun.job_name == ACCRUAL_JOB_NAME, JobRun.run_date == accrual_date
            )
        )
    ).scalar_one()
    assert row.status == "failed"
    assert row.completed_at is not None
    assert row.notes and "StaleQuoteError" in row.notes


async def test_accrue_skips_non_yield_eligible_instrument_type(session: AsyncSession):
    """Backend defensive gate: stocks/etfs/funds/metals must not accrue
    yield even if an ApyConfig points at them (frontend tab gating prevents
    new bad configs, but historical/direct-API configs should be ignored)."""
    account = Account(name="Revolut", account_type="broker")
    instrument = Instrument(
        symbol="AAPL",
        name="Apple Inc.",
        instrument_type="stock",  # NOT yield-eligible
        base_currency="USD",
        price_source="finnhub",
    )
    session.add_all([account, instrument])
    await session.flush()
    await _seed_buy(session, account.id, instrument.id)
    await _seed_config(session, account.id, instrument.id)
    await _seed_price(session, instrument.id)
    await session.commit()

    created = await accrue_for_date(session, date(2025, 1, 15))
    await session.commit()

    assert created == []
    assert await _yield_txn_count(session) == 0
