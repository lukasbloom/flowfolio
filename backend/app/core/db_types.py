"""TEXT-backed Decimal storage for SQLite.

SQLite has no decimal type: SQLAlchemy's Numeric binds Decimal values as
floats and SQLite stores REAL — silently corrupting >15-significant-digit
money values and making SQL SUM() float arithmetic. This TypeDecorator
stores the canonical plain-format string (same form as the wire format in
app.schemas._serializers.decimal_to_str) and parses it back losslessly.

IMPORTANT: columns of this type must never appear in SQL arithmetic,
aggregates (SUM/AVG), or numeric comparisons — SQLite would coerce the text
back to float. Aggregate in Python with Decimal. Sign filters move to
Python too.
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import String, TypeDecorator


class DecimalText(TypeDecorator):
    impl = String
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if not isinstance(value, Decimal):
            # Reject floats loudly — a float here is the exact bug this
            # type exists to prevent.
            if isinstance(value, float):
                raise TypeError(f"float bound to DecimalText column: {value!r}")
            value = Decimal(value)  # int / str are exact
        return format(value, "f")

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return Decimal(value)
