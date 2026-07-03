import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.db_types import DecimalText

# Per-table source enum. Declared here, never imported from
# app.models.transaction (transaction has its own per-table source set with a
# different value space — do NOT conflate the two).
# Covers external pricing providers + the manual override path.
# Live sources: finnhub (stocks/etf), coingecko (crypto), ft (EU funds/etf/metal), manual.
# History/backfill sources: twelve_data + alpha_vantage (stocks), binance (crypto),
# yahoo (EU funds/etf/metal — history-only; see services/pricing/yahoo.py).
PRICE_QUOTE_SOURCES = (
    "finnhub", "alpha_vantage", "twelve_data", "coingecko", "binance",
    "ft", "yahoo", "manual",
)


class PriceQuote(Base):
    __tablename__ = "price_quote"
    __table_args__ = (
        UniqueConstraint(
            "instrument_id", "date", "source", name="uq_price_quote_inst_date_source"
        ),
        Index("idx_price_quote_instrument_date", "instrument_id", "date"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    instrument_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("instrument.id", ondelete="CASCADE"), nullable=False
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    # NUMERIC(20,8) for fiat-denominated prices; coingecko/finnhub return fiat.
    price: Mapped[Decimal] = mapped_column(
        DecimalText, nullable=False
    )
    currency: Mapped[str] = mapped_column(String(10), nullable=False)  # "EUR" or "USD"
    source: Mapped[str] = mapped_column(String, nullable=False)  # PRICE_QUOTE_SOURCES
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
