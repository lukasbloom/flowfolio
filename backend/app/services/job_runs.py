"""Race-safe job_runs writer reused by APScheduler handlers.

Mirrors the accrual.py pattern (lines 126-138, 222-246): insert with status,
flush; if IntegrityError fires (UNIQUE on job_name + run_date), another worker
or a prior call owns the row — UPDATE it instead of letting the exception
escape. Caller commits.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.models.job_runs import JobRun


async def record_job_run(
    session: AsyncSession,
    *,
    job_name: str,
    run_date: date,
    status: str,
    notes: str | None = None,
) -> JobRun:
    """Insert a JobRun row OR UPDATE the existing (job_name, run_date) row.

    Status is one of "running", "ok", or "failed". For "ok" / "failed" we
    set ``completed_at = utcnow()`` so the audit trail captures completion;
    for "running" we leave ``completed_at`` null. Notes are truncated to
    500 chars to mirror accrual.py's existing convention.

    Caller commits. The race-safe path: insert + flush, on IntegrityError
    rollback and SELECT-then-UPDATE.
    """
    truncated = (notes or "")[:500] if notes is not None else None
    completed_at = clock.now() if status in ("ok", "failed") else None

    row = JobRun(
        job_name=job_name,
        run_date=run_date,
        status=status,
        completed_at=completed_at,
        notes=truncated,
    )
    session.add(row)
    try:
        await session.flush()
        return row
    except IntegrityError:
        await session.rollback()
        existing = (
            await session.execute(
                select(JobRun).where(
                    JobRun.job_name == job_name,
                    JobRun.run_date == run_date,
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            # IntegrityError wasn't on JobRun's UNIQUE — some other pending
            # state in this session collided. Re-raise so the caller sees the
            # real cause instead of swallowing it as a NoResultFound.
            raise
        existing.status = status
        existing.notes = truncated
        if status in ("ok", "failed"):
            existing.completed_at = clock.now()
        await session.flush()
        return existing


async def has_completed_job_run(
    session: AsyncSession, *, job_name: str, run_date: date
) -> bool:
    """True if a (job_name, run_date) row already exists with status='ok'.

    Used by daily handlers as their idempotency guard before doing any
    HTTP work or DB writes; lets a manually re-triggered run no-op cleanly
    instead of duplicating fx_rate / price_quote rows.
    """
    existing = (
        await session.execute(
            select(JobRun).where(
                JobRun.job_name == job_name,
                JobRun.run_date == run_date,
                JobRun.status == "ok",
            )
        )
    ).scalar_one_or_none()
    return existing is not None
