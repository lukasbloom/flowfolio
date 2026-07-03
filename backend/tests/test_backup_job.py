"""In-process backup job (scheduler shells backup.sh).

Stubs asyncio.create_subprocess_exec so the idempotency / job_runs recording
logic is exercised without invoking a real backup. Mirrors the in-memory
`maker` fixture from test_scheduler.py.
"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base, attach_sqlite_pragmas
from app.models.job_runs import JobRun
from app.services import scheduler as scheduler_mod


@pytest_asyncio.fixture
async def maker():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    attach_sqlite_pragmas(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    yield session_factory
    await engine.dispose()


def _app():
    return SimpleNamespace(state=SimpleNamespace())


def _fake_subprocess(*, returncode: int, output: bytes):
    """Return a monkeypatch target for asyncio.create_subprocess_exec.

    The fake records each invocation and yields a proc whose communicate()
    returns (output, None) and whose returncode matches `returncode`.
    """
    calls: list[tuple] = []

    class _FakeProc:
        def __init__(self) -> None:
            self.returncode = returncode

        async def communicate(self):
            return output, None

    async def fake_exec(*args, **kwargs):
        calls.append(args)
        return _FakeProc()

    return fake_exec, calls


# --------------------------------------------------------------------------
# Registration
# --------------------------------------------------------------------------


async def test_start_scheduler_registers_backup_job():
    app = _app()
    scheduler_mod.start_scheduler(app)
    try:
        ids = {job.id for job in app.state.scheduler.get_jobs()}
        assert "backup" in ids
        assert ids == {
            "price_refresh",
            "accrual",
            "fx_refresh",
            "backup",
            "version_check",
        }
    finally:
        scheduler_mod.shutdown_scheduler(app)


async def test_backup_cron_at_02_utc():
    app = _app()
    scheduler_mod.start_scheduler(app)
    try:
        jobs = {job.id: job for job in app.state.scheduler.get_jobs()}
        assert str(jobs["backup"].trigger) == "cron[hour='2', minute='0']"
    finally:
        scheduler_mod.shutdown_scheduler(app)


def test_web_concurrency_guard_still_present(monkeypatch):
    """The single-image WEB_CONCURRENCY=1 invariant is preserved (RESEARCH anti-pattern)."""
    app = _app()
    monkeypatch.setenv("WEB_CONCURRENCY", "3")
    with pytest.raises(RuntimeError, match="WEB_CONCURRENCY=1"):
        scheduler_mod.start_scheduler(app)


# --------------------------------------------------------------------------
# Handler behavior
# --------------------------------------------------------------------------


async def test_backup_handler_records_ok_on_success(maker, monkeypatch):
    today = date(2026, 6, 25)
    fake_exec, calls = _fake_subprocess(returncode=0, output=b"INFO: Backup process finished successfully.\n")

    monkeypatch.setattr(scheduler_mod, "async_session_factory", maker)
    monkeypatch.setattr(scheduler_mod.asyncio, "create_subprocess_exec", fake_exec)

    result = await scheduler_mod.backup_job_handler(today=today)

    assert result == {"ok": 1}
    assert len(calls) == 1  # subprocess actually invoked

    async with maker() as session:
        rows = (
            await session.execute(
                select(JobRun).where(JobRun.job_name == "backup")
            )
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].run_date == today
        assert rows[0].status == "ok"
        assert "skipped" not in (rows[0].notes or "")


async def test_backup_handler_idempotent_same_day(maker, monkeypatch):
    today = date(2026, 6, 25)

    async with maker() as session:
        session.add(
            JobRun(job_name="backup", run_date=today, status="ok", notes="prior")
        )
        await session.commit()

    fake_exec, calls = _fake_subprocess(returncode=0, output=b"ok")
    monkeypatch.setattr(scheduler_mod, "async_session_factory", maker)
    monkeypatch.setattr(scheduler_mod.asyncio, "create_subprocess_exec", fake_exec)

    result = await scheduler_mod.backup_job_handler(today=today)

    assert result == {"skipped_already_ran": 1}
    assert calls == []  # short-circuit: subprocess never invoked

    async with maker() as session:
        rows = (
            await session.execute(
                select(JobRun).where(JobRun.job_name == "backup")
            )
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].notes == "prior"


async def test_backup_handler_marks_failed_on_nonzero_exit(maker, monkeypatch):
    today = date(2026, 6, 25)
    fake_exec, _calls = _fake_subprocess(
        returncode=2, output=b"ERROR: Database file not found at /data/flowfolio.db\n"
    )
    monkeypatch.setattr(scheduler_mod, "async_session_factory", maker)
    monkeypatch.setattr(scheduler_mod.asyncio, "create_subprocess_exec", fake_exec)

    with pytest.raises(RuntimeError, match="backup.sh"):
        await scheduler_mod.backup_job_handler(today=today)

    async with maker() as session:
        rows = (
            await session.execute(
                select(JobRun).where(JobRun.job_name == "backup")
            )
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].status == "failed"
        assert "Database file not found" in (rows[0].notes or "")


async def test_backup_handler_records_skip_as_ok_and_is_terminal(maker, monkeypatch):
    """Key-unset skip (exit 75, the BACKUP_SKIP_EXIT_CODE contract) records
    status=ok with a skip note, and is terminal — a same-day re-run
    short-circuits. The handler keys on the exit code, not the log wording."""
    today = date(2026, 6, 25)
    # The warning text is human-only; the scheduler must NOT parse it. Use the
    # exact em-dash wording backup.sh emits to prove the handler ignores it.
    skip_output = (
        b"WARNING: BACKUP_ENCRYPTION_KEY not set \xe2\x80\x94 backups disabled "
        b"(refusing to write unencrypted financial data).\n"
    )
    fake_exec, calls = _fake_subprocess(
        returncode=scheduler_mod._BACKUP_SKIP_EXIT_CODE, output=skip_output
    )
    monkeypatch.setattr(scheduler_mod, "async_session_factory", maker)
    monkeypatch.setattr(scheduler_mod.asyncio, "create_subprocess_exec", fake_exec)

    result = await scheduler_mod.backup_job_handler(today=today)
    assert result == {"ok": 1}

    async with maker() as session:
        rows = (
            await session.execute(
                select(JobRun).where(JobRun.job_name == "backup")
            )
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].status == "ok"
        assert rows[0].notes == "skipped — BACKUP_ENCRYPTION_KEY unset"

    # Terminal: a same-day second call short-circuits via has_completed_job_run.
    second = await scheduler_mod.backup_job_handler(today=today)
    assert second == {"skipped_already_ran": 1}
    assert len(calls) == 1  # subprocess only ran the first time
