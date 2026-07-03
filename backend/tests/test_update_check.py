"""GitHub Releases fetch + cache + the daily cron.

Stubs httpx.AsyncClient so the cache-write logic, the github.com URL validation,
and the soft-fail status recording are exercised without a real GitHub call.
Mirrors the in-memory `maker` fixture from test_backup_job.py.
"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base, attach_sqlite_pragmas
from app.services import scheduler as scheduler_mod
from app.services import update_check as update_check_mod
from app.services.job_runs import has_completed_job_run
from app.services.update_store import get_cached_release


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


class _FakeResponse:
    def __init__(self, payload: dict, *, raise_exc: Exception | None = None):
        self._payload = payload
        self._raise_exc = raise_exc

    def raise_for_status(self) -> None:
        if self._raise_exc is not None:
            raise self._raise_exc

    def json(self) -> dict:
        return self._payload


def _fake_client_factory(payload: dict, *, get_raises: Exception | None = None):
    """Return a monkeypatch target replacing update_check.httpx.AsyncClient.

    The fake is an async context manager whose get() returns a _FakeResponse,
    or raises get_raises to simulate a network failure.
    """

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, **kwargs):
            if get_raises is not None:
                raise get_raises
            return _FakeResponse(payload)

    def factory(*args, **kwargs):
        return _FakeClient()

    return factory


_VALID_RELEASE = {
    "tag_name": "v1.3.0",
    "html_url": "https://github.com/lukasbloom/flowfolio/releases/tag/v1.3.0",
    "published_at": "2026-06-25T10:00:00Z",
}


# --------------------------------------------------------------------------
# Fetch + cache
# --------------------------------------------------------------------------


async def test_run_version_check_caches_release(maker, monkeypatch):
    monkeypatch.setattr(
        update_check_mod.httpx, "AsyncClient", _fake_client_factory(_VALID_RELEASE)
    )
    async with maker() as session:
        result = await update_check_mod.run_version_check(session)
        await session.commit()
        assert result["status"] == "ok"
        cached = await get_cached_release(session)
    assert cached.latest_version == "v1.3.0"
    assert cached.notes_url == _VALID_RELEASE["html_url"]
    assert cached.published_at == "2026-06-25T10:00:00Z"
    assert cached.last_status == "ok"
    assert cached.last_checked is not None


async def test_non_github_notes_url_rejected(maker, monkeypatch):
    evil = {
        "tag_name": "v1.3.0",
        "html_url": "https://evil.example.com/phish",
        "published_at": "2026-06-25T10:00:00Z",
    }
    monkeypatch.setattr(
        update_check_mod.httpx, "AsyncClient", _fake_client_factory(evil)
    )
    async with maker() as session:
        await update_check_mod.run_version_check(session)
        await session.commit()
        cached = await get_cached_release(session)
    assert cached.latest_version == "v1.3.0"
    assert cached.notes_url is None  # non-github.com url dropped


async def test_failed_fetch_records_failed_status(maker, monkeypatch):
    monkeypatch.setattr(
        update_check_mod.httpx,
        "AsyncClient",
        _fake_client_factory({}, get_raises=RuntimeError("boom")),
    )
    async with maker() as session:
        result = await update_check_mod.run_version_check(session)
        await session.commit()
        assert result["status"] == "failed"
        cached = await get_cached_release(session)
    assert cached.last_status == "failed"
    assert cached.last_checked is not None
    # Soft fail: no prior cache existed, none was fabricated.
    assert cached.latest_version is None


async def test_setting_keys_allowlist_unchanged():
    from app.services.settings import SETTING_KEYS_ALLOWLIST

    assert SETTING_KEYS_ALLOWLIST == ("concentration_threshold",)


# --------------------------------------------------------------------------
# The daily version-check cron
# --------------------------------------------------------------------------


def _app():
    return SimpleNamespace(state=SimpleNamespace())


async def test_version_check_handler_records_job_run(maker, monkeypatch):
    monkeypatch.setattr(scheduler_mod, "async_session_factory", maker)
    monkeypatch.setattr(
        update_check_mod.httpx, "AsyncClient", _fake_client_factory(_VALID_RELEASE)
    )
    today = date(2026, 6, 26)
    await scheduler_mod.version_check_job_handler(today=today)
    async with maker() as session:
        assert await has_completed_job_run(
            session, job_name=scheduler_mod.VERSION_CHECK_JOB_NAME, run_date=today
        )
        cached = await get_cached_release(session)
    assert cached.latest_version == "v1.3.0"


async def test_version_check_handler_short_circuits_same_day(maker, monkeypatch):
    monkeypatch.setattr(scheduler_mod, "async_session_factory", maker)
    calls = {"n": 0}

    def factory(*args, **kwargs):
        calls["n"] += 1
        return _fake_client_factory(_VALID_RELEASE)()

    monkeypatch.setattr(update_check_mod.httpx, "AsyncClient", factory)
    today = date(2026, 6, 26)
    first = await scheduler_mod.version_check_job_handler(today=today)
    second = await scheduler_mod.version_check_job_handler(today=today)
    assert calls["n"] == 1  # second run short-circuited before any fetch
    assert second == {"skipped_already_ran": 1}
    assert first.get("status") == "ok"


async def test_version_check_job_registered():
    app = _app()
    scheduler_mod.start_scheduler(app)
    try:
        ids = {job.id for job in app.state.scheduler.get_jobs()}
        assert "version_check" in ids
        assert ids == {
            "price_refresh",
            "accrual",
            "fx_refresh",
            "backup",
            "version_check",
        }
    finally:
        scheduler_mod.shutdown_scheduler(app)
