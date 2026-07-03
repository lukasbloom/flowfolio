import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.db_types import DecimalText


class LotAlloc(Base):
    __tablename__ = "lot_alloc"
    __table_args__ = (
        Index("idx_lot_alloc_sell", "sell_txn_id"),
        Index("idx_lot_alloc_buy", "buy_txn_id"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    # ON DELETE CASCADE — when the sell transaction is deleted, its lot_alloc rows vanish
    sell_txn_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("transaction.id", ondelete="CASCADE"),
        nullable=False,
    )
    buy_txn_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("transaction.id"),
        nullable=False,
    )
    # NUMERIC(36,18) to match transaction.quantity precision
    quantity: Mapped[Decimal] = mapped_column(DecimalText, nullable=False)
    # realized_gain_eur = (sell_price/sell_fx - buy_price/buy_fx) * quantity
    realized_gain_eur: Mapped[Decimal | None] = mapped_column(
        DecimalText, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
