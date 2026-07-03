from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from app.core.enums import INSTRUMENT_TYPE_SET, PRICE_SOURCE_SET, RISK_LEVEL_SET
from app.services.instrument_pricing import allowed_sources_for

# Canonical membership lives in app.core.enums (dependency-free, shared with the
# ORM models). Derived as plain `set(...)` here — NOT used directly as the
# frozensets — so the validator error messages (f"... must be one of {VALID_*}")
# keep rendering `{...}` rather than `frozenset({...})`, preserving the exact
# error strings.
VALID_TYPES = set(INSTRUMENT_TYPE_SET)
VALID_SOURCES = set(PRICE_SOURCE_SET)
VALID_RISK_LEVELS = set(RISK_LEVEL_SET)

# Instrument identifiers that would shadow the Holdings sub-tab routes
# (`/holdings/active`, `/holdings/closed`, `/holdings/catalog`,
# `/holdings/i/[id]`). Validator on InstrumentCreate
# rejects POST/PUT requests setting either `id` or `symbol` to a reserved
# token; Alembic 0006 scans existing rows. This module is the single source
# of truth. The migration imports the constant from here.
RESERVED_INSTRUMENT_IDS: frozenset[str] = frozenset({"active", "closed", "catalog", "i"})


class InstrumentCreate(BaseModel):
    id: Optional[str] = None
    symbol: str
    name: str
    instrument_type: str
    base_currency: str
    price_source: str = "na"
    # Risk classification (High / Medium / Low / Liquid).
    # Optional on the wire — defaults to "Medium" to match the ORM column
    # default so existing POST callers that don't send the field continue
    # to work unchanged.
    risk_level: str = "Medium"
    ticker_override: Optional[str] = None
    # Optional per-instrument override (0..12). None means
    # "inherit the per-type default in frontend/lib/format.ts".
    display_decimals: Optional[int] = None

    @field_validator("display_decimals")
    @classmethod
    def validate_display_decimals(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and not (0 <= v <= 12):
            raise ValueError("display_decimals must be between 0 and 12")
        return v

    @field_validator("instrument_type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        if v not in VALID_TYPES:
            raise ValueError(f"instrument_type must be one of {VALID_TYPES}")
        return v

    @field_validator("price_source")
    @classmethod
    def validate_source(cls, v: str) -> str:
        if v not in VALID_SOURCES:
            raise ValueError(f"price_source must be one of {VALID_SOURCES}")
        return v

    @field_validator("risk_level")
    @classmethod
    def validate_risk_level(cls, v: str) -> str:
        if v not in VALID_RISK_LEVELS:
            raise ValueError(f"risk_level must be one of {VALID_RISK_LEVELS}")
        return v

    @field_validator("symbol")
    @classmethod
    def validate_symbol_not_reserved(cls, v: str) -> str:
        if v in RESERVED_INSTRUMENT_IDS:
            raise ValueError(
                f"symbol {v!r} is reserved by the Holdings sub-tab route layout; "
                f"reserved set: {sorted(RESERVED_INSTRUMENT_IDS)}"
            )
        return v

    @field_validator("id")
    @classmethod
    def validate_id_not_reserved(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v in RESERVED_INSTRUMENT_IDS:
            raise ValueError(
                f"id {v!r} is reserved by the Holdings sub-tab route layout; "
                f"reserved set: {sorted(RESERVED_INSTRUMENT_IDS)}"
            )
        return v

    @model_validator(mode="after")
    def _validate_type_source_combo(self) -> "InstrumentCreate":
        """Cross-field guard: reject (type, price_source) pairs outside the
        canonical mapping in `app.services.instrument_pricing`. Closes the
        gap where the per-field validators accept any valid token but allow
        e.g. (stock, coingecko) which silently breaks the daily refresh.
        """
        allowed = allowed_sources_for(self.instrument_type)
        if self.price_source not in allowed:
            raise ValueError(
                f"price_source={self.price_source!r} is not allowed for "
                f"instrument_type={self.instrument_type!r}; "
                f"allowed: {sorted(allowed)}"
            )
        return self


class InstrumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    symbol: str
    name: str
    instrument_type: str
    base_currency: str
    price_source: str
    risk_level: str
    ticker_override: Optional[str]
    display_decimals: Optional[int] = None
    created_at: datetime
