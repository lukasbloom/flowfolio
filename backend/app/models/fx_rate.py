import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, Index, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.db_types import DecimalText

# Per-table source enum. Declared here; never imported from
# app.models.transaction (different value space — do NOT conflate the enums).
# FX cache rows come either from Frankfurter (ECB-sourced) or a manual override.
FX_RATE_SOURCES = ("frankfurter", "manual")


class FxRate(Base):
    __tablename__ = "fx_rate"
    __table_args__ = (
        UniqueConstraint(
            "date", "base_currency", "quote_currency", name="uq_fx_rate_date_pair"
        ),
        Index("idx_fx_rate_date", "date"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    base_currency: Mapped[str] = mapped_column(String(10), nullable=False)
    quote_currency: Mapped[str] = mapped_column(String(10), nullable=False)
    # NUMERIC(20,10), same precision as transaction.fx_rate_to_eur.
    rate: Mapped[Decimal] = mapped_column(
        DecimalText, nullable=False
    )
    source: Mapped[str] = mapped_column(String, nullable=False)  # FX_RATE_SOURCES
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
