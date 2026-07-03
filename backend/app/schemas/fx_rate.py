from datetime import date, datetime
from decimal import Decimal

from pydantic import field_validator

from app.core.constants import VALID_CURRENCIES as _VALID_CURRENCIES_FROZEN
from app.schemas._serializers import DecimalModel, DecimalORMModel, FxRateStr

VALID_FX_SOURCES = {"frankfurter", "manual"}
# Derived as a plain `set(...)` from the centralized frozenset so the
# validator + fx.py error messages keep rendering `{...}` (fx.py imports this).
VALID_CURRENCIES = set(_VALID_CURRENCIES_FROZEN)


class FxRateCreate(DecimalModel):

    date: date
    base_currency: str
    quote_currency: str
    rate: FxRateStr
    source: str = "frankfurter"

    @field_validator("base_currency", "quote_currency")
    @classmethod
    def validate_currency(cls, v: str) -> str:
        if v not in VALID_CURRENCIES:
            raise ValueError(f"currency must be one of {VALID_CURRENCIES}, got {v!r}")
        return v

    @field_validator("source")
    @classmethod
    def validate_source(cls, v: str) -> str:
        if v not in VALID_FX_SOURCES:
            raise ValueError(f"source must be one of {VALID_FX_SOURCES}, got {v!r}")
        return v

    @field_validator("rate")
    @classmethod
    def rate_positive(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("rate must be > 0")
        return v


class FxRateResponse(DecimalORMModel):

    id: str
    date: date
    base_currency: str
    quote_currency: str
    rate: FxRateStr
    source: str
    fetched_at: datetime
