import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.enums import INSTRUMENT_TYPES, PRICE_SOURCES, RISK_LEVELS  # noqa: F401  (re-exported)


class Instrument(Base):
    __tablename__ = "instrument"
    __table_args__ = (UniqueConstraint("symbol", "instrument_type"),)

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    instrument_type: Mapped[str] = mapped_column(String, nullable=False)
    risk_level: Mapped[str] = mapped_column(String, nullable=False, default="Medium")
    base_currency: Mapped[str] = mapped_column(String(10), nullable=False)
    price_source: Mapped[str] = mapped_column(String, nullable=False, default="na")
    ticker_override: Mapped[str | None] = mapped_column(String, nullable=True)
    # Per-instrument override for quantity-decimal rendering.
    # NULL → use the per-type default defined in frontend/lib/format.ts.
    # Bounds (0..12) are enforced at the schema level, not in SQLite.
    display_decimals: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
