"""APScheduler wiring tests."""
from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base, attach_sqlite_pragmas
from app.models import Instrument
from app.models.fx_rate import FxRate
from app.models.job_runs import JobRun
from app.services import scheduler as scheduler_mod
from app.services.pricing.dispatcher import StaleQuoteError


@pytest_asyncio.fixture
async def maker():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    attach_sqlite_pragmas(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield session_factory
    await engine.dispose()


def _app():
    return SimpleNamespace(state=SimpleNamespace())


def _shutdown(app) -> None:
    scheduler_mod.shutdown_scheduler(app)


async def test_start_scheduler_registers_fx_refresh_job():
    app = _app()
    scheduler_mod.start_scheduler(app)
    try:
        ids = {job.id for job in app.state.scheduler.get_jobs()}
        assert ids == {
            "price_refresh",
            "accrual",
            "fx_refresh",
            "backup",
            "version_check",
        }
    finally:
        _shutdown(app)


async def test_demo_mode_registers_only_reset_job(monkeypatch):
    """With demo_mode on, start_scheduler registers ONLY the demo_reset job.

    The five production jobs (price_refresh / fx_refresh / accrual / backup /
    version_check) must be absent so no live fetch or frozen-seed mutation runs.
    """
    monkeypatch.setattr(scheduler_mod.settings, "demo_mode", True)
    monkeypatch.setattr(scheduler_mod.settings, "demo_reset_interval_hours", 6)
    app = _app()
    scheduler_mod.start_scheduler(app)
    try:
        ids = {job.id for job in app.state.scheduler.get_jobs()}
        assert ids == {"demo_reset"}
    finally:
        _shutdown(app)


async def test_start_scheduler_cron_times_match_decisions():
    app = _app()
    scheduler_mod.start_scheduler(app)
    try:
        jobs = {job.id: job for job in app.state.scheduler.get_jobs()}
        assert str(jobs["price_refresh"].trigger) == "cron[hour='22', minute='0']"
        assert str(jobs["accrual"].trigger) == "cron[hour='0', minute='30']"
    finally:
        _shutdown(app)


async def test_fx_refresh_cron_at_17_utc():
    app = _app()
    scheduler_mod.start_scheduler(app)
    try:
        jobs = {job.id: job for job in app.state.scheduler.get_jobs()}
        assert str(jobs["fx_refresh"].trigger) == "cron[hour='17', minute='0']"
    finally:
        _shutdown(app)


async def test_shutdown_scheduler_calls_shutdown():
    app = _app()
    scheduler_mod.start_scheduler(app)
    scheduler = app.state.scheduler

    scheduler_mod.shutdown_scheduler(app)
    await asyncio.sleep(0)

    assert scheduler.running is False


def test_multiworker_guard_raises(monkeypatch):
    """WEB_CONCURRENCY != 1 must refuse to start the scheduler (double-fire risk)."""
    app = _app()
    monkeypatch.setenv("WEB_CONCURRENCY", "2")
    with pytest.raises(RuntimeError, match="WEB_CONCURRENCY=1"):
        scheduler_mod.start_scheduler(app)
    # No scheduler should have been attached when the guard trips.
    assert not hasattr(app.state, "scheduler")


async def test_price_refresh_handler_iterates_active_instruments(maker, monkeypatch):
    async with maker() as session:
        session.add_all(
            [
                Instrument(
                    symbol="AAPL",
                    name="Apple",
                    instrument_type="stock",
                    base_currency="USD",
                    price_source="finnhub",
                ),
                Instrument(
                    symbol="BTC",
                    name="Bitcoin",
                    instrument_type="crypto",
                    base_currency="EUR",
                    price_source="coingecko",
                ),
                Instrument(
                    symbol="MANUAL",
                    name="Manual Fund",
                    instrument_type="fund",
                    base_currency="EUR",
                    price_source="manual",
                ),
            ]
        )
        await session.commit()

    calls: list[str] = []

    async def fake_fetch_price(session, client, inst, today):
        calls.append(inst.symbol)
        if inst.symbol == "BTC":
            raise StaleQuoteError("stale")
        return SimpleNamespace(source=inst.price_source, price=Decimal("1"))

    monkeypatch.setattr(scheduler_mod, "async_session_factory", maker)
    monkeypatch.setattr(scheduler_mod, "fetch_price", fake_fetch_price)

    summary = await scheduler_mod.price_refresh_job_handler()

    assert summary == {"ok": 1, "stale": 1, "skipped": 0}
    assert calls == ["AAPL", "BTC"]


# -------------------------------------------------------------------------
# price_refresh job_runs instrumentation tests
# -------------------------------------------------------------------------


async def test_price_refresh_handler_writes_job_run_ok(maker, monkeypatch):
    """Successful run writes a job_runs row with status=ok."""
    today = date(2026, 5, 8)
    async with maker() as session:
        session.add_all(
            [
                Instrument(
                    symbol="AAPL",
                    name="Apple",
                    instrument_type="stock",
                    base_currency="USD",
                    price_source="finnhub",
                ),
                Instrument(
                    symbol="BTC",
                    name="Bitcoin",
                    instrument_type="crypto",
                    base_currency="EUR",
                    price_source="coingecko",
                ),
            ]
        )
        await session.commit()

    async def fake_fetch_price(session, client, inst, today):
        return SimpleNamespace(source=inst.price_source, price=Decimal("1"))

    monkeypatch.setattr(scheduler_mod, "async_session_factory", maker)
    monkeypatch.setattr(scheduler_mod, "fetch_price", fake_fetch_price)

    summary = await scheduler_mod.price_refresh_job_handler(today=today)

    assert summary == {"ok": 2, "stale": 0, "skipped": 0}

    async with maker() as session:
        job_rows = (
            await session.execute(
                select(JobRun).where(JobRun.job_name == "price_refresh")
            )
        ).scalars().all()
        assert len(job_rows) == 1
        assert job_rows[0].run_date == today
        assert job_rows[0].status == "ok"
        assert job_rows[0].notes is not None
        assert "ok=2" in job_rows[0].notes
        assert "stale=0" in job_rows[0].notes
        assert "skipped=0" in job_rows[0].notes


async def test_price_refresh_handler_idempotent_same_day(maker, monkeypatch):
    """Existing status=ok row → short-circuits, no fetch_price call."""
    today = date(2026, 5, 8)

    async with maker() as session:
        session.add(
            Instrument(
                symbol="AAPL",
                name="Apple",
                instrument_type="stock",
                base_currency="USD",
                price_source="finnhub",
            )
        )
        session.add(
            JobRun(
                job_name="price_refresh",
                run_date=today,
                status="ok",
                notes="prior run",
            )
        )
        await session.commit()

    call_count = {"n": 0}

    async def fake_fetch_price(session, client, inst, today):
        call_count["n"] += 1
        return SimpleNamespace(source=inst.price_source, price=Decimal("1"))

    monkeypatch.setattr(scheduler_mod, "async_session_factory", maker)
    monkeypatch.setattr(scheduler_mod, "fetch_price", fake_fetch_price)

    result = await scheduler_mod.price_refresh_job_handler(today=today)

    assert result == {"skipped_already_ran": 1}
    assert call_count["n"] == 0

    async with maker() as session:
        job_rows = (
            await session.execute(
                select(JobRun).where(JobRun.job_name == "price_refresh")
            )
        ).scalars().all()
        assert len(job_rows) == 1
        assert job_rows[0].notes == "prior run"


async def test_price_refresh_handler_marks_failed_on_outer_block_error(
    maker, monkeypatch
):
    """Outer-block exception → re-raise + status=failed marker row."""
    today = date(2026, 5, 8)

    async with maker() as session:
        session.add(
            Instrument(
                symbol="AAPL",
                name="Apple",
                instrument_type="stock",
                base_currency="USD",
                price_source="finnhub",
            )
        )
        await session.commit()

    # Force the outer-block work to fail — raise inside fetch_price's call site
    # by patching session.execute to blow up on the Instrument SELECT.
    from sqlalchemy.ext.asyncio import AsyncSession as RealAsyncSession

    original_execute = RealAsyncSession.execute
    raised = {"n": 0}

    async def fake_execute(self, statement, *args, **kwargs):
        # Only the price_refresh SELECT(Instrument) should raise.
        compiled = str(statement)
        if "FROM instrument" in compiled and raised["n"] == 0:
            raised["n"] += 1
            raise RuntimeError("DB exploded")
        return await original_execute(self, statement, *args, **kwargs)

    monkeypatch.setattr(scheduler_mod, "async_session_factory", maker)
    monkeypatch.setattr(RealAsyncSession, "execute", fake_execute)

    with pytest.raises(RuntimeError, match="DB exploded"):
        await scheduler_mod.price_refresh_job_handler(today=today)

    async with maker() as session:
        job_rows = (
            await session.execute(
                select(JobRun).where(JobRun.job_name == "price_refresh")
            )
        ).scalars().all()
        assert len(job_rows) == 1
        assert job_rows[0].run_date == today
        assert job_rows[0].status == "failed"
        assert "RuntimeError" in (job_rows[0].notes or "")
        assert "DB exploded" in (job_rows[0].notes or "")


async def test_accrual_handler_calls_backfill_with_90_days(maker, monkeypatch):
    captured = {}

    async def fake_backfill(session, today, backfill_days):
        captured["today"] = today
        captured["backfill_days"] = backfill_days
        return {date(2025, 1, 1): 2}

    monkeypatch.setattr(scheduler_mod, "async_session_factory", maker)
    monkeypatch.setattr(scheduler_mod, "run_accrual_with_backfill", fake_backfill)

    result = await scheduler_mod.accrual_job_handler()

    assert captured["backfill_days"] == 90
    assert result == {"days": 1, "txns": 2}


def test_app_lifespan_starts_and_stops_scheduler():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()

    @app.get("/health")
    async def health():
        return {"ok": True}

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(app):
        scheduler_mod.start_scheduler(app)
        try:
            yield
        finally:
            scheduler_mod.shutdown_scheduler(app)

    app.router.lifespan_context = lifespan
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert app.state.scheduler.running is True
    assert app.state.scheduler.state == 0


# -------------------------------------------------------------------------
# fx_refresh handler + job_runs observability tests
# -------------------------------------------------------------------------


def _seed_fx_rate(session, *, on_date, rate=Decimal("1.08"), source="frankfurter"):
    session.add(
        FxRate(
            date=on_date,
            base_currency="EUR",
            quote_currency="USD",
            rate=rate,
            source=source,
        )
    )


async def test_fx_refresh_handler_fills_gap_writes_job_run(maker, monkeypatch):
    """Gap-fill from last cached date → yesterday writes rows + job_runs."""
    today = date(2026, 5, 8)
    target = date(2026, 5, 7)  # today - 1
    last_cached = date(2026, 4, 25)

    async with maker() as session:
        _seed_fx_rate(session, on_date=last_cached)
        await session.commit()

    # Frankfurter only returns business days; mock a plausible subset.
    fake_rates = [
        (date(2026, 4, 27), Decimal("1.0801")),
        (date(2026, 4, 28), Decimal("1.0802")),
        (date(2026, 4, 29), Decimal("1.0803")),
        (date(2026, 4, 30), Decimal("1.0804")),
        (date(2026, 5, 1), Decimal("1.0805")),
        (date(2026, 5, 4), Decimal("1.0806")),
        (date(2026, 5, 5), Decimal("1.0807")),
        (date(2026, 5, 6), Decimal("1.0808")),
        (date(2026, 5, 7), Decimal("1.0809")),
    ]
    captured = {}

    async def fake_fetch_fx_range(client, start, end, base="EUR", quote="USD"):
        captured["start"] = start
        captured["end"] = end
        captured["base"] = base
        captured["quote"] = quote
        return list(fake_rates)

    monkeypatch.setattr(scheduler_mod, "async_session_factory", maker)
    monkeypatch.setattr(scheduler_mod, "fetch_fx_range", fake_fetch_fx_range)

    result = await scheduler_mod.fx_refresh_job_handler(today=today)

    assert result == {"fetched": len(fake_rates)}
    # Frankfurter call used POSITIONAL start (last_cached + 1) and end=target.
    from datetime import timedelta as _td
    assert captured["start"] == last_cached + _td(days=1)
    assert captured["end"] == target

    async with maker() as session:
        rows = (
            await session.execute(
                select(FxRate).where(
                    FxRate.base_currency == "EUR", FxRate.quote_currency == "USD"
                )
            )
        ).scalars().all()
        # Original seed + all 9 returned rows.
        assert len(rows) == 1 + len(fake_rates)
        sources = {r.source for r in rows}
        assert "frankfurter" in sources

        job_rows = (
            await session.execute(
                select(JobRun).where(JobRun.job_name == "fx_refresh")
            )
        ).scalars().all()
        assert len(job_rows) == 1
        assert job_rows[0].run_date == today
        assert job_rows[0].status == "ok"
        assert job_rows[0].notes is not None
        assert str(len(fake_rates)) in job_rows[0].notes


async def test_fx_refresh_handler_idempotent_same_day(maker, monkeypatch):
    """Second invocation same day is a no-op (no HTTP, no extra rows)."""
    today = date(2026, 5, 8)

    async with maker() as session:
        _seed_fx_rate(session, on_date=date(2026, 5, 6))
        await session.commit()

    call_count = {"n": 0}

    async def fake_fetch_fx_range(client, start, end, base="EUR", quote="USD"):
        call_count["n"] += 1
        return [(date(2026, 5, 7), Decimal("1.09"))]

    monkeypatch.setattr(scheduler_mod, "async_session_factory", maker)
    monkeypatch.setattr(scheduler_mod, "fetch_fx_range", fake_fetch_fx_range)

    first = await scheduler_mod.fx_refresh_job_handler(today=today)
    second = await scheduler_mod.fx_refresh_job_handler(today=today)

    assert first == {"fetched": 1}
    assert second == {"skipped": 1}
    assert call_count["n"] == 1

    async with maker() as session:
        job_rows = (
            await session.execute(
                select(JobRun).where(JobRun.job_name == "fx_refresh")
            )
        ).scalars().all()
        assert len(job_rows) == 1


async def test_fx_refresh_handler_no_gap_no_http_call(maker, monkeypatch):
    """Yesterday already cached → short-circuit, no HTTP, status=ok."""
    today = date(2026, 5, 8)
    target = date(2026, 5, 7)

    async with maker() as session:
        _seed_fx_rate(session, on_date=target)
        await session.commit()

    async def fake_fetch_fx_range(client, start, end, base="EUR", quote="USD"):
        raise AssertionError("fetch_fx_range must not be called when there is no gap")

    monkeypatch.setattr(scheduler_mod, "async_session_factory", maker)
    monkeypatch.setattr(scheduler_mod, "fetch_fx_range", fake_fetch_fx_range)

    result = await scheduler_mod.fx_refresh_job_handler(today=today)

    assert result == {"fetched": 0, "skipped_no_gap": 1}

    async with maker() as session:
        job_rows = (
            await session.execute(
                select(JobRun).where(JobRun.job_name == "fx_refresh")
            )
        ).scalars().all()
        assert len(job_rows) == 1
        assert job_rows[0].status == "ok"
        assert "no gap" in (job_rows[0].notes or "")


async def test_fx_refresh_handler_marks_failed_on_upstream_error(maker, monkeypatch):
    """Upstream error → re-raise + job_runs status=failed + no fx_rate inserts."""
    today = date(2026, 5, 8)

    async with maker() as session:
        _seed_fx_rate(session, on_date=date(2026, 4, 25))
        await session.commit()

    async def fake_fetch_fx_range(client, start, end, base="EUR", quote="USD"):
        raise ValueError("frankfurter rate limited")

    monkeypatch.setattr(scheduler_mod, "async_session_factory", maker)
    monkeypatch.setattr(scheduler_mod, "fetch_fx_range", fake_fetch_fx_range)

    with pytest.raises(ValueError, match="rate limited"):
        await scheduler_mod.fx_refresh_job_handler(today=today)

    async with maker() as session:
        rows = (
            await session.execute(
                select(FxRate).where(
                    FxRate.base_currency == "EUR", FxRate.quote_currency == "USD"
                )
            )
        ).scalars().all()
        # Only the original seed row — nothing else committed.
        assert len(rows) == 1

        job_rows = (
            await session.execute(
                select(JobRun).where(JobRun.job_name == "fx_refresh")
            )
        ).scalars().all()
        assert len(job_rows) == 1
        assert job_rows[0].status == "failed"
        assert "rate limited" in (job_rows[0].notes or "")


async def test_fx_refresh_recovers_from_failed_jobrun_without_losing_fxrates(
    maker, monkeypatch
):
    """Regression test for cross-pending-state rollback bug (1ff62d9).

    Pre-seeds an FxRate row + a status='failed' JobRun row for today, runs
    fx_refresh, asserts the new fx_rate inserts SURVIVE and the JobRun row
    UPSERTs from failed → ok. Defends 1ff62d9: committing fx_rate inserts
    BEFORE record_job_run so its UPSERT-path internal rollback can't discard
    them. Pre-1ff62d9 (commit position swapped), assertion (b) fails because
    the fx_rate inserts get rolled back when record_job_run hits the UPSERT
    branch.
    """
    today = date(2026, 5, 8)
    last_cached = date(2026, 5, 3)

    async with maker() as session:
        _seed_fx_rate(session, on_date=last_cached)
        session.add(
            JobRun(
                job_name="fx_refresh",
                run_date=today,
                status="failed",
                notes="prior attempt",
            )
        )
        await session.commit()

    fake_rates = [
        (date(2026, 5, 4), Decimal("1.0901")),
        (date(2026, 5, 5), Decimal("1.0902")),
        (date(2026, 5, 6), Decimal("1.0903")),
        (date(2026, 5, 7), Decimal("1.0904")),
    ]

    async def fake_fetch_fx_range(client, start, end, base="EUR", quote="USD"):
        return list(fake_rates)

    monkeypatch.setattr(scheduler_mod, "async_session_factory", maker)
    monkeypatch.setattr(scheduler_mod, "fetch_fx_range", fake_fetch_fx_range)

    # (a) Handler returns the fetched count.
    result = await scheduler_mod.fx_refresh_job_handler(today=today)
    assert result == {"fetched": len(fake_rates)}

    async with maker() as session:
        # (b) New fx_rate rows survived the record_job_run UPSERT-path rollback.
        new_rows = (
            await session.execute(
                select(FxRate).where(
                    FxRate.base_currency == "EUR",
                    FxRate.quote_currency == "USD",
                    FxRate.date >= date(2026, 5, 4),
                    FxRate.date <= date(2026, 5, 7),
                )
            )
        ).scalars().all()
        assert len(new_rows) == 4, (
            "fx_rate inserts must survive the UPSERT-path rollback inside "
            "record_job_run — 1ff62d9 commits them BEFORE writing the JobRun "
            "row to keep them outside that pending-state."
        )

        # (c) UPSERT, not duplicate INSERT — exactly one JobRun row.
        job_rows = (
            await session.execute(
                select(JobRun).where(
                    JobRun.job_name == "fx_refresh",
                    JobRun.run_date == today,
                )
            )
        ).scalars().all()
        assert len(job_rows) == 1

        # (d) Status flipped failed → ok, completed_at set, notes mention count.
        row = job_rows[0]
        assert row.status == "ok"
        assert row.completed_at is not None
        assert "fetched 4 rates" in (row.notes or "")


async def test_record_job_run_handles_existing_row_update(maker):
    """record_job_run UPDATEs an existing (job_name, run_date) row.

    Protects against a race: if a JobRun row exists already, the helper
    updates status + notes + completed_at instead of raising IntegrityError.
    """
    from app.services.job_runs import record_job_run

    run_date = date(2026, 5, 8)

    # First call inserts.
    async with maker() as session:
        await record_job_run(
            session,
            job_name="fx_refresh",
            run_date=run_date,
            status="running",
            notes="started",
        )
        await session.commit()

    # Second call (in a fresh session) updates the existing row.
    async with maker() as session:
        await record_job_run(
            session,
            job_name="fx_refresh",
            run_date=run_date,
            status="ok",
            notes="finished",
        )
        await session.commit()

    async with maker() as session:
        rows = (
            await session.execute(
                select(JobRun).where(JobRun.job_name == "fx_refresh")
            )
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].status == "ok"
        assert rows[0].notes == "finished"
        assert rows[0].completed_at is not None


