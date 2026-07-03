"""APScheduler wiring for daily price refresh, FX refresh, yield accrual, and backup."""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import date, timedelta
from pathlib import Path

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import FastAPI
from sqlalchemy import func, select

from app.core import clock
from app.core.config import settings
from app.core.database import async_session_factory
from app.models.fx_rate import FxRate
from app.models.instrument import Instrument
from app.services.accrual import run_accrual_with_backfill
from app.services.demo_reset import DEMO_RESET_JOB_NAME, demo_reset_job_handler
from app.services.fx import fetch_fx_range
from app.services.job_runs import has_completed_job_run, record_job_run
from app.services.pricing.dispatcher import StaleQuoteError, fetch_price
from app.services.update_check import run_version_check

logger = logging.getLogger(__name__)

PRICE_REFRESH_HOUR_UTC = 22
PRICE_REFRESH_MINUTE_UTC = 0
ACCRUAL_HOUR_UTC = 0
ACCRUAL_MINUTE_UTC = 30
FX_REFRESH_HOUR_UTC = 17
FX_REFRESH_MINUTE_UTC = 0
BACKUP_HOUR_UTC = 2
BACKUP_MINUTE_UTC = 0
# 03:30 UTC — a slot that does not collide with price(22:00)/accrual(00:30)/
# fx(17:00)/backup(02:00). Daily cadence keeps GitHub calls off the user's
# browser and well under the unauthenticated 60/hr limit.
VERSION_CHECK_HOUR_UTC = 3
VERSION_CHECK_MINUTE_UTC = 30

PRICE_REFRESH_JOB_NAME = "price_refresh"
FX_REFRESH_JOB_NAME = "fx_refresh"
BACKUP_JOB_NAME = "backup"
VERSION_CHECK_JOB_NAME = "version_check"

# The backup shell pipeline. Defaults to the script's in-repo location (and the
# combined image's /app/scripts/backup.sh); overridable via env for tests / other
# layouts. Resolved module-relative so it works without hardcoding /app.
_DEFAULT_BACKUP_SCRIPT = str(
    Path(__file__).resolve().parents[3] / "scripts" / "backup.sh"
)
# Exit code backup.sh returns on the keyless-skip path (BACKUP_ENCRYPTION_KEY
# unset). This is the single source of truth for the skip signal: the
# script and this handler agree on the exit code, not on log wording. Mirror in
# scripts/backup.sh (BACKUP_SKIP_EXIT_CODE) and tests/test_backup_job.py.
_BACKUP_SKIP_EXIT_CODE = 75

# Bootstrap window for a brand-new DB with zero EUR/USD fx_rate rows.
# 30 calendar days is well below Frankfurter rate limits (~22 business days),
# and a fresh install will typically be seeded by backfill anyway.
FX_REFRESH_BOOTSTRAP_DAYS = 30


async def price_refresh_job_handler(
    today: date | None = None,
) -> dict[str, int]:
    """Iterate active instruments and commit each successful quote independently.

    Idempotent on a per-UTC-day basis via the job_runs UNIQUE(job_name, run_date)
    guard — mirrors fx_refresh_job_handler. A second invocation on the same UTC
    day short-circuits via has_completed_job_run; on outer-block failure a fresh
    marker session writes a status='failed' row and the exception is re-raised.
    """
    today = today or clock.today()
    summary = {"ok": 0, "stale": 0, "skipped": 0}
    async with async_session_factory() as session, httpx.AsyncClient() as client:
        if await has_completed_job_run(
            session, job_name=PRICE_REFRESH_JOB_NAME, run_date=today
        ):
            logger.info(
                "price_refresh_skipped_already_ran",
                extra={"date": today.isoformat()},
            )
            return {"skipped_already_ran": 1}
        try:
            stmt = select(Instrument).where(
                Instrument.price_source.in_(["finnhub", "coingecko", "ft"])
            )
            instruments = (await session.execute(stmt)).scalars().all()
            for inst in instruments:
                try:
                    quote = await fetch_price(session, client, inst, today=today)
                    await session.commit()
                    summary["ok"] += 1
                    logger.info(
                        "price_refresh_ok",
                        extra={
                            "symbol": inst.symbol,
                            "source": quote.source,
                            "price": str(quote.price),
                        },
                    )
                except StaleQuoteError as exc:
                    await session.rollback()
                    summary["stale"] += 1
                    logger.warning(
                        "price_refresh_stale",
                        extra={"symbol": inst.symbol, "err": str(exc)},
                    )
                except Exception as exc:
                    await session.rollback()
                    summary["skipped"] += 1
                    logger.error(
                        "price_refresh_unexpected",
                        extra={"symbol": inst.symbol, "err": str(exc)},
                    )
            await record_job_run(
                session,
                job_name=PRICE_REFRESH_JOB_NAME,
                run_date=today,
                status="ok",
                notes=(
                    f"ok={summary['ok']} stale={summary['stale']} "
                    f"skipped={summary['skipped']}"
                ),
            )
            await session.commit()
            logger.info(
                "price_refresh_complete",
                extra={**summary, "date": today.isoformat()},
            )
            return summary
        except Exception as exc:
            # Outer-block failure (anything outside the per-instrument try).
            # The work session may be poisoned — open a fresh session for the
            # failure marker, mirroring fx_refresh_job_handler.
            async with async_session_factory() as marker_session:
                await record_job_run(
                    marker_session,
                    job_name=PRICE_REFRESH_JOB_NAME,
                    run_date=today,
                    status="failed",
                    notes=f"{type(exc).__name__}: {exc}",
                )
                await marker_session.commit()
            logger.error("price_refresh_failed", extra={"err": str(exc)})
            raise


async def fx_refresh_job_handler(today: date | None = None) -> dict[str, int]:
    """Daily 17:00 UTC: gap-fill EUR/USD rates from last cached date through yesterday.

    Idempotent on a per-UTC-day basis via the job_runs UNIQUE(job_name, run_date)
    guard plus fx_rate UNIQUE(date, base, quote). Frankfurter publishes T-1, so
    the target end-of-range is yesterday. If the last cached date is null
    (brand-new DB), we bootstrap a 30-day window — well below Frankfurter
    rate limits and consistent with the dev DB's existing 64-row history.
    """
    today = today or clock.today()
    target = today - timedelta(days=1)

    async with async_session_factory() as session:
        if await has_completed_job_run(
            session, job_name=FX_REFRESH_JOB_NAME, run_date=today
        ):
            logger.info(
                "fx_refresh_skipped_already_ran",
                extra={"date": today.isoformat()},
            )
            return {"skipped": 1}

        last = (
            await session.execute(
                select(func.max(FxRate.date)).where(
                    FxRate.base_currency == "EUR",
                    FxRate.quote_currency == "USD",
                )
            )
        ).scalar()
        if last is not None:
            start = last + timedelta(days=1)
        else:
            start = target - timedelta(days=FX_REFRESH_BOOTSTRAP_DAYS)

        if start > target:
            await record_job_run(
                session,
                job_name=FX_REFRESH_JOB_NAME,
                run_date=today,
                status="ok",
                notes=f"no gap (last={last}, target={target})",
            )
            await session.commit()
            logger.info(
                "fx_refresh_no_gap",
                extra={"last": str(last), "target": str(target)},
            )
            return {"fetched": 0, "skipped_no_gap": 1}

        try:
            async with httpx.AsyncClient() as client:
                rates_raw = await fetch_fx_range(client, start, target, "EUR", "USD")
            # Frankfurter occasionally returns rates outside the requested
            # window (e.g. last business day before `start` when the window
            # opens on a bank holiday). Clamp to [start, target] defensively.
            rates = [(d, r) for d, r in rates_raw if start <= d <= target]
            # Count what upstream gave us (post-clamp) so the audit note can
            # distinguish "no gap → never called Frankfurter" from "called
            # Frankfurter but every date was already cached".
            upstream_count = len(rates)
            # Even with the clamp, a concurrent on-demand fetch via /api/fx
            # could have inserted any of these dates between MAX(date) and now.
            # Skip already-present dates so UNIQUE(date, base, quote) never
            # fires here — record_job_run shares the session with these
            # inserts, so a UNIQUE failure on fx_rate would poison the
            # job_runs flush via the same session.
            if rates:
                existing_rows = (
                    await session.execute(
                        select(FxRate.date).where(
                            FxRate.base_currency == "EUR",
                            FxRate.quote_currency == "USD",
                            FxRate.date.in_([d for d, _ in rates]),
                        )
                    )
                ).scalars().all()
                existing_set = set(existing_rows)
                rates = [(d, r) for d, r in rates if d not in existing_set]
            for day, rate in rates:
                session.add(
                    FxRate(
                        date=day,
                        base_currency="EUR",
                        quote_currency="USD",
                        rate=rate,
                        source="frankfurter",
                    )
                )
            # Commit the fx_rate inserts BEFORE writing the job_runs row.
            # If record_job_run hits its UPSERT path (re-running after a
            # prior failed attempt that left a JobRun row), its internal
            # rollback would otherwise discard the un-committed fx_rate
            # rows in the same transaction.
            await session.commit()
            # When dedup filtered every fetched date (a concurrent
            # on-demand fetch beat us to all of them) the note records the
            # upstream-vs-inserted split so the audit isn't muddied with a bare
            # "fetched 0 rates".
            if not rates and upstream_count:
                notes = (
                    f"fetched {upstream_count} from upstream, 0 new after dedup "
                    f"(all already cached) from {start} to {target}"
                )
            else:
                notes = f"fetched {len(rates)} rates from {start} to {target}"
            await record_job_run(
                session,
                job_name=FX_REFRESH_JOB_NAME,
                run_date=today,
                status="ok",
                notes=notes,
            )
            await session.commit()
            logger.info(
                "fx_refresh_ok",
                extra={
                    "fetched": len(rates),
                    "upstream": upstream_count,
                    "start": str(start),
                    "end": str(target),
                },
            )
            return {"fetched": len(rates)}
        except Exception as exc:
            await session.rollback()
            # Open a fresh session for the failure marker because the work
            # session was rolled back. Mirrors accrual.py's spirit: commit
            # the audit row even when the work transaction failed.
            async with async_session_factory() as marker_session:
                await record_job_run(
                    marker_session,
                    job_name=FX_REFRESH_JOB_NAME,
                    run_date=today,
                    status="failed",
                    notes=f"{type(exc).__name__}: {exc}",
                )
                await marker_session.commit()
            logger.error("fx_refresh_failed", extra={"err": str(exc)})
            raise


async def backup_job_handler(today: date | None = None) -> dict[str, int]:
    """Daily 02:00 UTC: shell scripts/backup.sh, record into job_runs.

    Idempotent on a per-UTC-day basis via has_completed_job_run. backup.sh is a
    synchronous shell pipeline, so it runs off the event loop via
    create_subprocess_exec. Both the real-backup path and the keyless-skip path
    exit 0 → both record a terminal status="ok" so the run is not retried later
    the same UTC day; the skip case carries a distinguishing note. A non-zero
    exit records status="failed" on a fresh marker session and re-raises,
    mirroring fx_refresh_job_handler's dual-session failure marker.
    """
    today = today or clock.today()
    script_path = os.environ.get("BACKUP_SCRIPT_PATH", _DEFAULT_BACKUP_SCRIPT)

    async with async_session_factory() as session:
        if await has_completed_job_run(
            session, job_name=BACKUP_JOB_NAME, run_date=today
        ):
            logger.info(
                "backup_skipped_already_ran",
                extra={"date": today.isoformat()},
            )
            return {"skipped_already_ran": 1}
        try:
            proc = await asyncio.create_subprocess_exec(
                "/bin/sh",
                script_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            out, _ = await proc.communicate()
            output = (out or b"").decode(errors="replace")
            # Exit-code contract: 0 = backup ok, 75 = terminal keyless
            # skip (record ok + skip note), anything else = real failure.
            skipped = proc.returncode == _BACKUP_SKIP_EXIT_CODE
            if proc.returncode != 0 and not skipped:
                raise RuntimeError(
                    f"backup.sh exit {proc.returncode}: {output[-500:]}"
                )
            notes = (
                "skipped — BACKUP_ENCRYPTION_KEY unset"
                if skipped
                else "backup ok"
            )
            await record_job_run(
                session,
                job_name=BACKUP_JOB_NAME,
                run_date=today,
                status="ok",
                notes=notes,
            )
            await session.commit()
            logger.info(
                "backup_complete",
                extra={"date": today.isoformat(), "skipped": skipped},
            )
            return {"ok": 1}
        except Exception as exc:
            # The work session may be poisoned — open a fresh session for the
            # failure marker, mirroring fx_refresh_job_handler lines 240-248.
            async with async_session_factory() as marker_session:
                await record_job_run(
                    marker_session,
                    job_name=BACKUP_JOB_NAME,
                    run_date=today,
                    status="failed",
                    notes=f"{type(exc).__name__}: {exc}",
                )
                await marker_session.commit()
            logger.error("backup_failed", extra={"err": str(exc)})
            raise


async def version_check_job_handler(today: date | None = None) -> dict[str, object]:
    """Daily 03:30 UTC: poll GitHub Releases and cache the latest release.

    Idempotent on a per-UTC-day basis via the job_runs UNIQUE(job_name, run_date)
    guard — mirrors backup_job_handler / fx_refresh_job_handler. A second
    invocation on the same UTC day short-circuits via has_completed_job_run.

    run_version_check SOFT-FAILS internally: a failed GitHub fetch records
    update_check_last_status=failed and returns rather than raising, so the
    job_runs row is still status="ok" (the job completed) and the cron does not
    hammer GitHub again the same day. The outer except here is for
    UNEXPECTED errors only (e.g. DB failure), recorded on a fresh marker session.
    """
    today = today or clock.today()
    async with async_session_factory() as session:
        if await has_completed_job_run(
            session, job_name=VERSION_CHECK_JOB_NAME, run_date=today
        ):
            logger.info(
                "version_check_skipped_already_ran",
                extra={"date": today.isoformat()},
            )
            return {"skipped_already_ran": 1}
        try:
            result = await run_version_check(session)
            await record_job_run(
                session,
                job_name=VERSION_CHECK_JOB_NAME,
                run_date=today,
                status="ok",
                notes=f"check={result['status']} latest={result['latest']}",
            )
            await session.commit()
            logger.info(
                "version_check_complete",
                extra={"date": today.isoformat(), **result},
            )
            return result
        except Exception as exc:
            # The work session may be poisoned — open a fresh session for the
            # failure marker, mirroring backup_job_handler / fx_refresh_job_handler.
            async with async_session_factory() as marker_session:
                await record_job_run(
                    marker_session,
                    job_name=VERSION_CHECK_JOB_NAME,
                    run_date=today,
                    status="failed",
                    notes=f"{type(exc).__name__}: {exc}",
                )
                await marker_session.commit()
            logger.error("version_check_failed", extra={"err": str(exc)})
            raise


async def accrual_job_handler() -> dict[str, int]:
    """Run accrual for today with 90-day backfill."""
    async with async_session_factory() as session:
        result = await run_accrual_with_backfill(
            session, today=clock.today(), backfill_days=90
        )
    total_txns = sum(result.values())
    logger.info(
        "accrual_complete",
        extra={"days_processed": len(result), "txns": total_txns},
    )
    return {"days": len(result), "txns": total_txns}


def start_scheduler(app: FastAPI) -> None:
    """Start APScheduler and attach it to app.state."""
    workers = os.environ.get("WEB_CONCURRENCY", "1")
    if workers != "1":
        raise RuntimeError(
            "APScheduler requires WEB_CONCURRENCY=1; refusing to start with "
            "WEB_CONCURRENCY=%s — cron jobs would double-fire and duplicate "
            "yield accruals/price snapshots." % workers
        )

    scheduler = AsyncIOScheduler(timezone="UTC")

    if settings.demo_mode:
        # In demo mode the reset cron is the ONLY scheduler job that runs.
        # price_refresh / fx_refresh / accrual / backup / version_check are NOT
        # registered, so no live fetch and no mutation of the coherent frozen
        # seed ever happens. accrual is SKIPPED outright (not left as a
        # frozen-clock no-op) for a clean reset-only scheduler.
        # APScheduler triggers fire on real wall-clock regardless of the frozen
        # logical clock, so the reset cadence is honest real time.
        scheduler.add_job(
            demo_reset_job_handler,
            trigger=IntervalTrigger(hours=settings.demo_reset_interval_hours),
            id=DEMO_RESET_JOB_NAME,
            replace_existing=True,
            max_instances=1,
            misfire_grace_time=3600,
        )
        scheduler.start()
        app.state.scheduler = scheduler
        logger.info(
            "scheduler_started",
            extra={
                "demo_reset": f"every {settings.demo_reset_interval_hours}h",
            },
        )
        return

    scheduler.add_job(
        price_refresh_job_handler,
        trigger=CronTrigger(
            hour=PRICE_REFRESH_HOUR_UTC,
            minute=PRICE_REFRESH_MINUTE_UTC,
            timezone="UTC",
        ),
        id="price_refresh",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        accrual_job_handler,
        trigger=CronTrigger(
            hour=ACCRUAL_HOUR_UTC,
            minute=ACCRUAL_MINUTE_UTC,
            timezone="UTC",
        ),
        id="accrual",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        fx_refresh_job_handler,
        trigger=CronTrigger(
            hour=FX_REFRESH_HOUR_UTC,
            minute=FX_REFRESH_MINUTE_UTC,
            timezone="UTC",
        ),
        id="fx_refresh",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        backup_job_handler,
        trigger=CronTrigger(
            hour=BACKUP_HOUR_UTC,
            minute=BACKUP_MINUTE_UTC,
            timezone="UTC",
        ),
        id="backup",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        version_check_job_handler,
        trigger=CronTrigger(
            hour=VERSION_CHECK_HOUR_UTC,
            minute=VERSION_CHECK_MINUTE_UTC,
            timezone="UTC",
        ),
        id="version_check",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=3600,
    )
    scheduler.start()
    app.state.scheduler = scheduler
    logger.info(
        "scheduler_started",
        extra={
            "price_refresh": "22:00 UTC",
            "accrual": "00:30 UTC",
            "fx_refresh": "17:00 UTC",
            "backup": "02:00 UTC",
            "version_check": "03:30 UTC",
        },
    )


def shutdown_scheduler(app: FastAPI) -> None:
    scheduler: AsyncIOScheduler | None = getattr(app.state, "scheduler", None)
    if scheduler is not None:
        scheduler.shutdown(wait=False)
        logger.info("scheduler_shutdown")
