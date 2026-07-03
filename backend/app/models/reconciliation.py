"""Reconciliation event model.

One row per user-initiated reconciliation event. Carries the holdings
snapshot the user typed (JSON) and is referenced by transaction.reconciliation_id
on every adjustment txn (accept/dismiss) and every real txn produced from
the Reject drawer.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, Date, DateTime, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Reconciliation(Base):
    __tablename__ = "reconciliation"
    __table_args__ = (
        Index("idx_reconciliation_account_date", "account_id", "snapshot_date"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    account_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("account.id"), nullable=False
    )
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    notes: Mapped[str | None] = mapped_column(String, nullable=True)
    holdings_snapshot: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False
    )
