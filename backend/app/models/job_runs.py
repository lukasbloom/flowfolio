import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

# Job run lifecycle states. Idempotency is enforced by the
# UniqueConstraint(job_name, run_date) — duplicate writes raise IntegrityError.
JOB_RUN_STATUSES = ("running", "ok", "failed")


class JobRun(Base):
    __tablename__ = "job_runs"
    __table_args__ = (
        UniqueConstraint("job_name", "run_date", name="uq_job_runs_name_date"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    # e.g. "accrual", "price_refresh", "fx_refresh"
    job_name: Mapped[str] = mapped_column(String, nullable=False)
    run_date: Mapped[date] = mapped_column(Date, nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="running")
    notes: Mapped[str | None] = mapped_column(String, nullable=True)
