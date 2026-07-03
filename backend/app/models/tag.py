import uuid

from sqlalchemy import ForeignKey, PrimaryKeyConstraint, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Tag(Base):
    __tablename__ = "tag"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    name: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    color: Mapped[str | None] = mapped_column(String(20), nullable=True)


class HoldingTag(Base):
    __tablename__ = "holding_tag"
    __table_args__ = (
        PrimaryKeyConstraint("account_id", "instrument_id", "tag_id"),
    )

    account_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("account.id"), nullable=False
    )
    instrument_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("instrument.id", ondelete="CASCADE"), nullable=False
    )
    tag_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tag.id"), nullable=False
    )
