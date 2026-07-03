import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, JSON, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

AUDIT_EVENT_TYPES = ("edit", "delete")


class TxnAudit(Base):
    __tablename__ = "txn_audit"
    __table_args__ = (
        CheckConstraint("event_type IN ('edit', 'delete')", name="ck_txn_audit_event_type"),
        Index("idx_txn_audit_transaction", "transaction_id"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    transaction_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("transaction.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    changed_fields: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
