"""Demo reset engine: atomic seed swap + admin re-claim + scheduler job handler.

Generalizes the proven Playwright golden-reset path (scripts/test_db_reset.sh)
into the in-process FastAPI world. Boot-seed and the scheduled
reset are the SAME swap mechanism: every boot and every cron
tick replaces the live DB with the pristine, secret-free synthetic seed baked
at DEMO_SEED_PATH in the Dockerfile, then re-claims the unclaimed seed so the
shared demo session stays valid.

The swap is robust by construction, no visitor write, however destructive, can
leave a half-swapped DB, which is exactly the guarantee we need.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import date
from pathlib import Path

from sqlalchemy.engine import make_url

from app.core import clock
from app.core.config import settings
from app.core.database import async_session_factory
from app.services.job_runs import record_job_run
from app.services.setup_state import pre_seed_admin_password_from_env

logger = logging.getLogger(__name__)

DEMO_RESET_JOB_NAME = "demo_reset"

# Single source of truth for the pristine seed path — the Dockerfile bakes the
# seed here and sets the same FLOWFOLIO_DEMO_SEED_PATH env, so the build and the
# runtime never disagree. Env-overridable so tests can point it at a tmp file.
DEMO_SEED_PATH = Path(os.environ.get("FLOWFOLIO_DEMO_SEED_PATH", "/app/demo-seed.sqlite"))


def _live_db_path() -> Path:
    """Resolve the live SQLite DB file from settings.database_url.

    e.g. "sqlite+aiosqlite:////data/flowfolio.db" -> "/data/flowfolio.db".
    """
    return Path(make_url(settings.database_url).database or "")


async def swap_demo_seed() -> None:
    """Atomically replace the live DB with the pristine seed.

    Copy DEMO_SEED_PATH to a temp file in the live DB's own directory, then
    `os.replace` (atomic mv) it onto the live file, and unlink the `-wal`/`-shm`
    sidecars so a stale WAL cannot shadow the swapped inode. NullPool
    (FLOWFOLIO_NULL_POOL=true in the demo) guarantees the next connection sees
    the new inode. Blocking file ops run via asyncio.to_thread so the event loop
    is never blocked.
    """
    seed = DEMO_SEED_PATH
    live = _live_db_path()

    def _swap() -> None:
        # Temp file in the SAME directory as the live DB so os.replace is a true
        # atomic rename (same filesystem), never a cross-device copy.
        tmp = live.with_name(live.name + ".reset-tmp")
        import shutil

        shutil.copyfile(seed, tmp)
        os.replace(tmp, live)
        # SQLite WAL: the seed is committed in journal=DELETE mode, so any
        # leftover WAL/SHM from the old inode would shadow the swapped file.
        for sidecar in (f"{live}-wal", f"{live}-shm"):
            try:
                os.unlink(sidecar)
            except FileNotFoundError:
                pass

    await asyncio.to_thread(_swap)


async def demo_reset_job_handler(today: date | None = None) -> dict[str, int]:
    """Swap the pristine seed in, re-claim the admin, audit the run.

    Idempotency note: this handler MUST NOT use the daily completed-run
    short-circuit that the other scheduler jobs use. A sub-daily cadence (default
    6h) fires multiple times per UTC day, and that daily guard would suppress runs
    2..N. Idempotency comes from the atomic swap being naturally repeatable;
    record_job_run UPSERTs the per-UTC-day audit row (last reset of the day wins),
    which is the intended audit semantics.

    On failure, a fresh marker session writes status="failed" and the exception
    is re-raised — the work session may be poisoned (dual-session pattern,
    mirroring backup_job_handler).
    """
    today = today or clock.today()
    try:
        await swap_demo_seed()
        async with async_session_factory() as session:
            # Re-claim the unclaimed seed so the shared demo session stays valid.
            # NullPool means this fresh session sees the swapped inode.
            await pre_seed_admin_password_from_env(session, settings.app_password)
            await record_job_run(
                session,
                job_name=DEMO_RESET_JOB_NAME,
                run_date=today,
                status="ok",
                notes="demo reset: seed swapped + admin re-claimed",
            )
            await session.commit()
        logger.info("demo_reset_complete", extra={"date": today.isoformat()})
        return {"ok": 1}
    except Exception as exc:
        async with async_session_factory() as marker_session:
            await record_job_run(
                marker_session,
                job_name=DEMO_RESET_JOB_NAME,
                run_date=today,
                status="failed",
                notes=f"{type(exc).__name__}: {exc}",
            )
            await marker_session.commit()
        logger.error("demo_reset_failed", extra={"err": str(exc)})
        raise
