from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, field_validator

from app.schemas._serializers import DecimalModel, DecimalORMModel, DecimalStr


class ApyConfigCreate(DecimalModel):

    account_id: str
    instrument_id: str
    # Stored as a fraction: 0.0237 represents 2.37% APY (matches the
    # Numeric(10,6) backing column on the model).
    apy_rate: DecimalStr
    effective_from: date
    compounding: str = "daily_simple"

    @field_validator("apy_rate")
    @classmethod
    def apy_positive(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("apy_rate must be > 0")
        if v > 1:
            raise ValueError(
                "apy_rate is a fraction (0.0237 for 2.37%); values >1 rejected"
            )
        return v


class ApyConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="allow")

    apy_rate: Optional[DecimalStr] = None
    effective_to: Optional[date] = None

    @field_validator("apy_rate")
    @classmethod
    def apy_positive(cls, v: Optional[Decimal]) -> Optional[Decimal]:
        if v is None:
            return v
        if v <= 0:
            raise ValueError("apy_rate must be > 0")
        if v > 1:
            raise ValueError(
                "apy_rate is a fraction (0.0237 for 2.37%); values >1 rejected"
            )
        return v


class ApyConfigResponse(DecimalORMModel):

    id: str
    account_id: str
    instrument_id: str
    apy_rate: DecimalStr
    effective_from: date
    effective_to: Optional[date]
    compounding: str
    created_at: datetime
