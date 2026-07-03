from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


class AccountCreate(BaseModel):
    name: str
    account_type: str
    is_banked: bool = True
    currency: str = "EUR"


class AccountResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    account_type: str
    is_banked: bool
    currency: str
    created_at: datetime
    last_reconciled_date: date | None = None
