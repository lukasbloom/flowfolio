import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.db_types import DecimalText


class ApyConfig(Base):
    __tablename__ = "apy_config"
    __table_args__ = (UniqueConstraint("account_id", "instrument_id", "effective_from"),)

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    account_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("account.id"), nullable=False
    )
    instrument_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("instrument.id", ondelete="CASCADE"), nullable=False
    )
    # NUMERIC(10,6) — stores e.g. 0.023700 for 2.37% APY
    apy_rate: Mapped[Decimal] = mapped_column(DecimalText, nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    compounding: Mapped[str] = mapped_column(String, nullable=False, default="daily_simple")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
