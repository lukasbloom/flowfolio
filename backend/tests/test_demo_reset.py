"""Demo reset engine (atomic swap + re-claim + audit).

Builds a tmp pristine seed + a tmp live DB, points DEMO_SEED_PATH / _live_db_path
at them, and exercises swap_demo_seed + demo_reset_job_handler without touching
the real /data volume. The live maker uses NullPool so a post-swap session sees
the new inode — the same flag the demo runs with (FLOWFOLIO_NULL_POOL=true).
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.database import Base, attach_sqlite_pragmas
from app.models.job_runs import JobRun
from app.models.user_setting import UserSetting
from app.services import demo_reset as demo_reset_mod
from app.services.setup_state import is_setup_complete


async def _build_db(path: Path, *, settings_rows: dict[str, str]) -> None:
    """Create a self-contained sqlite file with the full schema + given rows.

    No WAL pragma is attached so the file is written in journal=DELETE mode —
    all data lands in the main file with no -wal residue, exactly like the baked
    golden seed (which the real test_db_reset.sh relies on).
    """
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        for key, value in settings_rows.items():
            session.add(UserSetting(key=key, value=value))
        await session.commit()
    await engine.dispose()


def _live_maker(live_path: Path) -> async_sessionmaker[AsyncSession]:
    """A NullPool session factory bound to the live path (sees post-swap inode)."""
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{live_path}", poolclass=NullPool
    )
    attach_sqlite_pragmas(engine)
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture
async def paths(tmp_path: Path):
    """An unclaimed pristine seed + a claimed live DB with stale WAL/SHM."""
    seed = tmp_path / "demo-seed.sqlite"
    live = tmp_path / "flowfolio.db"
    # Seed: UNCLAIMED (no setup_complete) + a marker so we can prove the swap.
    await _build_db(seed, settings_rows={"demo_marker": "seed"})
    # Live: a different marker + a stale setup_complete to prove it gets replaced.
    await _build_db(
        live, settings_rows={"demo_marker": "live-old", "setup_complete": "true"}
    )
    # Stale WAL/SHM sidecars that must be unlinked by the swap.
    (tmp_path / "flowfolio.db-wal").write_bytes(b"stale-wal")
    (tmp_path / "flowfolio.db-shm").write_bytes(b"stale-shm")
    return {"seed": seed, "live": live, "dir": tmp_path}


def _wire(monkeypatch, paths) -> async_sessionmaker[AsyncSession]:
    """Point the module at the tmp seed/live + a NullPool live maker + a pw."""
    monkeypatch.setattr(demo_reset_mod, "DEMO_SEED_PATH", paths["seed"])
    monkeypatch.setattr(demo_reset_mod, "_live_db_path", lambda: paths["live"])
    maker = _live_maker(paths["live"])
    monkeypatch.setattr(demo_reset_mod, "async_session_factory", maker)
    # The demo runs with a throwaway APP_PASSWORD so the re-claim has something
    # to materialize; without it pre_seed_admin_password_from_env no-ops.
    monkeypatch.setattr(demo_reset_mod.settings, "app_password", "demo-throwaway-pw")
    return maker


# --------------------------------------------------------------------------
# (a) swap replaces the live contents + removes WAL/SHM
# --------------------------------------------------------------------------


async def test_swap_replaces_live_and_removes_sidecars(paths, monkeypatch):
    monkeypatch.setattr(demo_reset_mod, "DEMO_SEED_PATH", paths["seed"])
    monkeypatch.setattr(demo_reset_mod, "_live_db_path", lambda: paths["live"])

    await demo_reset_mod.swap_demo_seed()

    # The live DB now carries the SEED's marker, not the old live value.
    maker = _live_maker(paths["live"])
    async with maker() as session:
        marker = (
            await session.execute(
                select(UserSetting.value).where(UserSetting.key == "demo_marker")
            )
        ).scalar_one()
        assert marker == "seed"
        # The seed was unclaimed, so the stale setup_complete is gone too.
        assert await is_setup_complete(session) is False

    # Stale sidecars were unlinked; no temp file left behind.
    assert not (paths["dir"] / "flowfolio.db-wal").exists()
    assert not (paths["dir"] / "flowfolio.db-shm").exists()
    assert not (paths["dir"] / "flowfolio.db.reset-tmp").exists()


# --------------------------------------------------------------------------
# (b)+(c) handler re-claims the instance and records an ok audit row
# --------------------------------------------------------------------------


async def test_handler_reclaims_and_records_ok(paths, monkeypatch):
    maker = _wire(monkeypatch, paths)
    today = date(2026, 4, 30)

    result = await demo_reset_mod.demo_reset_job_handler(today=today)
    assert result == {"ok": 1}

    async with maker() as session:
        # The unclaimed seed was re-claimed → the shared demo session validates.
        assert await is_setup_complete(session) is True
        rows = (
            await session.execute(
                select(JobRun).where(JobRun.job_name == "demo_reset")
            )
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].status == "ok"
        assert rows[0].run_date == today


# --------------------------------------------------------------------------
# (d) twice in the same UTC day: no raise, single per-day audit row (UPSERT)
# --------------------------------------------------------------------------


async def test_handler_twice_same_day_single_audit_row(paths, monkeypatch):
    maker = _wire(monkeypatch, paths)
    today = date(2026, 4, 30)

    first = await demo_reset_mod.demo_reset_job_handler(today=today)
    second = await demo_reset_mod.demo_reset_job_handler(today=today)
    assert first == {"ok": 1}
    assert second == {"ok": 1}  # naturally repeatable, never short-circuits

    async with maker() as session:
        rows = (
            await session.execute(
                select(JobRun).where(JobRun.job_name == "demo_reset")
            )
        ).scalars().all()
        # record_job_run UPSERTs on UNIQUE(job_name, run_date): one row per day.
        assert len(rows) == 1
        assert rows[0].status == "ok"


# --------------------------------------------------------------------------
# Prohibition guard: the handler must NOT use the daily short-circuit.
# --------------------------------------------------------------------------


def test_handler_does_not_use_daily_short_circuit():
    src = Path(demo_reset_mod.__file__).read_text()
    assert "has_completed_job_run" not in src
