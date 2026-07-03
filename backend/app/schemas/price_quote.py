from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from pydantic import field_validator

from app.core.constants import VALID_CURRENCIES as _VALID_CURRENCIES_FROZEN
from app.schemas._serializers import DecimalModel, DecimalORMModel, DecimalStr

# Mirrors PRICE_QUOTE_SOURCES. Kept as a per-module set instead of an
# import from app.models.price_quote so a Pydantic-only client (e.g. an OpenAPI
# generator) does not need the SQLAlchemy stack.
VALID_SOURCES = {"finnhub", "alpha_vantage", "coingecko", "ft", "manual"}
# Only EUR/USD accepted in V1. Derived as a plain `set(...)` from the
# centralized frozenset so the validator messages keep rendering `{...}`.
VALID_CURRENCIES = set(_VALID_CURRENCIES_FROZEN)


class PriceQuoteCreate(DecimalModel):

    instrument_id: str
    date: date
    price: DecimalStr
    currency: str
    source: str

    @field_validator("source")
    @classmethod
    def validate_source(cls, v: str) -> str:
        if v not in VALID_SOURCES:
            raise ValueError(f"source must be one of {VALID_SOURCES}, got {v!r}")
        return v

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, v: str) -> str:
        if v not in VALID_CURRENCIES:
            raise ValueError(f"currency must be one of {VALID_CURRENCIES}, got {v!r}")
        return v

    @field_validator("price")
    @classmethod
    def price_positive(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("price must be > 0")
        return v


class PriceQuoteResponse(DecimalORMModel):

    id: str
    instrument_id: str
    date: date
    price: DecimalStr
    currency: str
    source: str
    fetched_at: datetime


class ManualNavOverride(DecimalModel):
    """Body for POST /api/prices/manual (instrument detail page).

    Manual NAV overrides are written with source="manual" and win over API
    sources for the same (instrument_id, date).
    """


    instrument_id: str
    date: date
    price: DecimalStr
    currency: str = "EUR"
    note: Optional[str] = None

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, v: str) -> str:
        if v not in VALID_CURRENCIES:
            raise ValueError(f"currency must be one of {VALID_CURRENCIES}, got {v!r}")
        return v

    @field_validator("price")
    @classmethod
    def price_positive(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("price must be > 0")
        return v
